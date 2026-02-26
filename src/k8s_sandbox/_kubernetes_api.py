from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import TypedDict, cast

from kubernetes import client, config
from kubernetes.config import (
    ConfigException,
)

logger = logging.getLogger(__name__)

_thread_local = threading.local()

_INCLUSTER_NAMESPACE_PATH = "/var/run/secrets/kubernetes.io/serviceaccount/namespace"


class _KubeContext(TypedDict):
    name: str
    context: dict[str, str]


def k8s_client(context_name: str | None) -> client.CoreV1Api:
    """
    Get a thread-local Kubernetes client for interacting with the specified context.

    The context name must refer to an existing context within the kubeconfig file. If
    context is None, the current context is used. When running in-cluster, the default
    service account credentials are used.

    This function is thread-safe and ensures that the Kubernetes configuration is
    loaded.

    A Kubernetes client cannot be used simultaneously from multiple threads (which are
    used because the kubernetes client is not async).
    """
    _Config.ensure_loaded()
    if not hasattr(_thread_local, "client_factory"):
        _thread_local.client_factory = _ThreadLocalClientFactory()
    return _thread_local.client_factory.get_client(context_name)


def get_default_namespace(context_name: str | None) -> str:
    """
    Get the default namespace for the specified kubeconfig context name.

    If context_name is None, the current context is used. When running in-cluster,
    the namespace is read from the service account token mount.

    If the namespace is not specified, "default" is returned.
    """
    instance = _Config.get_instance()
    if instance.in_cluster:
        try:
            return Path(_INCLUSTER_NAMESPACE_PATH).read_text().strip()
        except OSError:
            return "default"
    context = instance.get_context(context_name)
    namespace = context["context"].get("namespace", "default")
    assert isinstance(namespace, str)
    return namespace


def get_current_context_name() -> str:
    """Get the name of the current kubeconfig context.

    As defined by the kubeconfig file. Raises ValueError when running in-cluster
    (no kubeconfig contexts available).
    """
    context = _Config.get_instance().get_context(None)
    return context["name"]


def validate_context_name(context_name: str) -> None:
    """Validate that the current kubeconfig context is a valid context.

    If the context is invalid, a ValueError is raised.
    """
    _ = _Config.get_instance().get_context(context_name)


class _Config:
    """A thread-safe singleton for Kubernetes configuration.

    Supports two modes:
    - In-cluster: uses the service account token mounted in the pod.
    - Kubeconfig: uses the kubeconfig file on disk.

    Tries in-cluster first, falls back to kubeconfig. Loaded only once for
    performance and thread-safety.
    """

    _load_lock: threading.Lock = threading.Lock()
    _instance: _Config | None = None

    def __init__(
        self,
        contexts: list[_KubeContext] | None,
        current_context: _KubeContext | None,
        *,
        in_cluster: bool = False,
    ):
        self.contexts: list[_KubeContext] | None = contexts
        self.current_context: _KubeContext | None = current_context
        self.in_cluster: bool = in_cluster

    @classmethod
    def get_instance(cls) -> _Config:
        with cls._load_lock:
            if cls._instance is None:
                cls._instance = cls._load()
        return cls._instance

    @classmethod
    def _load(cls) -> _Config:
        # Try in-cluster config first (running inside a pod).
        try:
            config.load_incluster_config()
            logger.info("Loaded in-cluster Kubernetes configuration.")
            return _Config(contexts=None, current_context=None, in_cluster=True)
        except ConfigException:
            pass

        # Fall back to kubeconfig file.
        config.load_kube_config()
        contexts, current = config.list_kube_config_contexts()
        typed_contexts = cast(list[_KubeContext], contexts)
        typed_current = cast(_KubeContext | None, current)
        return _Config(
            contexts=typed_contexts, current_context=typed_current, in_cluster=False
        )

    @classmethod
    def ensure_loaded(cls) -> None:
        _ = cls.get_instance()

    def get_context(self, context_name: str | None) -> _KubeContext:
        if self.in_cluster:
            if context_name is not None:
                raise ValueError(
                    "Named contexts are not available when running in-cluster. "
                    + f"Requested context: '{context_name}'."
                )
            return {"name": "in-cluster", "context": {}}
        if context_name is None:
            return self._get_current_context()
        return self._get_named_context(context_name)

    def _get_current_context(self) -> _KubeContext:
        if self.current_context is None:
            raise ValueError(
                "Could not get the current context because the current context is not "
                + "set in the kubeconfig file."
            )
        return self.current_context

    def _get_named_context(self, context_name: str) -> _KubeContext:
        if not self.contexts:
            raise ValueError(
                f"Could not find a context named '{context_name}' in kubeconfig "
                + "because no contexts were present in the kubeconfig file."
            )
        for context in self.contexts:
            if context["name"] == context_name:
                return context
        available = [ctx["name"] for ctx in self.contexts]
        raise ValueError(
            f"Could not find a context named '{context_name}' in the kubeconfig file. "
            + f"Available contexts: {available}."
        )


class _ThreadLocalClientFactory:
    """Each instance of this class assumes that only one thread may access it."""

    def __init__(self) -> None:
        self._current_context_client: client.CoreV1Api | None = None
        self._clients: dict[str, client.CoreV1Api] = {}

    def get_client(self, context_name: str | None) -> client.CoreV1Api:
        if context_name is None:
            return self._get_or_create_client_for_current_context()
        return self._get_or_create_client_for_named_context(context_name)

    def _get_or_create_client_for_current_context(self) -> client.CoreV1Api:
        if self._current_context_client is None:
            self._current_context_client = client.CoreV1Api()
        return self._current_context_client

    def _get_or_create_client_for_named_context(
        self, context_name: str
    ) -> client.CoreV1Api:
        if context_name in self._clients:
            return self._clients[context_name]
        api_client = self._create_client_for_named_context(context_name)
        self._clients[context_name] = api_client
        return api_client

    def _create_client_for_named_context(self, context_name: str) -> client.CoreV1Api:
        return client.CoreV1Api(
            api_client=config.new_client_from_config(context=context_name)
        )

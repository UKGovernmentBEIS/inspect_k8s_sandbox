from __future__ import annotations

import logging
import threading
from typing import Any

from kubernetes import client, config  # type: ignore

logger = logging.getLogger(__name__)

_thread_local = threading.local()


def k8s_client(context_name: str | None) -> client.CoreV1Api:
    """
    Get a thread-local Kubernetes client for interacting with the specified context.

    The context name must refer to an existing context within the kubeconfig file. If
    context is None, the current context is used.

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

    If context_name is None, the current context is used.

    If the namespace is not specified in the kubeconfig file, "default" is returned.
    """
    context = _Config.get_instance().get_context(context_name)
    namespace = context["context"].get("namespace", "default")
    assert isinstance(namespace, str)
    return namespace


def get_current_context_name() -> str:
    """Get the name of the current kubeconfig context.

    As defined by the kubeconfig file.
    """
    context = _Config.get_instance().get_context(None)
    return context["name"]


def validate_context_name(context_name: str) -> None:
    """Validate that the current kubeconfig context is a valid context.

    If the context is invalid, a ValueError is raised.
    """
    _Config.get_instance().get_context(context_name)


class _Config:
    """A thread-safe singleton that holds a snapshot of the kubeconfig file.

    Loaded only once for performance and thread-safety.

    This config can become out of date if the underlying kubeconfig file on disk is
    modified after it is loaded.
    """

    _load_lock: threading.Lock = threading.Lock()
    _instance: _Config | None = None

    def __init__(
        self,
        contexts: list[dict[str, Any]] | None,
        current_context: dict[str, Any] | None,
    ):
        self.contexts = contexts
        self.current_context = current_context

    @classmethod
    def get_instance(cls) -> _Config:
        # The kubernetes.config module is not thread-safe.
        with cls._load_lock:
            if cls._instance is None:
                # Must call load_kube_config() before any clients created with the
                # current context can be used.
                config.load_kube_config()
                cls._instance = _Config(*config.list_kube_config_contexts())
        return cls._instance

    @classmethod
    def ensure_loaded(cls) -> None:
        cls.get_instance()

    def get_context(self, context_name: str | None) -> dict[str, Any]:
        if context_name is None:
            return self._get_current_context()
        return self._get_named_context(context_name)

    def _get_current_context(self) -> dict:
        if self.current_context is None:
            raise ValueError(
                "Could not get the current context because the current context is not "
                "set in the kubeconfig file."
            )
        return self.current_context

    def _get_named_context(self, context_name: str) -> dict:
        if not self.contexts:
            raise ValueError(
                f"Could not find a context named '{context_name}' in kubeconfig "
                "because no contexts were present in the kubeconfig file."
            )
        for context in self.contexts:
            if context["name"] == context_name:
                return context
        raise ValueError(
            f"Could not find a context named '{context_name}' in the kubeconfig file. "
            f"Available contexts: {[ctx['name'] for ctx in self.contexts]}."
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
        # Inspired from example
        # https://github.com/kubernetes-client/python/blob/master/examples/multiple_clusters.py
        return client.CoreV1Api(
            api_client=config.new_client_from_config(context=context_name)
        )

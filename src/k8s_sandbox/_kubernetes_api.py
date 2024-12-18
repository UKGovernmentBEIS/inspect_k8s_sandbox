from __future__ import annotations

import logging
import threading

from kubernetes import client, config  # type: ignore

logger = logging.getLogger(__name__)

_thread_local = threading.local()
_load_config_lock = threading.Lock()
_config_loaded = False


def k8s_client() -> client.CoreV1Api:
    """
    Gets a thread-local Kubernetes client.

    This function is thread-safe and ensures that the Kubernetes configuration is
    loaded.
    A Kubernetes client cannot be used simultaneously from multiple threads (which are
    used because the kubernetes client is not async).
    """
    _ensure_config_loaded()
    if not hasattr(_thread_local, "client"):
        _thread_local.client = client.CoreV1Api()
    return _thread_local.client


def get_current_context_namespace() -> str:
    """Get the current context's namespace from the Kubernetes configuration."""
    _ensure_config_loaded()
    _, current_ctx = config.list_kube_config_contexts()
    namespace = current_ctx["context"]["namespace"]
    assert isinstance(namespace, str)
    return namespace


def _ensure_config_loaded() -> None:
    with _load_config_lock:
        global _config_loaded
        if not _config_loaded:
            config.load_kube_config()
            _config_loaded = True

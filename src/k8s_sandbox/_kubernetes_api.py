"""Re-export from k8s_sandbox_core for backward compatibility."""

from k8s_sandbox_core._kubernetes_api import *  # noqa: F401, F403
from k8s_sandbox_core._kubernetes_api import (  # explicit for type checkers
    _Config,
    _ThreadLocalClientFactory,
    get_current_context_name,
    get_default_namespace,
    k8s_client,
    validate_context_name,
)

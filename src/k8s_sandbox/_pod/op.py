"""Re-export from k8s_sandbox_core for backward compatibility."""

from k8s_sandbox_core._pod.op import *  # noqa: F401, F403
from k8s_sandbox_core._pod.op import (  # explicit for type checkers
    PodInfo,
    PodOperation,
    _KEEPALIVE_INTERVAL_SECONDS,
    _send_keepalive,
    check_for_pod_restart,
    raise_for_known_read_write_errors,
)

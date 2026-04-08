"""Re-export from k8s_sandbox_core for backward compatibility."""

from k8s_sandbox_core._pod import GetReturncodeError, Pod, PodError

__all__ = [
    "GetReturncodeError",
    "PodError",
    "Pod",
]

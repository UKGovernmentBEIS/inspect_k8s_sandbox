"""Re-export from k8s_sandbox_core for backward compatibility."""

from k8s_sandbox_core._pod.get_returncode import get_returncode
from k8s_sandbox_core._pod.error import GetReturncodeError

__all__ = ["get_returncode", "GetReturncodeError"]

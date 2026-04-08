"""Re-export from k8s_sandbox_core for backward compatibility."""

from k8s_sandbox_core._pod.execute import (
    COMPLETED_SENTINEL,
    COMPLETED_SENTINEL_PATTERN,
    ExecuteOperation,
)

__all__ = ["COMPLETED_SENTINEL", "COMPLETED_SENTINEL_PATTERN", "ExecuteOperation"]

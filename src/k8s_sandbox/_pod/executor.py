"""Re-export from k8s_sandbox_core for backward compatibility."""

from k8s_sandbox_core._pod.executor import PodOpExecutor

__all__ = ["PodOpExecutor"]

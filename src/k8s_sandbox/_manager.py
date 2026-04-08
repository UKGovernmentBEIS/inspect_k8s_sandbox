"""Re-export from k8s_sandbox_core for backward compatibility."""

from k8s_sandbox_core._manager import (
    HelmReleaseManager,
    uninstall_all_unmanaged_releases,
    uninstall_unmanaged_release,
)

__all__ = [
    "HelmReleaseManager",
    "uninstall_all_unmanaged_releases",
    "uninstall_unmanaged_release",
]

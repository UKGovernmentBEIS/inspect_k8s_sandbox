from typing import Any

from k8s_sandbox._error import K8sError
from k8s_sandbox._logger import format_log_message


class PodError(Exception):
    """
    A generic error raised when interacting with a Pod.

    This will typically cause the eval to fail.
    """

    def __init__(self, message: str, **kwargs: Any) -> None:
        super().__init__(format_log_message(message, **kwargs))


class GetReturncodeError(Exception):
    """The return code of a Pod operation could not be retrieved."""

    pass


class ExecutableNotFoundError(Exception):
    """The executable could not be found in the container.

    This is **not** raised as a result of user-supplied commands not being found. It is
    typically raised when /bin/sh or runuser cannot be found in the container.
    """

    pass


class PodReplacedError(K8sError):
    """The pod's UID has changed since it was first observed.

    Typically caused by eviction (e.g. node disk/memory pressure, OOM) followed
    by the controlling workload (StatefulSet/Deployment) recreating the pod
    with the same name but a fresh UID. The previous pod is gone, along with
    any state held in its filesystem or memory that did not reside on a
    persistent volume.

    The sandbox provider refreshes its cached pod identity when this is
    raised, so subsequent operations target the new pod and will not re-raise
    ``PodReplacedError`` unless yet another replacement occurs.
    """

    def __init__(  # noqa: D107
        self,
        *,
        pod_name: str,
        old_uid: str,
        new_uid: str,
        new_restart_count: int,
        **kwargs: Any,
    ):
        self.pod_name = pod_name
        self.old_uid = old_uid
        self.new_uid = new_uid
        self.new_restart_count = new_restart_count
        super().__init__(
            f"Pod '{pod_name}' has been replaced "
            f"(old UID {old_uid}, new UID {new_uid})",
            pod=pod_name,
            old_uid=old_uid,
            new_uid=new_uid,
            **kwargs,
        )


class ContainerRestartedError(K8sError):
    """A container in the pod has restarted since the pod was first observed.

    The pod itself is the same (same UID), but the container's process was
    restarted (OOM, crash, liveness probe failure, etc.). Files on disk
    survive; in-memory state and any background processes started by the agent
    do not.

    The sandbox provider refreshes its cached restart count when this is
    raised, so subsequent operations will not re-raise
    ``ContainerRestartedError`` unless a further restart occurs.
    """

    def __init__(  # noqa: D107
        self,
        *,
        pod_name: str,
        container_name: str,
        restart_count: int,
        last_reason: str,
        **kwargs: Any,
    ):
        self.pod_name = pod_name
        self.container_name = container_name
        self.restart_count = restart_count
        self.last_reason = last_reason
        super().__init__(
            f"Container '{container_name}' in pod '{pod_name}' has restarted "
            f"{restart_count} time(s) (last reason: {last_reason}); "
            "in-memory pod state is no longer guaranteed",
            pod=pod_name,
            container=container_name,
            restart_count=restart_count,
            last_reason=last_reason,
            **kwargs,
        )

from unittest.mock import MagicMock, patch

from kubernetes.client import (  # type: ignore
    CoreV1Event,
    CoreV1EventList,
    V1ContainerState,
    V1ContainerStateRunning,
    V1ContainerStateTerminated,
    V1ContainerStateWaiting,
    V1ContainerStatus,
    V1ObjectMeta,
    V1ObjectReference,
    V1Pod,
    V1PodList,
    V1PodStatus,
)

from k8s_sandbox._diagnostics import describe_release_pods


def _pod(
    name: str,
    phase: str,
    container_statuses,
    labels=None,
    init_container_statuses=None,
) -> V1Pod:
    return V1Pod(
        metadata=V1ObjectMeta(name=name, labels=labels or {}),
        status=V1PodStatus(
            phase=phase,
            container_statuses=container_statuses,
            init_container_statuses=init_container_statuses,
        ),
    )


def _patch_client(pods, events=None):
    client = MagicMock()
    client.list_namespaced_pod.return_value = V1PodList(items=pods)
    client.list_namespaced_event.return_value = CoreV1EventList(items=events or [])
    return patch("k8s_sandbox._diagnostics.k8s_client", return_value=client)


def test_surfaces_oom_killed_reason_exit_code_and_restart_count() -> None:
    container = V1ContainerStatus(
        name="default",
        image="busybox:1.36",
        image_id="",
        ready=False,
        restart_count=3,
        state=V1ContainerState(
            waiting=V1ContainerStateWaiting(reason="CrashLoopBackOff")
        ),
        last_state=V1ContainerState(
            terminated=V1ContainerStateTerminated(reason="OOMKilled", exit_code=137)
        ),
    )
    pods = [_pod("rel-default", "Running", [container])]

    with _patch_client(pods):
        summary = describe_release_pods(None, "default", "rel")

    assert summary is not None
    assert "default" in summary  # container name
    assert "OOMKilled" in summary
    assert "137" in summary
    assert "3" in summary  # restart count


def test_surfaces_image_pull_backoff_reason_message_and_image() -> None:
    container = V1ContainerStatus(
        name="default",
        image="nonexistent.invalid/nope:latest",
        image_id="",
        ready=False,
        restart_count=0,
        state=V1ContainerState(
            waiting=V1ContainerStateWaiting(
                reason="ImagePullBackOff",
                message='Back-off pulling image "nonexistent.invalid/nope:latest"',
            )
        ),
    )
    pods = [_pod("rel-default", "Pending", [container])]

    with _patch_client(pods):
        summary = describe_release_pods(None, "default", "rel")

    assert summary is not None
    assert "ImagePullBackOff" in summary
    assert "Back-off pulling image" in summary
    assert "nonexistent.invalid/nope:latest" in summary


def test_surfaces_failing_init_container_cause() -> None:
    # A failing init container leaves the app container merely "waiting
    # (PodInitializing)"; the actionable cause lives in the init container's status.
    init_container = V1ContainerStatus(
        name="setup",
        image="busybox:1.36",
        image_id="",
        ready=False,
        restart_count=2,
        state=V1ContainerState(
            waiting=V1ContainerStateWaiting(reason="CrashLoopBackOff")
        ),
        last_state=V1ContainerState(
            terminated=V1ContainerStateTerminated(reason="Error", exit_code=1)
        ),
    )
    app_container = V1ContainerStatus(
        name="default",
        image="busybox:1.36",
        image_id="",
        ready=False,
        restart_count=0,
        state=V1ContainerState(
            waiting=V1ContainerStateWaiting(reason="PodInitializing")
        ),
    )
    pods = [
        _pod(
            "rel-default",
            "Pending",
            container_statuses=[app_container],
            init_container_statuses=[init_container],
        )
    ]

    with _patch_client(pods):
        summary = describe_release_pods(None, "default", "rel")

    assert summary is not None
    assert "init container 'setup'" in summary
    assert "Error" in summary
    assert "exit code 1" in summary
    assert "restarted 2 time(s)" in summary


def test_omits_completed_init_container_when_app_container_failing() -> None:
    # Only the app container's failure is reported; the completed (exit 0) init
    # container is omitted.
    init_container = V1ContainerStatus(
        name="setup",
        image="busybox:1.36",
        image_id="",
        ready=True,
        restart_count=0,
        state=V1ContainerState(
            terminated=V1ContainerStateTerminated(reason="Completed", exit_code=0)
        ),
    )
    app_container = V1ContainerStatus(
        name="default",
        image="busybox:1.36",
        image_id="",
        ready=False,
        restart_count=3,
        state=V1ContainerState(
            waiting=V1ContainerStateWaiting(reason="CrashLoopBackOff")
        ),
        last_state=V1ContainerState(
            terminated=V1ContainerStateTerminated(reason="Error", exit_code=1)
        ),
    )
    pods = [
        _pod(
            "rel-default",
            "Running",
            container_statuses=[app_container],
            init_container_statuses=[init_container],
        )
    ]

    with _patch_client(pods):
        summary = describe_release_pods(None, "default", "rel")

    assert summary is not None
    assert "init container" not in summary
    assert "container 'default'" in summary
    assert "exit code 1" in summary


def test_returns_none_when_init_completed_and_app_healthy() -> None:
    # A healthy pod with a completed init container produces no diagnostics at all.
    init_container = V1ContainerStatus(
        name="setup",
        image="busybox:1.36",
        image_id="",
        ready=True,
        restart_count=0,
        state=V1ContainerState(
            terminated=V1ContainerStateTerminated(reason="Completed", exit_code=0)
        ),
    )
    app_container = V1ContainerStatus(
        name="default",
        image="busybox:1.36",
        image_id="",
        ready=True,
        restart_count=0,
        state=V1ContainerState(running=V1ContainerStateRunning(started_at=None)),
    )
    pods = [
        _pod(
            "rel-default",
            "Running",
            container_statuses=[app_container],
            init_container_statuses=[init_container],
        )
    ]

    with _patch_client(pods):
        summary = describe_release_pods(None, "default", "rel")

    assert summary is None


def test_surfaces_failed_scheduling_event_when_pod_has_no_container_statuses() -> None:
    # An unschedulable pod stays Pending with no container statuses; the actionable
    # detail is in a FailedScheduling Warning event.
    pods = [_pod("rel-default", "Pending", container_statuses=None)]
    events = [
        CoreV1Event(
            metadata=V1ObjectMeta(name="evt-1"),
            involved_object=V1ObjectReference(name="rel-default"),
            reason="FailedScheduling",
            message="0/1 nodes available: 1 Insufficient cpu",
            type="Warning",
        )
    ]

    with _patch_client(pods, events) as mock_client_factory:
        summary = describe_release_pods(None, "default", "rel")

    assert summary is not None
    assert "FailedScheduling" in summary
    assert "Insufficient cpu" in summary
    # Warning events are filtered server-side rather than in Python.
    mock_client_factory.return_value.list_namespaced_event.assert_called_once_with(
        "default", field_selector="type=Warning"
    )


def test_returns_none_and_does_not_raise_when_api_call_fails() -> None:
    # describe_release_pods runs from error-handling paths; it must never raise and mask
    # the original failure.
    with patch("k8s_sandbox._diagnostics.k8s_client", side_effect=RuntimeError("boom")):
        summary = describe_release_pods(None, "default", "rel")

    assert summary is None


def test_returns_none_when_all_containers_healthy_and_no_events() -> None:
    container = V1ContainerStatus(
        name="default",
        image="busybox:1.36",
        image_id="",
        ready=True,
        restart_count=0,
        state=V1ContainerState(running=V1ContainerStateRunning(started_at=None)),
    )
    pods = [_pod("rel-default", "Running", [container])]

    with _patch_client(pods):
        summary = describe_release_pods(None, "default", "rel")

    assert summary is None

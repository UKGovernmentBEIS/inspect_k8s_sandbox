import threading
from unittest.mock import MagicMock, patch

import pytest

from k8s_sandbox._pod import pod as pod_module
from k8s_sandbox._pod.error import ContainerRestartedError, PodReplacedError
from k8s_sandbox._pod.op import PodInfo, check_for_pod_restart
from k8s_sandbox._pod.pod import Pod


def _k8s_pod(uid: str, container_name: str, restart_count: int) -> MagicMock:
    pod = MagicMock()
    pod.metadata.uid = uid
    pod.metadata.name = "agent-env-abc-default-0"
    status = MagicMock()
    status.name = container_name
    status.restart_count = restart_count
    status.last_state.terminated.reason = "OOMKilled"
    pod.status.container_statuses = [status]
    return pod


def _pod_info(uid: str = "uid-1", restart_count: int = 0) -> PodInfo:
    return PodInfo(
        name="agent-env-abc-default-0",
        namespace="ns",
        context_name=None,
        default_container_name="default",
        uid=uid,
        initial_restart_count=restart_count,
        restarted_container_behavior="raise",
    )


def test_no_change_does_not_raise():
    with patch("k8s_sandbox._pod.op.k8s_client") as mock_client:
        mock_client.return_value.read_namespaced_pod.return_value = _k8s_pod(
            uid="uid-1", container_name="default", restart_count=0
        )
        # Should not raise
        check_for_pod_restart(_pod_info())


def test_missing_container_statuses_skips_restart_check():
    # Briefly possible right after pod scheduling: same UID but kubelet
    # hasn't published container_statuses yet. Must not assert.
    pod = MagicMock()
    pod.metadata.uid = "uid-1"
    pod.metadata.name = "agent-env-abc-default-0"
    pod.status.container_statuses = None
    with patch("k8s_sandbox._pod.op.k8s_client") as mock_client:
        mock_client.return_value.read_namespaced_pod.return_value = pod
        check_for_pod_restart(_pod_info(uid="uid-1"))


def test_pod_replaced_raises_typed_with_new_restart_count():
    with patch("k8s_sandbox._pod.op.k8s_client") as mock_client:
        mock_client.return_value.read_namespaced_pod.return_value = _k8s_pod(
            uid="uid-NEW", container_name="default", restart_count=2
        )
        with pytest.raises(PodReplacedError) as excinfo:
            check_for_pod_restart(_pod_info(uid="uid-OLD", restart_count=0))
    err = excinfo.value
    assert err.old_uid == "uid-OLD"
    assert err.new_uid == "uid-NEW"
    assert err.new_restart_count == 2
    assert err.pod_name == "agent-env-abc-default-0"


def test_container_restarted_raises_typed():
    with patch("k8s_sandbox._pod.op.k8s_client") as mock_client:
        mock_client.return_value.read_namespaced_pod.return_value = _k8s_pod(
            uid="uid-1", container_name="default", restart_count=3
        )
        with pytest.raises(ContainerRestartedError) as excinfo:
            check_for_pod_restart(_pod_info(uid="uid-1", restart_count=1))
    err = excinfo.value
    assert err.restart_count == 3
    assert err.container_name == "default"
    assert err.last_reason == "OOMKilled"


def _make_pod(restarted_container_behavior: str = "raise") -> Pod:
    return Pod(
        name="agent-env-abc-default-0",
        namespace="ns",
        context_name=None,
        default_container_name="default",
        uid="uid-OLD",
        initial_restart_count=0,
        restarted_container_behavior=restarted_container_behavior,  # type: ignore[arg-type]
    )


async def test_pod_auto_refreshes_uid_after_replacement():
    pod = _make_pod()
    with patch("k8s_sandbox._pod.op.k8s_client") as mock_client:
        mock_client.return_value.read_namespaced_pod.return_value = _k8s_pod(
            uid="uid-NEW", container_name="default", restart_count=2
        )
        with pytest.raises(PodReplacedError):
            await pod.check_for_pod_restart()
    # Cached identity is refreshed so a subsequent call against the new pod
    # does NOT re-raise.
    assert pod.info.uid == "uid-NEW"
    assert pod.info.initial_restart_count == 2
    with patch("k8s_sandbox._pod.op.k8s_client") as mock_client:
        mock_client.return_value.read_namespaced_pod.return_value = _k8s_pod(
            uid="uid-NEW", container_name="default", restart_count=2
        )
        await pod.check_for_pod_restart()  # no raise


async def test_warn_mode_logs_and_does_not_raise_but_still_refreshes(caplog):
    pod = _make_pod(restarted_container_behavior="warn")
    with patch("k8s_sandbox._pod.op.k8s_client") as mock_client:
        mock_client.return_value.read_namespaced_pod.return_value = _k8s_pod(
            uid="uid-NEW", container_name="default", restart_count=0
        )
        with caplog.at_level("WARNING"):
            await pod.check_for_pod_restart()
    assert pod.info.uid == "uid-NEW"
    assert any("has been replaced" in r.message for r in caplog.records)


async def test_warn_is_logged_on_calling_thread_not_executor_thread():
    # Detection runs in a PodOpExecutor worker thread, but the warn-mode
    # warning must be emitted on the calling (event loop) thread. Inspect's
    # logging-to-transcript capture is contextvar-based and run_in_executor
    # does not propagate the context to the worker, so a warning logged there
    # would never reach the sample transcript. This guards that regression.
    pod = _make_pod(restarted_container_behavior="warn")
    calling_thread = threading.get_ident()
    warn_threads: list[int] = []
    with patch("k8s_sandbox._pod.op.k8s_client") as mock_client:
        mock_client.return_value.read_namespaced_pod.return_value = _k8s_pod(
            uid="uid-NEW", container_name="default", restart_count=0
        )
        with patch.object(
            pod_module.logger,
            "warning",
            side_effect=lambda *a, **k: warn_threads.append(threading.get_ident()),
        ):
            await pod.check_for_pod_restart()
    assert warn_threads == [calling_thread]


async def test_pod_auto_refreshes_restart_count_after_container_restart():
    pod = _make_pod()
    with patch("k8s_sandbox._pod.op.k8s_client") as mock_client:
        mock_client.return_value.read_namespaced_pod.return_value = _k8s_pod(
            uid="uid-OLD", container_name="default", restart_count=4
        )
        with pytest.raises(ContainerRestartedError):
            await pod.check_for_pod_restart()
    assert pod.info.initial_restart_count == 4
    # Same restart_count next time → no raise.
    with patch("k8s_sandbox._pod.op.k8s_client") as mock_client:
        mock_client.return_value.read_namespaced_pod.return_value = _k8s_pod(
            uid="uid-OLD", container_name="default", restart_count=4
        )
        await pod.check_for_pod_restart()

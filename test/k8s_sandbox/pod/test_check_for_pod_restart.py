import asyncio
import io
import json
import pathlib
from unittest.mock import MagicMock, patch

import pytest

from k8s_sandbox._pod.error import ContainerRestartedError, PodReplacedError
from k8s_sandbox._pod.op import PodInfo, check_for_pod_restart
from k8s_sandbox._pod.pod import Pod


def _raw_pod_response(body: dict) -> MagicMock:
    """Mimic the raw urllib3 response returned by ``_preload_content=False``."""
    response = MagicMock()
    response.data = json.dumps(body).encode()
    return response


def _k8s_pod(uid: str, container_name: str, restart_count: int) -> MagicMock:
    return _raw_pod_response(
        {
            "metadata": {"uid": uid, "name": "agent-env-abc-default-0"},
            "status": {
                "containerStatuses": [
                    {
                        "name": container_name,
                        "restartCount": restart_count,
                        "lastState": {"terminated": {"reason": "OOMKilled"}},
                    }
                ]
            },
        }
    )


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
    # hasn't published container_statuses yet. Must not raise.
    pod = _raw_pod_response(
        {"metadata": {"uid": "uid-1", "name": "agent-env-abc-default-0"}, "status": {}}
    )
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


def test_pod_auto_refreshes_uid_after_replacement():
    pod = _make_pod()
    with patch("k8s_sandbox._pod.op.k8s_client") as mock_client:
        mock_client.return_value.read_namespaced_pod.return_value = _k8s_pod(
            uid="uid-NEW", container_name="default", restart_count=2
        )
        with pytest.raises(PodReplacedError):
            pod._check_for_pod_restart_sync()
    # Cached identity is refreshed so a subsequent call against the new pod
    # does NOT re-raise.
    assert pod.info.uid == "uid-NEW"
    assert pod.info.initial_restart_count == 2
    with patch("k8s_sandbox._pod.op.k8s_client") as mock_client:
        mock_client.return_value.read_namespaced_pod.return_value = _k8s_pod(
            uid="uid-NEW", container_name="default", restart_count=2
        )
        pod._check_for_pod_restart_sync()  # no raise


def test_warn_mode_logs_and_does_not_raise_but_still_refreshes(caplog):
    pod = _make_pod(restarted_container_behavior="warn")
    with patch("k8s_sandbox._pod.op.k8s_client") as mock_client:
        mock_client.return_value.read_namespaced_pod.return_value = _k8s_pod(
            uid="uid-NEW", container_name="default", restart_count=0
        )
        with caplog.at_level("WARNING"):
            pod._check_for_pod_restart_sync()
    assert pod.info.uid == "uid-NEW"
    assert any("has been replaced" in r.message for r in caplog.records)


def test_pod_auto_refreshes_restart_count_after_container_restart():
    pod = _make_pod()
    with patch("k8s_sandbox._pod.op.k8s_client") as mock_client:
        mock_client.return_value.read_namespaced_pod.return_value = _k8s_pod(
            uid="uid-OLD", container_name="default", restart_count=4
        )
        with pytest.raises(ContainerRestartedError):
            pod._check_for_pod_restart_sync()
    assert pod.info.initial_restart_count == 4
    # Same restart_count next time → no raise.
    with patch("k8s_sandbox._pod.op.k8s_client") as mock_client:
        mock_client.return_value.read_namespaced_pod.return_value = _k8s_pod(
            uid="uid-OLD", container_name="default", restart_count=4
        )
        pod._check_for_pod_restart_sync()


# --- INSPECT_POD_RESTART_CHECK gating (METR-private; not upstream) -----------


def _make_replaced_pod_response() -> MagicMock:
    return _k8s_pod(uid="uid-NEW", container_name="default", restart_count=0)


def test_env_var_skips_file_op_check_but_not_exec(monkeypatch):
    # Sandbox provider: Pod.read_file / Pod.write_file must skip the pre-op
    # restart check when INSPECT_POD_RESTART_CHECK=false, while Pod.exec must
    # always run it. Gate the file-op API hammer; keep the high-signal exec
    # check unconditional.
    monkeypatch.setenv("INSPECT_POD_RESTART_CHECK", "false")

    # Patch the executor to run callables inline so we don't need a real
    # threadpool / event loop boundary here.
    with patch(
        "k8s_sandbox._pod.executor.PodOpExecutor.get_instance"
    ) as mock_get_instance:
        executor = MagicMock()

        async def queue(callable):
            return callable()

        executor.queue_operation = queue
        mock_get_instance.return_value = executor

        with patch("k8s_sandbox._pod.op.k8s_client") as mock_client:
            mock_client.return_value.read_namespaced_pod.return_value = (
                _make_replaced_pod_response()
            )
            # Patch the actual file ops so they don't try to hit a real pod.
            with (
                patch("k8s_sandbox._pod.pod.ReadFileOperation"),
                patch("k8s_sandbox._pod.pod.WriteFileOperation"),
                patch("k8s_sandbox._pod.pod.ExecuteOperation"),
            ):
                pod = _make_pod()

                # read_file: env var should suppress the check entirely.
                asyncio.run(pod.read_file(pathlib.Path("/x"), io.BytesIO()))
                assert mock_client.return_value.read_namespaced_pod.call_count == 0

                # write_file: same.
                asyncio.run(pod.write_file(io.BytesIO(b""), pathlib.Path("/x")))
                assert mock_client.return_value.read_namespaced_pod.call_count == 0

                # exec: must always check, env var notwithstanding.
                with pytest.raises(PodReplacedError):
                    asyncio.run(pod.exec(["true"], None, None, {}, None, None))
                assert mock_client.return_value.read_namespaced_pod.call_count == 1


def test_env_var_default_keeps_file_op_check(monkeypatch):
    monkeypatch.delenv("INSPECT_POD_RESTART_CHECK", raising=False)

    with patch(
        "k8s_sandbox._pod.executor.PodOpExecutor.get_instance"
    ) as mock_get_instance:
        executor = MagicMock()

        async def queue(callable):
            return callable()

        executor.queue_operation = queue
        mock_get_instance.return_value = executor

        with patch("k8s_sandbox._pod.op.k8s_client") as mock_client:
            mock_client.return_value.read_namespaced_pod.return_value = (
                _make_replaced_pod_response()
            )
            with patch("k8s_sandbox._pod.pod.ReadFileOperation"):
                pod = _make_pod()
                with pytest.raises(PodReplacedError):
                    asyncio.run(pod.read_file(pathlib.Path("/x"), io.BytesIO()))
                assert mock_client.return_value.read_namespaced_pod.call_count == 1

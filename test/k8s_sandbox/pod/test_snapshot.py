import json
from unittest.mock import MagicMock

import pytest

from k8s_sandbox._pod.snapshot import (
    ContainerStatus,
    PodSnapshot,
    _parse_pod,
    list_pods,
    read_pod,
)


def _raw_response(body: dict) -> MagicMock:
    response = MagicMock()
    response.data = json.dumps(body).encode()
    return response


def _pod_body(
    *,
    name: str = "agent-env-abc-default-0",
    uid: str = "uid-1",
    labels: dict | None = None,
    containers: list[str] | None = None,
    container_statuses: list[dict] | None = None,
) -> dict:
    body: dict = {"metadata": {"name": name, "uid": uid}, "spec": {}, "status": {}}
    if labels is not None:
        body["metadata"]["labels"] = labels
    if containers is not None:
        body["spec"]["containers"] = [{"name": n} for n in containers]
    if container_statuses is not None:
        body["status"]["containerStatuses"] = container_statuses
    return body


def test_parse_pod_extracts_key_fields():
    # Arrange
    body = _pod_body(
        labels={"inspect/service": "default"},
        containers=["default", "sidecar"],
        container_statuses=[
            {
                "name": "default",
                "restartCount": 2,
                "lastState": {"terminated": {"reason": "OOMKilled"}},
            }
        ],
    )

    # Act
    snapshot = _parse_pod(body)

    # Assert
    assert snapshot == PodSnapshot(
        name="agent-env-abc-default-0",
        uid="uid-1",
        labels={"inspect/service": "default"},
        container_names=("default", "sidecar"),
        container_statuses=(
            ContainerStatus(
                name="default", restart_count=2, last_terminated_reason="OOMKilled"
            ),
        ),
    )


def test_parse_pod_distinguishes_missing_statuses_from_empty():
    # Kubelet hasn't published container statuses yet -> None, not ().
    no_statuses = _parse_pod(_pod_body())
    empty_statuses = _parse_pod(_pod_body(container_statuses=[]))

    assert no_statuses.container_statuses is None
    assert empty_statuses.container_statuses == ()


def test_parse_pod_defaults_missing_optional_fields():
    snapshot = _parse_pod(_pod_body())

    assert snapshot.labels == {}
    assert snapshot.container_names == ()


def test_parse_pod_raises_when_identity_missing():
    with pytest.raises(ValueError, match="metadata.name or metadata.uid"):
        _parse_pod({"metadata": {"name": "no-uid"}})


@pytest.mark.parametrize(
    ("container_name", "expected"),
    [("default", 3), ("sidecar", 0), ("absent", 0)],
)
def test_restart_count_for(container_name: str, expected: int):
    snapshot = _parse_pod(
        _pod_body(
            container_statuses=[{"name": "default", "restartCount": 3}],
        )
    )

    assert snapshot.restart_count_for(container_name) == expected


def test_restart_count_for_is_zero_when_statuses_unpublished():
    snapshot = _parse_pod(_pod_body())

    assert snapshot.restart_count_for("default") == 0


def test_read_pod_requests_raw_json_and_parses():
    # Arrange
    api = MagicMock()
    api.read_namespaced_pod.return_value = _raw_response(_pod_body(uid="uid-42"))

    # Act
    snapshot = read_pod(api, name="pod", namespace="ns")

    # Assert
    api.read_namespaced_pod.assert_called_once_with(
        name="pod", namespace="ns", _preload_content=False
    )
    assert snapshot.uid == "uid-42"


def test_list_pods_parses_all_items():
    # Arrange
    api = MagicMock()
    api.list_namespaced_pod.return_value = _raw_response(
        {"items": [_pod_body(uid="a"), _pod_body(uid="b")]}
    )

    # Act
    snapshots = list_pods(api, "ns", label_selector="app=x")

    # Assert
    api.list_namespaced_pod.assert_called_once_with(
        "ns", label_selector="app=x", _preload_content=False
    )
    assert [s.uid for s in snapshots] == ["a", "b"]


def test_list_pods_handles_empty_items():
    api = MagicMock()
    api.list_namespaced_pod.return_value = _raw_response({})

    assert list_pods(api, "ns", label_selector="app=x") == []

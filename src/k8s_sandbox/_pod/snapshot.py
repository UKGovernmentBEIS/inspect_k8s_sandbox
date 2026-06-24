"""Fast, low-overhead reads of Kubernetes Pod state.

The kubernetes Python client deserializes every API response into its generated
model classes (``V1Pod`` et al.) via ``ApiClient.__deserialize``. That code path
is slow and effectively single-threaded, so it becomes a serialization
bottleneck under high concurrency. In particular the frequently-called pod
restart check (``check_for_pod_restart``) starts timing out and grinds large,
many-cluster evals to a halt.
See https://github.com/kubernetes-client/python/issues/2284.

We sidestep the model layer by requesting the raw response
(``_preload_content=False``) and parsing only the handful of fields we actually
use out of the JSON ourselves. ``PodSnapshot`` is that minimal, immutable view.

Note: the kubernetes client still raises ``ApiException`` for non-2xx responses
even with ``_preload_content=False`` (the status check runs before the response
is returned), so error handling at call sites is unchanged. The raw JSON uses
camelCase keys (``containerStatuses``, ``restartCount``, ...), unlike the
snake_case attributes on the generated model classes.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, cast

from kubernetes import client  # type: ignore
from urllib3 import HTTPResponse


@dataclass(frozen=True)
class ContainerStatus:
    """A minimal view of a single container's status."""

    name: str
    restart_count: int
    last_terminated_reason: str | None


@dataclass(frozen=True)
class PodSnapshot:
    """A minimal, immutable view of a Kubernetes Pod.

    Only the fields used by this library are parsed. ``container_statuses`` is
    ``None`` when the kubelet has not yet published any (briefly possible right
    after scheduling), as distinct from an empty tuple.
    """

    name: str
    uid: str
    labels: dict[str, str]
    container_names: tuple[str, ...]
    """Container names from the pod spec, in declared order."""
    container_statuses: tuple[ContainerStatus, ...] | None

    def status_for(self, container_name: str) -> ContainerStatus | None:
        if self.container_statuses is None:
            return None
        return next(
            (cs for cs in self.container_statuses if cs.name == container_name),
            None,
        )

    def restart_count_for(self, container_name: str) -> int:
        status = self.status_for(container_name)
        return status.restart_count if status is not None else 0


def read_pod(api: client.CoreV1Api, name: str, namespace: str) -> PodSnapshot:
    """Read a single pod, bypassing the kubernetes client's model deserialization."""
    # _preload_content is passed through to the generated client's **kwargs at
    # runtime but is absent from the typed stubs, hence the call-arg ignore.
    response = cast(
        HTTPResponse,
        api.read_namespaced_pod(  # type: ignore[call-arg]
            name=name, namespace=namespace, _preload_content=False
        ),
    )
    return _parse_pod(json.loads(response.data))


def list_pods(
    api: client.CoreV1Api, namespace: str, *, label_selector: str
) -> list[PodSnapshot]:
    """List pods, bypassing the kubernetes client's model deserialization."""
    # See read_pod for why _preload_content needs a call-arg ignore.
    response = cast(
        HTTPResponse,
        api.list_namespaced_pod(  # type: ignore[call-arg]
            namespace, label_selector=label_selector, _preload_content=False
        ),
    )
    body = json.loads(response.data)
    return [_parse_pod(item) for item in body.get("items", [])]


def _parse_pod(pod: dict[str, Any]) -> PodSnapshot:
    metadata = pod.get("metadata") or {}
    spec = pod.get("spec") or {}
    status = pod.get("status") or {}
    name = metadata.get("name")
    uid = metadata.get("uid")
    if name is None or uid is None:
        raise ValueError(f"Pod is missing metadata.name or metadata.uid: {metadata}")
    raw_statuses = status.get("containerStatuses")
    container_statuses = (
        tuple(_parse_container_status(cs) for cs in raw_statuses)
        if raw_statuses is not None
        else None
    )
    return PodSnapshot(
        name=name,
        uid=uid,
        labels=metadata.get("labels") or {},
        container_names=tuple(c["name"] for c in spec.get("containers") or []),
        container_statuses=container_statuses,
    )


def _parse_container_status(cs: dict[str, Any]) -> ContainerStatus:
    terminated = (cs.get("lastState") or {}).get("terminated") or {}
    return ContainerStatus(
        name=cs["name"],
        restart_count=cs.get("restartCount", 0),
        last_terminated_reason=terminated.get("reason"),
    )

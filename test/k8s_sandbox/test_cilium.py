import json
import logging
from contextlib import contextmanager
from typing import Any, AsyncGenerator, Generator, Sequence
from unittest.mock import MagicMock, patch

import pytest
import pytest_asyncio
import urllib3
from kubernetes.client.exceptions import ApiException
from pytest import LogCaptureFixture

from k8s_sandbox._cilium import (
    CiliumPolicyNotRealizedError,
    CiliumWaitOutcome,
    _warn_endpoints_unreadable,
    delete_release_network_policies,
    wait_for_policy_realized,
)
from k8s_sandbox._error import K8sError
from k8s_sandbox._sandbox_environment import K8sSandboxEnvironment
from test.k8s_sandbox.utils import install_sandbox_environments

RELEASE = "abcd1234"
POD = f"agent-env-{RELEASE}-default-0"


@pytest.fixture(autouse=True)
def _reset_permission_warning() -> Generator[None, None, None]:
    _warn_endpoints_unreadable.cache_clear()
    yield
    _warn_endpoints_unreadable.cache_clear()


def _raw_response(body: dict[str, Any]) -> MagicMock:
    response = MagicMock()
    response.data = json.dumps(body).encode()
    return response


def _pods(*names: str, host_network: bool = False) -> dict[str, Any]:
    return {
        "items": [
            {
                "metadata": {"name": name, "uid": f"uid-{name}"},
                "spec": {"hostNetwork": host_network},
                "status": {},
            }
            for name in names
        ]
    }


def _endpoints(*items: dict[str, Any]) -> dict[str, Any]:
    return {"items": list(items)}


def _endpoint(
    name: str = POD, *, state: str = "ready", pod_uid: str | None = None
) -> dict[str, Any]:
    return {
        "metadata": {
            "name": name,
            "ownerReferences": [{"kind": "Pod", "uid": pod_uid or f"uid-{name}"}],
        },
        "status": {"state": state},
    }


@contextmanager
def fake_cluster(
    pods: dict[str, Any] | Exception,
    reads: list[dict[str, Any] | Exception],
    policies: Sequence[str | None] | Exception = (),
) -> Generator[MagicMock, None, None]:
    """Fake the Kubernetes API at the same boundary the pod reads fake it.

    `reads` is the sequence of CiliumEndpoint list responses (or errors) to
    serve, one per poll; the last is repeated for any further polls.
    `policies` are the names a CiliumNetworkPolicy list returns; None is a
    policy the API returned without a name.
    """
    core = MagicMock()
    if isinstance(pods, Exception):
        core.list_namespaced_pod.side_effect = pods
    else:
        core.list_namespaced_pod.return_value = _raw_response(pods)
    custom_objects = MagicMock()
    served = list(reads)

    def serve(*_args: Any, plural: str = "", **_kwargs: Any) -> MagicMock:
        if plural == "ciliumnetworkpolicies":
            if isinstance(policies, Exception):
                raise policies
            items = [
                {"metadata": {} if name is None else {"name": name}}
                for name in policies
            ]
            return _raw_response({"items": items})
        read = served.pop(0) if len(served) > 1 else served[0]
        if isinstance(read, Exception):
            raise read
        return _raw_response(read)

    custom_objects.list_namespaced_custom_object.side_effect = serve
    with (
        patch("k8s_sandbox._cilium.k8s_client", return_value=core),
        patch(
            "k8s_sandbox._cilium.k8s_custom_objects_client",
            return_value=custom_objects,
        ),
    ):
        yield custom_objects


async def _wait(timeout_seconds: float = 1, **kwargs: Any) -> CiliumWaitOutcome:
    return await wait_for_policy_realized(
        None,
        "namespace",
        RELEASE,
        timeout_seconds=timeout_seconds,
        poll_interval_seconds=0,
        slow_poll_interval_seconds=0,
        **kwargs,
    )


async def test_returns_when_every_endpoint_is_ready() -> None:
    with fake_cluster(_pods(POD), [_endpoints(_endpoint())]) as custom_objects:
        outcome = await _wait()

    assert outcome is CiliumWaitOutcome.REALIZED
    assert custom_objects.list_namespaced_custom_object.call_count == 1
    _, kwargs = custom_objects.list_namespaced_custom_object.call_args
    assert kwargs["group"] == "cilium.io"
    assert kwargs["version"] == "v2"
    assert kwargs["plural"] == "ciliumendpoints"
    assert kwargs["namespace"] == "namespace"
    assert kwargs["label_selector"] == f"app.kubernetes.io/instance={RELEASE}"


async def test_waits_for_an_endpoint_which_does_not_exist_yet() -> None:
    with fake_cluster(_pods(POD), [_endpoints(), _endpoints(_endpoint())]) as objects:
        outcome = await _wait()

    assert outcome is CiliumWaitOutcome.REALIZED
    assert objects.list_namespaced_custom_object.call_count == 2


async def test_waits_while_an_endpoint_is_regenerating() -> None:
    regenerating = _endpoints(_endpoint(state="regenerating"))
    with fake_cluster(_pods(POD), [regenerating, _endpoints(_endpoint())]) as objects:
        outcome = await _wait()

    assert outcome is CiliumWaitOutcome.REALIZED
    assert objects.list_namespaced_custom_object.call_count == 2


async def test_waits_for_every_pod_in_the_release() -> None:
    other = f"agent-env-{RELEASE}-victim-0"
    one_ready = _endpoints(_endpoint(), _endpoint(other, state="regenerating"))
    both_ready = _endpoints(_endpoint(), _endpoint(other))
    with fake_cluster(_pods(POD, other), [one_ready, both_ready]) as objects:
        outcome = await _wait()

    assert outcome is CiliumWaitOutcome.REALIZED
    assert objects.list_namespaced_custom_object.call_count == 2


async def test_ignores_an_endpoint_left_by_a_previous_pod_of_the_same_name() -> None:
    stale = _endpoints(_endpoint(pod_uid="uid-of-the-previous-pod"))
    with fake_cluster(_pods(POD), [stale, _endpoints(_endpoint())]) as objects:
        outcome = await _wait()

    assert outcome is CiliumWaitOutcome.REALIZED
    assert objects.list_namespaced_custom_object.call_count == 2


async def test_raises_naming_the_endpoint_and_its_state_on_timeout() -> None:
    with fake_cluster(_pods(POD), [_endpoints(_endpoint(state="regenerating"))]):
        with pytest.raises(CiliumPolicyNotRealizedError) as excinfo:
            await _wait(timeout_seconds=0)

    message = str(excinfo.value)
    assert POD in message
    assert "regenerating" in message
    assert excinfo.value.release_name == RELEASE
    assert [endpoint.name for endpoint in excinfo.value.pending] == [POD]


async def test_warns_and_skips_when_a_pod_never_gets_an_endpoint(
    caplog: LogCaptureFixture,
) -> None:
    # Cilium creates an endpoint for every pod it manages, so a pod which never
    # gets one is on a node it does not manage rather than a stuck agent.
    caplog.set_level(logging.WARNING)
    with fake_cluster(_pods(POD), [_endpoints()]):
        outcome = await _wait(timeout_seconds=0)

    assert outcome is CiliumWaitOutcome.ENDPOINTS_ABSENT
    assert len(caplog.records) == 1
    assert POD in caplog.records[0].message


async def test_recovers_from_a_transient_api_error() -> None:
    reads: list[dict[str, Any] | Exception] = [
        ApiException(status=429, reason="Too Many Requests"),
        _endpoints(_endpoint()),
    ]
    with fake_cluster(_pods(POD), reads):
        assert await _wait() is CiliumWaitOutcome.REALIZED


async def test_recovers_from_a_connection_error() -> None:
    # The kubernetes client converts only SSLError into ApiException.
    reads: list[dict[str, Any] | Exception] = [
        urllib3.exceptions.ProtocolError("connection reset"),
        _endpoints(_endpoint()),
    ]
    with fake_cluster(_pods(POD), reads):
        assert await _wait() is CiliumWaitOutcome.REALIZED


async def test_reports_an_endpoint_read_which_never_succeeds() -> None:
    with fake_cluster(_pods(POD), [ApiException(status=500, reason="Server Error")]):
        with pytest.raises(K8sError, match="CiliumEndpoints") as excinfo:
            await _wait(timeout_seconds=0)

    assert "ApiException" in str(excinfo.value)


async def test_reports_a_failed_pod_read_as_a_k8s_error() -> None:
    with fake_cluster(ApiException(status=500, reason="Server Error"), [_endpoints()]):
        with pytest.raises(K8sError, match="pods") as excinfo:
            await _wait(timeout_seconds=0)

    assert RELEASE in str(excinfo.value)
    assert not isinstance(excinfo.value, CiliumPolicyNotRealizedError)


async def test_skips_the_wait_when_pods_may_not_be_read() -> None:
    with fake_cluster(ApiException(status=403, reason="Forbidden"), [_endpoints()]):
        assert await _wait() is CiliumWaitOutcome.NOT_PERMITTED


async def test_skips_the_wait_when_the_endpoint_crd_is_absent(
    caplog: LogCaptureFixture,
) -> None:
    caplog.set_level(logging.WARNING)
    with fake_cluster(_pods(POD), [ApiException(status=404, reason="Not Found")]):
        outcome = await _wait()

    assert outcome is CiliumWaitOutcome.CRD_ABSENT
    assert caplog.records == []


async def test_warns_once_when_endpoints_may_not_be_read(
    caplog: LogCaptureFixture,
) -> None:
    caplog.set_level(logging.WARNING)
    with fake_cluster(_pods(POD), [ApiException(status=403, reason="Forbidden")]):
        first = await _wait()
        second = await _wait()

    assert first is CiliumWaitOutcome.NOT_PERMITTED
    assert second is CiliumWaitOutcome.NOT_PERMITTED
    assert len(caplog.records) == 1
    assert "ciliumendpoints.cilium.io" in caplog.records[0].message


async def test_ignores_host_networked_pods_which_have_no_endpoint() -> None:
    with fake_cluster(_pods(POD, host_network=True), [_endpoints()]) as objects:
        outcome = await _wait()

    assert outcome is CiliumWaitOutcome.REALIZED
    objects.list_namespaced_custom_object.assert_not_called()


async def test_deletes_the_releases_network_policies_one_by_one() -> None:
    # Deleting a collection is the separate `deletecollection` RBAC verb, which
    # an identity permitted to create and delete the policies may not hold.
    names = [f"agent-env-{RELEASE}-sandbox-egress", f"agent-env-{RELEASE}-svc-ingress"]
    with fake_cluster(_pods(POD), [_endpoints()], policies=names) as objects:
        await delete_release_network_policies(None, "namespace", RELEASE)

    objects.delete_collection_namespaced_custom_object.assert_not_called()
    deleted = objects.delete_namespaced_custom_object.call_args_list
    assert [call.kwargs["name"] for call in deleted] == names
    assert {call.kwargs["plural"] for call in deleted} == {"ciliumnetworkpolicies"}
    assert {call.kwargs["namespace"] for call in deleted} == {"namespace"}
    _, list_kwargs = objects.list_namespaced_custom_object.call_args
    assert list_kwargs["label_selector"] == f"app.kubernetes.io/instance={RELEASE}"


async def test_deleting_network_policies_is_a_no_op_without_the_crd() -> None:
    absent = ApiException(status=404, reason="Not Found")
    with fake_cluster(_pods(POD), [_endpoints()], policies=absent) as objects:
        await delete_release_network_policies(None, "namespace", RELEASE)

    objects.delete_namespaced_custom_object.assert_not_called()


async def test_deleting_a_policy_which_is_already_gone_is_not_an_error() -> None:
    names = ["one", "two"]
    with fake_cluster(_pods(POD), [_endpoints()], policies=names) as objects:
        objects.delete_namespaced_custom_object.side_effect = [
            ApiException(status=404, reason="Not Found"),
            None,
        ]

        await delete_release_network_policies(None, "namespace", RELEASE)

    assert objects.delete_namespaced_custom_object.call_count == 2


async def test_failing_to_list_network_policies_raises() -> None:
    forbidden = ApiException(status=403, reason="Forbidden")
    with fake_cluster(_pods(POD), [_endpoints()], policies=forbidden):
        with pytest.raises(K8sError, match="list the release's network policies"):
            await delete_release_network_policies(None, "namespace", RELEASE)


async def test_tries_every_policy_and_reports_the_ones_which_failed() -> None:
    # A policy left behind because an earlier one failed is a leak; the error
    # is the last thing to give up.
    with fake_cluster(
        _pods(POD), [_endpoints()], policies=["one", "two", "three"]
    ) as objects:
        objects.delete_namespaced_custom_object.side_effect = [
            ApiException(status=403, reason="Forbidden"),
            None,
            ApiException(status=500, reason="Server Error"),
        ]

        with pytest.raises(K8sError) as excinfo:
            await delete_release_network_policies(None, "namespace", RELEASE)

    deleted = objects.delete_namespaced_custom_object.call_args_list
    attempted = [call.kwargs["name"] for call in deleted]
    assert attempted == ["one", "two", "three"]
    message = str(excinfo.value)
    assert "Failed to delete some" in message
    assert '"policies": "one (' in message
    assert "; three (" in message


async def test_a_connection_error_while_deleting_does_not_abort_the_sweep() -> None:
    # Only SSLError reaches us as an ApiException; the rest are urllib3's.
    with fake_cluster(_pods(POD), [_endpoints()], policies=["one", "two"]) as objects:
        objects.delete_namespaced_custom_object.side_effect = [
            urllib3.exceptions.ProtocolError("connection reset"),
            None,
        ]

        with pytest.raises(K8sError, match="Failed to delete some") as excinfo:
            await delete_release_network_policies(None, "namespace", RELEASE)

    assert objects.delete_namespaced_custom_object.call_count == 2
    assert "ProtocolError" in str(excinfo.value)


async def test_a_connection_error_while_listing_is_a_k8s_error() -> None:
    reset = urllib3.exceptions.ProtocolError("connection reset")
    with fake_cluster(_pods(POD), [_endpoints()], policies=reset) as objects:
        with pytest.raises(K8sError, match="list the release's network policies"):
            await delete_release_network_policies(None, "namespace", RELEASE)

    objects.delete_namespaced_custom_object.assert_not_called()


async def test_reports_a_policy_the_api_returned_without_a_name() -> None:
    with fake_cluster(_pods(POD), [_endpoints()], policies=[None]) as objects:
        with pytest.raises(K8sError, match="no name"):
            await delete_release_network_policies(None, "namespace", RELEASE)

    objects.delete_namespaced_custom_object.assert_not_called()


@pytest_asyncio.fixture(scope="module")
async def sandbox() -> AsyncGenerator[K8sSandboxEnvironment, None]:
    async with install_sandbox_environments(__file__, None) as envs:
        yield envs["default"]


@pytest.mark.req_k8s
async def test_waits_against_the_real_cilium_crd(
    sandbox: K8sSandboxEnvironment,
) -> None:
    """Pin the group, version, plural and status field against a real cluster.

    Every other test in this file fakes the API, so a typo in any of them would
    read as 'this cluster does not run Cilium' and silently skip the wait.
    """
    release = sandbox.release

    outcome = await wait_for_policy_realized(
        release.context_name, release.namespace, release.release_name
    )

    assert outcome is CiliumWaitOutcome.REALIZED

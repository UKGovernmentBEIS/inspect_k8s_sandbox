"""Wait for Cilium to program a release's pods before they are used.

`helm install --wait` returns as soon as kubelet reports the release's pods
Ready. Cilium builds each pod's endpoint and compiles its policy separately, so
a sandbox handed back on kubelet's signal alone can run its first command
against a half-programmed egress policy: an allow-listed domain that does not
resolve, or a pod meant to have unrestricted egress with no network at all.

Cilium publishes per-pod progress as a ``CiliumEndpoint`` (``cilium.io/v2``),
named after the pod, owned by it, and carrying its labels. ``status.state`` is
``ready`` only once that endpoint's datapath is built and not waiting to
regenerate, and it is the only signal the API offers: the v2 CRD's
``status.policy`` schema declares just ``ingress``/``egress`` (an
apiextensions/v1 CRD prunes anything else), and ``CiliumNetworkPolicy.status``
carries no per-node realization at all. So the wait covers the window in which
the endpoint does not exist or is being (re)built. The chart's network policies
are installed as pre-install hooks, which puts the policy objects in the API
server before the pod is created; what remains unobservable is whether the
node's agent had imported them by the time it built that endpoint.

Reads use the raw API JSON rather than the kubernetes client's model
deserialization, as ``k8s_sandbox._pod.snapshot`` does and for the same reason.
"""

from __future__ import annotations

import asyncio
import functools
import json
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any, Sequence, cast

import urllib3
from kubernetes import client  # type: ignore
from kubernetes.client.exceptions import ApiException  # type: ignore
from urllib3 import HTTPResponse

from k8s_sandbox._error import K8sError
from k8s_sandbox._kubernetes_api import k8s_client, k8s_custom_objects_client
from k8s_sandbox._logger import log_debug, log_warn
from k8s_sandbox._pod.snapshot import PodSnapshot, list_pods

CILIUM_GROUP = "cilium.io"
CILIUM_VERSION = "v2"
CILIUM_ENDPOINTS_PLURAL = "ciliumendpoints"
CILIUM_NETWORK_POLICIES_PLURAL = "ciliumnetworkpolicies"
CILIUM_ENDPOINTS_RESOURCE = f"{CILIUM_ENDPOINTS_PLURAL}.{CILIUM_GROUP}"

READY_STATE = "ready"

# Building an endpoint is a node-local control loop which completes in
# milliseconds once the agent holds the policy, so poll fast enough that the
# wait is invisible on the happy path.
POLL_INTERVAL_SECONDS = 0.25
# Past this the endpoint is not merely mid-build, so stop listing four times a
# second per concurrent install against a cluster which is already struggling.
SLOW_POLL_AFTER_SECONDS = 2.0
SLOW_POLL_INTERVAL_SECONDS = 1.0
# A minute of it means something is broken rather than busy — and failing is
# better than handing back a sandbox whose egress rules are not in force.
TIMEOUT_SECONDS = 60

# The kubernetes client converts only SSLError into an ApiException, so a
# connection-level failure reaches us as a urllib3 or socket error.
_CONNECTION_ERRORS = (urllib3.exceptions.HTTPError, OSError)
_READ_ERRORS = (ApiException, *_CONNECTION_ERRORS)


class CiliumWaitOutcome(Enum):
    """How a wait for Cilium to program the release's pods finished."""

    REALIZED = "realized"
    """Every endpoint the release's pods have was built and ready."""

    CRD_ABSENT = "crd-absent"
    """The cluster has no CiliumEndpoint CRD, so it does not run Cilium."""

    NOT_PERMITTED = "not-permitted"
    """The cluster runs Cilium but did not permit reading CiliumEndpoints."""

    ENDPOINTS_ABSENT = "endpoints-absent"
    """Pods which never got an endpoint: a node Cilium does not manage."""


@dataclass(frozen=True)
class CiliumEndpointStatus:
    """A minimal view of one pod's CiliumEndpoint.

    ``found`` is False for a pod whose endpoint Cilium has not published yet,
    which is a state to wait through rather than a missing signal.
    """

    name: str
    found: bool
    state: str | None
    pod_uid: str | None = None

    @staticmethod
    def absent(pod_name: str) -> CiliumEndpointStatus:
        """The endpoint of a pod which Cilium has not published yet."""
        return CiliumEndpointStatus(name=pod_name, found=False, state=None)

    @property
    def is_ready(self) -> bool:
        return self.found and self.state == READY_STATE

    def describe(self) -> str:
        if not self.found:
            return f"{self.name} (no CiliumEndpoint)"
        return f"{self.name} (state {self.state or 'unknown'})"


class CiliumPolicyNotRealizedError(K8sError):
    """Cilium did not finish building a release's endpoints in time.

    The release's pods were ready, but their Cilium endpoints never became
    ready, so a command run in them would have raced its own egress rules: an
    allowed domain may not resolve, or a pod meant to have unrestricted egress
    may have no network at all.

    Typically a symptom of an unhealthy or overloaded Cilium agent on the node
    hosting the named endpoints.
    """

    def __init__(  # noqa: D107
        self,
        *,
        release_name: str,
        namespace: str,
        pending: Sequence[CiliumEndpointStatus],
        elapsed_seconds: float,
        **kwargs: Any,
    ):
        self.release_name = release_name
        self.namespace = namespace
        self.pending = tuple(pending)
        self.elapsed_seconds = elapsed_seconds
        super().__init__(
            f"Cilium did not make the endpoints of release '{release_name}' "
            f"ready within {elapsed_seconds:.1f}s. Endpoints still waiting: "
            f"{_describe(pending)}",
            release=release_name,
            namespace=namespace,
            elapsed_seconds=round(elapsed_seconds, 3),
            **kwargs,
        )


async def wait_for_policy_realized(
    context_name: str | None,
    namespace: str,
    release_name: str,
    *,
    timeout_seconds: float = TIMEOUT_SECONDS,
    poll_interval_seconds: float = POLL_INTERVAL_SECONDS,
    slow_poll_interval_seconds: float = SLOW_POLL_INTERVAL_SECONDS,
) -> CiliumWaitOutcome:
    """Wait until Cilium has built an endpoint for each of the release's pods.

    Args:
        context_name: The kubeconfig context, or None for the current one.
        namespace: The namespace the release was installed into.
        release_name: The Helm release name.
        timeout_seconds: How long to wait before raising.
        poll_interval_seconds: How long to wait between reads.
        slow_poll_interval_seconds: How long to wait between reads once the
          wait has lasted longer than a build normally takes.

    Returns:
        What the wait observed, for the caller to act on or ignore.

    Raises:
        CiliumPolicyNotRealizedError: If an endpoint of the release exists but
            has not become ready within `timeout_seconds`.
        K8sError: If the release's pods could not be read.
    """
    loop = asyncio.get_running_loop()
    started = time.monotonic()
    deadline = started + timeout_seconds
    label_selector = f"app.kubernetes.io/instance={release_name}"
    # Create the clients here and use them from the worker threads, as
    # Release.get_sandbox_pods does. k8s_client() is thread-local, so creating
    # one inside each worker re-runs the kubeconfig's credential plugin
    # (measured at ~0.9s) on the critical path of every concurrent sandbox.
    api = k8s_client(context_name)
    custom_objects = k8s_custom_objects_client(context_name)
    pods: tuple[PodSnapshot, ...] | None = None
    last_read_error: Exception | None = None
    logged_waiting = False
    while True:
        try:
            if pods is None:
                pods = await loop.run_in_executor(
                    None, lambda: _pods_with_endpoints(api, namespace, label_selector)
                )
            statuses = (
                _join(
                    pods,
                    await loop.run_in_executor(
                        None,
                        lambda: _list_endpoints(
                            custom_objects, namespace, label_selector
                        ),
                    ),
                )
                if pods
                else ()
            )
        except ApiException as e:
            if e.status == 404 and pods is not None:
                # The API 404s on the collection path itself when the CRD is
                # not registered: the cluster does not run Cilium, so there is
                # no policy to wait for. An endpoint which merely does not
                # exist yet is an empty list, not a 404.
                return _finish(CiliumWaitOutcome.CRD_ABSENT, release_name, started, ())
            if e.status in (401, 403):
                _warn_endpoints_unreadable()
                return _finish(
                    CiliumWaitOutcome.NOT_PERMITTED, release_name, started, ()
                )
            last_read_error = e
            statuses = ()
        except _CONNECTION_ERRORS as e:
            # A throttled or briefly unavailable API server may pass: keep
            # polling within the budget and report the last failure if it does
            # not.
            last_read_error = e
            statuses = ()
        pending = tuple(status for status in statuses if not status.is_ready)
        if pods is not None and not pending and last_read_error is None:
            return _finish(CiliumWaitOutcome.REALIZED, release_name, started, statuses)
        if pending and not logged_waiting:
            logged_waiting = True
            log_debug(
                "Waiting for Cilium to build the release's endpoints.",
                release=release_name,
                pending=_describe(pending),
            )
        elapsed = time.monotonic() - started
        if time.monotonic() >= deadline:
            return _timed_out(
                release_name, namespace, pods, pending, elapsed, last_read_error
            )
        last_read_error = None
        await asyncio.sleep(
            poll_interval_seconds
            if elapsed < SLOW_POLL_AFTER_SECONDS
            else slow_poll_interval_seconds
        )


async def delete_release_network_policies(
    context_name: str | None, namespace: str, release_name: str
) -> None:
    """Delete the CiliumNetworkPolicies a release installed.

    The chart installs them as pre-install hooks so that they exist before the
    pods they protect. Helm does not track hook resources as part of a release,
    so `helm uninstall` leaves them behind and they are deleted here instead.

    Deletes them one by one, as Helm itself would: deleting a collection is the
    distinct `deletecollection` RBAC verb, which an identity permitted to
    create and delete the chart's own policies does not necessarily hold. Every
    policy is attempted; a single error then names each one that failed.

    Raises:
        K8sError: If the policies could not be listed or deleted.
    """
    loop = asyncio.get_running_loop()
    custom_objects = k8s_custom_objects_client(context_name)
    label_selector = f"app.kubernetes.io/instance={release_name}"
    try:
        names = await loop.run_in_executor(
            None,
            lambda: _network_policy_names(custom_objects, namespace, label_selector),
        )
    except _READ_ERRORS as e:
        if isinstance(e, ApiException) and e.status == 404:
            # No CiliumNetworkPolicy CRD: the cluster does not run Cilium, so
            # the chart's policies were never created.
            return
        raise K8sError(
            "Failed to list the release's network policies.",
            release=release_name,
            namespace=namespace,
            cause=f"{type(e).__name__}: {e}",
        ) from e
    failures: list[str] = []
    first_failure: Exception | None = None
    for name in names:
        try:
            await loop.run_in_executor(
                None,
                functools.partial(
                    custom_objects.delete_namespaced_custom_object,
                    group=CILIUM_GROUP,
                    version=CILIUM_VERSION,
                    namespace=namespace,
                    plural=CILIUM_NETWORK_POLICIES_PLURAL,
                    name=name,
                ),
            )
        except _READ_ERRORS as e:
            if isinstance(e, ApiException) and e.status == 404:
                # Already gone: another uninstall, or the namespace being torn
                # down around us.
                continue
            # Try the rest before reporting: leaving the others behind because
            # one of them failed is a worse outcome than a longer error.
            failures.append(f"{name} ({type(e).__name__}: {e})")
            first_failure = first_failure or e
    if failures:
        raise K8sError(
            "Failed to delete some of the release's network policies.",
            release=release_name,
            namespace=namespace,
            policies="; ".join(failures),
        ) from first_failure


def _finish(
    outcome: CiliumWaitOutcome,
    release_name: str,
    started: float,
    statuses: Sequence[CiliumEndpointStatus],
) -> CiliumWaitOutcome:
    log_debug(
        "Waited for Cilium to build the release's endpoints.",
        release=release_name,
        outcome=outcome.value,
        elapsed_ms=round((time.monotonic() - started) * 1000),
        endpoints=_describe(statuses),
    )
    return outcome


def _timed_out(
    release_name: str,
    namespace: str,
    pods: tuple[PodSnapshot, ...] | None,
    pending: Sequence[CiliumEndpointStatus],
    elapsed: float,
    last_read_error: Exception | None,
) -> CiliumWaitOutcome:
    cause = (
        {"cause": f"{type(last_read_error).__name__}: {last_read_error}"}
        if last_read_error is not None
        else {}
    )
    if pods is None:
        raise K8sError(
            "Failed to list the release's pods while waiting for Cilium.",
            release=release_name,
            namespace=namespace,
            **cause,
        )
    if last_read_error is not None and not pending:
        raise K8sError(
            "Failed to read the release's CiliumEndpoints while waiting for Cilium.",
            release=release_name,
            namespace=namespace,
            **cause,
        )
    unready = tuple(status for status in pending if status.found)
    if unready or last_read_error is not None:
        raise CiliumPolicyNotRealizedError(
            release_name=release_name,
            namespace=namespace,
            pending=pending,
            elapsed_seconds=elapsed,
            **cause,
        )
    # Every pending pod simply has no endpoint. Cilium creates one for every
    # pod it manages, so after this long the likelier explanation is a node it
    # does not manage (Fargate, Windows, a cluster mid-rollout) than a stuck
    # agent, and failing the sandbox would be a regression for those clusters.
    log_warn(
        "Cilium published no endpoint for some of the release's pods, so they "
        "were not waited for. Their first commands may race their own egress "
        "rules if Cilium does manage them.",
        release=release_name,
        namespace=namespace,
        pods=_describe(pending),
    )
    return CiliumWaitOutcome.ENDPOINTS_ABSENT


def _describe(statuses: Sequence[CiliumEndpointStatus]) -> str:
    return "; ".join(status.describe() for status in statuses)


@functools.lru_cache(maxsize=1)
def _warn_endpoints_unreadable() -> None:
    """Warn once per process that the wait cannot observe Cilium.

    Once, because every sandbox in an eval hits the same missing permission and
    a warning per sandbox would bury it.
    """
    log_warn(
        f"Not permitted to read {CILIUM_ENDPOINTS_RESOURCE}, so sandboxes are "
        f"handed back without waiting for Cilium to program them. Their first "
        f"commands may race their own egress rules. Grant 'get' and 'list' on "
        f"{CILIUM_ENDPOINTS_RESOURCE} to fix this."
    )


def _pods_with_endpoints(
    api: client.CoreV1Api, namespace: str, label_selector: str
) -> tuple[PodSnapshot, ...]:
    # Host-networked pods share the node's network namespace and so have no
    # CiliumEndpoint of their own; waiting for one would never finish.
    return tuple(
        pod
        for pod in list_pods(api, namespace, label_selector=label_selector)
        if not pod.host_network
    )


def _list_cilium_objects(
    custom_objects: client.CustomObjectsApi,
    namespace: str,
    plural: str,
    label_selector: str,
) -> list[dict[str, Any]]:
    """List one of Cilium's namespaced resources as raw API JSON items.

    See k8s_sandbox._pod.snapshot for why the raw JSON, and why
    _preload_content needs an ignore.
    """
    response = cast(
        HTTPResponse,
        custom_objects.list_namespaced_custom_object(  # type: ignore[call-arg]
            group=CILIUM_GROUP,
            version=CILIUM_VERSION,
            namespace=namespace,
            plural=plural,
            label_selector=label_selector,
            _preload_content=False,
        ),
    )
    items: list[dict[str, Any]] = json.loads(response.data).get("items", [])
    return items


def _network_policy_names(
    custom_objects: client.CustomObjectsApi, namespace: str, label_selector: str
) -> tuple[str, ...]:
    names: list[str] = []
    for item in _list_cilium_objects(
        custom_objects, namespace, CILIUM_NETWORK_POLICIES_PLURAL, label_selector
    ):
        name = (item.get("metadata") or {}).get("name")
        if not isinstance(name, str):
            raise K8sError(
                "Read a CiliumNetworkPolicy with no name.",
                namespace=namespace,
                metadata=item.get("metadata"),
            )
        names.append(name)
    return tuple(names)


def _join(
    pods: Sequence[PodSnapshot], endpoints: dict[str, CiliumEndpointStatus]
) -> tuple[CiliumEndpointStatus, ...]:
    """Pair each pod with its endpoint, by pod UID rather than by name.

    Pods of the chart's StatefulSets have stable names, so an endpoint left
    behind by a previous pod of the same name would otherwise satisfy the wait
    immediately. Matching on the owning pod's UID means a stale endpoint can
    only delay the wait, never end it.
    """
    matched: list[CiliumEndpointStatus] = []
    for pod in pods:
        endpoint = endpoints.get(pod.name)
        if endpoint is None or (
            endpoint.pod_uid is not None and endpoint.pod_uid != pod.uid
        ):
            matched.append(CiliumEndpointStatus.absent(pod.name))
        else:
            matched.append(endpoint)
    return tuple(matched)


def _list_endpoints(
    custom_objects: client.CustomObjectsApi, namespace: str, label_selector: str
) -> dict[str, CiliumEndpointStatus]:
    endpoints = (
        _parse_endpoint(item)
        for item in _list_cilium_objects(
            custom_objects, namespace, CILIUM_ENDPOINTS_PLURAL, label_selector
        )
    )
    return {endpoint.name: endpoint for endpoint in endpoints}


def _parse_endpoint(item: dict[str, Any]) -> CiliumEndpointStatus:
    metadata = item.get("metadata") or {}
    status = item.get("status") or {}
    owners = metadata.get("ownerReferences") or []
    return CiliumEndpointStatus(
        name=metadata.get("name", "<unnamed>"),
        found=True,
        state=status.get("state"),
        pod_uid=next(
            (owner.get("uid") for owner in owners if owner.get("kind") == "Pod"), None
        ),
    )

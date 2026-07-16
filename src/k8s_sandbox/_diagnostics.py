from __future__ import annotations

import logging

from kubernetes.client import CoreV1Api, V1ContainerStatus  # type: ignore

from k8s_sandbox._kubernetes_api import k8s_client

logger = logging.getLogger(__name__)


def describe_release_pods(
    context_name: str | None, namespace: str, release_name: str
) -> str | None:
    """Summarise the state of a Helm release's pods for inclusion in error messages.

    Reads container states, termination reasons and exit codes from the Kubernetes API
    so that failures which Helm reports only generically (e.g. "Pod is in the Pending
    phase" or "Containers in CrashLoop state") can be attributed to a concrete cause
    such as ImagePullBackOff or OOMKilled.

    This is best-effort: it is intended to be called from error-handling paths, so any
    failure while gathering diagnostics is swallowed and results in None rather than
    masking the original error.

    Args:
        context_name: The kubeconfig context name, or None for the current context.
        namespace: The namespace the release was installed into.
        release_name: The Helm release name (used to select its pods).

    Returns:
        A human-readable, multi-line summary, or None if no useful diagnostics could be
        gathered.
    """
    try:
        return _collect_diagnostics(context_name, namespace, release_name)
    except Exception:
        logger.debug("Failed to collect pod diagnostics.", exc_info=True)
        return None


def _collect_diagnostics(
    context_name: str | None, namespace: str, release_name: str
) -> str | None:
    client = k8s_client(context_name)
    pods = client.list_namespaced_pod(
        namespace, label_selector=f"app.kubernetes.io/instance={release_name}"
    )
    lines: list[str] = []
    pod_names: set[str] = set()
    for pod in pods.items:
        if pod.metadata is None or pod.metadata.name is None:
            continue
        pod_names.add(pod.metadata.name)
        if pod.status is None:
            continue
        # Init containers run to completion before the app containers start, so a
        # failing init container leaves the app container merely "waiting
        # (PodInitializing)"; the actionable cause is in the init container's status.
        for container in pod.status.init_container_statuses or []:
            line = _describe_container(container, is_init=True)
            if line is not None:
                lines.append(line)
        for container in pod.status.container_statuses or []:
            line = _describe_container(container)
            if line is not None:
                lines.append(line)

    lines.extend(_describe_warning_events(client, namespace, pod_names))

    if not lines:
        return None
    return "\n".join(lines)


def _describe_warning_events(
    client: CoreV1Api, namespace: str, pod_names: set[str]
) -> list[str]:
    """Return formatted Warning events that involve any of the given pods."""
    events = client.list_namespaced_event(namespace, field_selector="type=Warning")
    lines: list[str] = []
    for event in events.items:
        involved = event.involved_object
        if involved is None or involved.name not in pod_names:
            continue
        lines.append(f"event ({event.reason}): {event.message}")
    return lines


def _describe_container(
    container: V1ContainerStatus, is_init: bool = False
) -> str | None:
    """Describe a single container's problematic state, or None if it looks healthy."""
    state = container.state
    last_state = container.last_state
    waiting = state.waiting if state else None
    terminated = state.terminated if state else None
    last_terminated = last_state.terminated if last_state else None

    parts: list[str] = []
    if waiting is not None:
        detail = waiting.reason or "Waiting"
        if waiting.message:
            detail += f": {waiting.message}"
        parts.append(f"waiting ({detail})")
    if terminated is not None:
        # Init containers run to completion: exit 0 is success, not a symptom.
        if is_init and terminated.exit_code == 0:
            return None
        parts.append(
            f"terminated {terminated.reason} (exit code {terminated.exit_code})"
        )
    if terminated is None and last_terminated is not None:
        # A crash-looping container is currently "waiting"; the reason it keeps dying
        # (e.g. OOMKilled, exit 137) lives in its previous termination.
        parts.append(
            f"last terminated {last_terminated.reason} "
            f"(exit code {last_terminated.exit_code})"
        )

    if not parts:
        return None

    kind = "init container" if is_init else "container"
    line = f"{kind} '{container.name}': " + "; ".join(parts)
    if container.restart_count:
        line += f", restarted {container.restart_count} time(s)"
    if container.image:
        line += f" [image: {container.image}]"
    return line

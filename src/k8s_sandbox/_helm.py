from __future__ import annotations

import asyncio
import functools
import json
import logging
import os
import re
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from typing import (
    Any,
    AsyncContextManager,
    Generator,
    Iterable,
    Iterator,
    Literal,
    NoReturn,
    Protocol,
)

import yaml
from inspect_ai.util import ExecResult, concurrency
from kubernetes.client.exceptions import ApiException  # type: ignore
from shortuuid import uuid

from k8s_sandbox._diagnostics import describe_release_pods
from k8s_sandbox._kubernetes_api import get_default_namespace, k8s_client
from k8s_sandbox._logger import (
    format_log_message,
    inspect_trace_action,
    log_debug,
    log_trace,
)
from k8s_sandbox._pod import Pod
from k8s_sandbox._pod.snapshot import PodSnapshot, list_pods

DEFAULT_CHART = Path(__file__).parent / "resources" / "helm" / "agent-env"
DEFAULT_TIMEOUT = 600  # 10 minutes
MAX_INSTALL_ATTEMPTS = 3
_READINESS_POLL_INTERVAL = 2  # seconds; the cadence `helm install --wait` used
# Without one, the client can wait on a stalled socket indefinitely.
_LIST_PODS_READ_TIMEOUT = (5, 30)  # (connect, read) seconds
INSTALL_RETRY_DELAY_SECONDS = 5
INSPECT_HELM_TIMEOUT = "INSPECT_HELM_TIMEOUT"
INSPECT_HELM_UNINSTALL_TIMEOUT = "INSPECT_HELM_UNINSTALL_TIMEOUT"
INSPECT_HELM_LABELS = "INSPECT_HELM_LABELS"
INSPECT_SANDBOX_COREDNS_IMAGE = "INSPECT_SANDBOX_COREDNS_IMAGE"
HELM_RELEASE_NOT_READY_URL = (
    "https://k8s-sandbox.aisi.org.uk/tips/troubleshooting/#helm-release-not-ready"
)
HELM_CONTEXT_DEADLINE_EXCEEDED_URL = (
    "https://k8s-sandbox.aisi.org.uk/tips/troubleshooting/"
    "#helm-context-deadline-exceeded"
)
# The label with which a chart declares a Pod to be a sandbox. Readiness and handover
# read it by the same rule, so a release cannot pass readiness while a sandbox the
# caller is about to ask for is missing.
_SERVICE_LABEL = "inspect/service"

logger = logging.getLogger(__name__)


def _get_helm_major_version() -> int | None:
    """Return the major version of the installed Helm CLI, or None on failure."""
    import subprocess as sp

    try:
        result = sp.run(
            ["helm", "version", "--short"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        version_str = result.stdout.strip().lstrip("v")
        return int(version_str.split(".")[0])
    except Exception:
        logger.warning("Failed to determine Helm version; assuming Helm 3.x.")
        return None


@functools.lru_cache(maxsize=1)
def _get_wait_flag() -> str:
    """Return the appropriate --wait flag for the installed Helm version.

    Helm 4.x uses kstatus for readiness checks (HIP-0022), which treats pods
    that cannot be scheduled within 15 seconds as permanently failed. This
    breaks workloads that depend on cluster autoscaling or resource queuing.
    Using --wait=legacy on Helm 4.x reverts to the Helm 3 wait behavior which
    correctly respects --timeout.

    See: https://helm.sh/community/hips/hip-0022/
    See: https://github.com/kubernetes-sigs/cli-utils/blob/master/pkg/kstatus/status/core.go
    """
    major = _get_helm_major_version()
    if major is not None and major >= 4:
        return "--wait=legacy"
    return "--wait"


class _ResourceQuotaModifiedError(Exception):
    pass


def validate_no_null_values(values: dict[str, Any], source_description: str) -> None:
    """Validate that the values dict does not contain any null values.

    Helm filters out null values from maps during template processing, which can
    cause unexpected behavior. This function checks for null values and raises an
    error with instructions to replace them with empty objects.

    Args:
        values: The values dictionary to validate.
        source_description: A description of the source (e.g., file path) for
            error messages.

    Raises:
        ValueError: If any null values are found in the values.
    """

    def find_null_paths(obj: Any, path: str = "") -> list[str]:
        """Recursively find all paths to null values in the object."""
        null_paths = []

        if isinstance(obj, dict):
            for key, value in obj.items():
                current_path = f"{path}.{key}" if path else key
                if value is None:
                    null_paths.append(current_path)
                else:
                    null_paths.extend(find_null_paths(value, current_path))
        elif isinstance(obj, list):
            for i, item in enumerate(obj):
                current_path = f"{path}[{i}]"
                if item is None:
                    null_paths.append(current_path)
                else:
                    null_paths.extend(find_null_paths(item, current_path))

        return null_paths

    null_paths = find_null_paths(values)

    if null_paths:
        paths_str = "\n  - ".join(null_paths)
        raise ValueError(
            f"The values from '{source_description}' contain null values at the "
            f"following paths:\n  - {paths_str}\n\n"
            f"Helm filters out null values from maps during template processing, which "
            f"causes them to be ignored. Please replace null values with empty objects "
            f"{{}} instead.\n\n"
            f"For example, change:\n"
            f"  volumes:\n"
            f"    shared:  # null\n\n"
            f"To:\n"
            f"  volumes:\n"
            f"    shared: {{}}"
        )


class ValuesSource(Protocol):
    """A protocol for classes which provide Helm values files.

    Uses a context manager to support temporarily generating values files only when
    needed, and ensuring they're cleaned up afterwards.
    """

    def __init__(self, file: Path) -> None:
        pass

    @contextmanager
    def values_file(self) -> Generator[Path | None, None, None]:
        pass

    @staticmethod
    def none() -> ValuesSource:
        """A ValuesSource which provides no values file."""
        return StaticValuesSource(None)


class StaticValuesSource(ValuesSource):
    """A ValuesSource which uses a static file."""

    def __init__(self, file: Path | None) -> None:
        self._file = file

    @contextmanager
    def values_file(self) -> Generator[Path | None, None, None]:
        yield self._file


class Release:
    """A release of a Helm chart."""

    def __init__(
        self,
        task_name: str,
        chart_path: Path | None,
        values_source: ValuesSource,
        context_name: str | None,
        restarted_container_behavior: Literal["warn", "raise"] = "warn",
        sample_uuid: str | None = None,
        extra_values: dict[str, str] | None = None,
    ) -> None:
        self.task_name = task_name
        self._chart_path = chart_path or DEFAULT_CHART
        self._values_source = values_source
        self._context_name = context_name
        self._namespace = get_default_namespace(context_name)
        # The release name is used in pod names too, so limit it to 8 chars.
        self.release_name = self._generate_release_name()
        self.restarted_container_behavior = restarted_container_behavior
        self.sample_uuid = sample_uuid
        self._extra_values = dict(extra_values) if extra_values else {}
        # The sandboxes the rendered chart declares; set by _install().
        self._expected_services: frozenset[str] = frozenset()
        # The pods readiness confirmed; set by install() and consumed by
        # get_sandbox_pods(), so nothing changes between the verdict and handover.
        self._ready_pods: list[PodSnapshot] | None = None

    @property
    def namespace(self) -> str:
        return self._namespace

    @property
    def context_name(self) -> str | None:
        return self._context_name

    def _generate_release_name(self) -> str:
        return uuid().lower()[:8]

    async def install(self) -> None:
        try:
            with inspect_trace_action(
                "K8s install Helm chart",
                chart=self._chart_path,
                release=self.release_name,
                namespace=self._namespace,
                task=self.task_name,
            ):
                # The permit bounds the submission burst, not how long the cluster
                # then takes to find capacity: holding it across the wait would let a
                # release queued behind unavailable capacity stop one whose capacity
                # is available from being submitted at all.
                async with _install_semaphore():
                    # INSPECT_HELM_TIMEOUT bounds submission and readiness together.
                    deadline = time.monotonic() + _get_timeout()
                    with self._values_source.values_file() as values:
                        log_trace(
                            "K8s Helm values", release=self.release_name, values=values
                        )
                        attempt = 1
                        while True:
                            try:
                                await self._install(
                                    values, deadline, upgrade=attempt > 1
                                )
                                break
                            except _ResourceQuotaModifiedError:
                                if attempt >= MAX_INSTALL_ATTEMPTS:
                                    raise
                                attempt += 1
                                await asyncio.sleep(INSTALL_RETRY_DELAY_SECONDS)
                self._ready_pods = await self._wait_until_ready(deadline)
        except asyncio.CancelledError:
            # When an eval is cancelled (either by user or by an error), the timing of
            # uninstall operations can be interleaved with existing `helm install`
            # processes. Uninstall the release now that we know the install process has
            # terminated.
            log_trace(
                "Helm install was cancelled; uninstalling.", release=self.release_name
            )
            await self.uninstall(quiet=True)
            raise

    async def uninstall(self, quiet: bool) -> None:
        await uninstall(self.release_name, self._namespace, self._context_name, quiet)

    async def get_sandbox_pods(self) -> dict[str, Pod]:
        """The release's sandboxes, as confirmed ready by install()."""
        pods = self._ready_pods
        if pods is None:
            try:
                pods = await asyncio.to_thread(self._list_release_pods)
            except ApiException as e:
                _raise_runtime_error(
                    "Failed to list pods.", release=self.release_name, from_exception=e
                )
        if not pods:
            _raise_runtime_error("No pods found.", release=self.release_name)
        sandboxes = dict()
        for pod in _sandbox_pods(pods):
            default_container_name = pod.container_names[0]
            sandboxes[pod.labels[_SERVICE_LABEL]] = Pod(
                pod.name,
                self._namespace,
                self._context_name,
                default_container_name,
                pod.uid,
                pod.restart_count_for(default_container_name),
                self.restarted_container_behavior,
            )
        return sandboxes

    async def _install(
        self, values: Path | None, deadline: float, upgrade: bool
    ) -> None:
        # Whilst `upgrade --install` could always be used, prefer explicitly using
        # `install` for the first attempt.
        subcommand = ["upgrade", "--install"] if upgrade else ["install"]
        values_args = ["--values", str(values)] if values else []
        result = await _run_subprocess(
            "helm",
            subcommand
            + [
                self.release_name,
                str(self._chart_path),
                f"--namespace={self._namespace}",
                *(
                    ["--create-namespace"]
                    if os.getenv("INSPECT_HELM_CREATE_NAMESPACE", "false").lower()
                    in {"1", "true", "yes", "y"}
                    else []
                ),
                # What is left of our budget, so that a retry cannot overrun it.
                f"--timeout={max(int(deadline - time.monotonic()), 1)}s",
                # Returns the rendered manifest, which is where the pod count for the
                # readiness wait comes from.
                "--output=json",
                # Annotation do not have strict length reqs. Quoting/escaping
                # handled by asyncio.create_subprocess_exec.
                f"--set=annotations.inspectTaskName={self.task_name}",
                # Include a label to identify releases created by Inspect.
                _labels_arg(),
            ]
            + (
                [f"--set=labels.inspectSampleUUID={self.sample_uuid}"]
                if self.sample_uuid
                else []
            )
            + _coredns_image_args()
            + [
                f"--set-string={_helm_escape(k)}={_helm_escape(v)}"
                for k, v in self._extra_values.items()
            ]
            + _kubeconfig_context_args(self._context_name)
            + values_args,
            capture_output=True,
        )
        if not result.success:
            await self._raise_install_error(result)
        self._expected_services = _get_expected_services(
            result.stdout, self.release_name
        )

    async def _raise_install_error(self, result: ExecResult[str]) -> NoReturn:
        # When concurrent helm operations are modifying the same resource quota, the
        # following error occasionally occurs. Retry.
        if re.search(
            r"Operation cannot be fulfilled on resourcequotas \".*\": the object has "
            r"been modified; please apply your changes to the latest version and try "
            r"again",
            result.stderr,
        ):
            log_trace(
                "resourcequota modified error whilst installing helm chart.",
                release=self.release_name,
                error=result.stderr,
            )
            raise _ResourceQuotaModifiedError(result.stderr)
        extra = await self._pod_diagnostics()
        if re.search(r"context deadline exceeded", result.stderr):
            _raise_runtime_error(
                f"Helm install timed out (context deadline exceeded). The configured "
                f"timeout value was {_get_timeout()}s. Please see the docs for why "
                f"this might occur: {HELM_CONTEXT_DEADLINE_EXCEEDED_URL}. Also "
                f"consider increasing the timeout by setting the "
                f"{INSPECT_HELM_TIMEOUT} environment variable.",
                release=self.release_name,
                result=result,
                **extra,
            )
        _raise_runtime_error(
            "Helm install failed.",
            release=self.release_name,
            result=result,
            **extra,
        )

    async def _pod_diagnostics(self) -> dict[str, Any]:
        """The release's container states. Empty if they cannot be gathered."""
        # Helm reports only the generic symptom, so name the concrete cause:
        # ImagePullBackOff, OOMKilled, FailedScheduling, ...
        diagnostics = await asyncio.to_thread(
            describe_release_pods,
            self._context_name,
            self._namespace,
            self.release_name,
        )
        return {"pod_diagnostics": diagnostics} if diagnostics else {}

    def _list_release_pods(self, allow_stale: bool = False) -> list[PodSnapshot]:
        """The release's pods. `allow_stale` reads the API server's watch cache."""
        return list_pods(
            k8s_client(self._context_name),
            self._namespace,
            label_selector=f"app.kubernetes.io/instance={self.release_name}",
            resource_version="0" if allow_stale else None,
            request_timeout=_LIST_PODS_READ_TIMEOUT,
        )

    def _missing_services(self, pods: list[PodSnapshot]) -> frozenset[str]:
        """The declared sandboxes which no pod is yet serving."""
        return self._expected_services - {
            pod.labels[_SERVICE_LABEL] for pod in _sandbox_pods(pods) if pod.ready
        }

    def _is_ready(self, pods: list[PodSnapshot]) -> bool:
        """Whether `pods` means the release is ready to be handed to a caller."""
        # A terminally failed pod is replaced by its controller; a bare one that
        # cannot be replaced just leaves its sandbox missing until the deadline.
        live = [pod for pod in pods if pod.phase != "Failed"]
        return not self._missing_services(pods) and all(map(_pod_ready, live))

    async def _wait_until_ready(self, deadline: float) -> list[PodSnapshot]:
        """Wait for the release's pods to report Ready.

        Pods are the signal Helm reduces every kind to (`pkg/kube/ready.go`), and one
        cannot be Ready until its init containers finish and its volumes are bound.
        """
        saw_a_pod = False
        poll_error: Exception | None = None
        while True:
            try:
                pods = await asyncio.to_thread(self._list_release_pods, True)
                saw_a_pod = saw_a_pod or bool(pods)
                # The cache can lag reality in either direction, so confirm against a
                # consistent read and hand those same pods on: nothing may change
                # between the verdict and the sandbox the caller gets. One read per
                # install, not per poll.
                if self._is_ready(pods):
                    confirmed = await asyncio.to_thread(self._list_release_pods)
                    if self._is_ready(confirmed):
                        return confirmed
            except Exception as e:
                # A blip must not fail an install. Keep the error: a persistent one
                # (a missing RBAC rule, say) is not a capacity problem and must not
                # be reported as one.
                log_debug("Readiness poll failed; will retry.", error=e)
                poll_error = e
            else:
                poll_error = None
            if time.monotonic() >= deadline:
                await self._raise_not_ready_error(saw_a_pod, poll_error)
            await asyncio.sleep(_READINESS_POLL_INTERVAL)

    async def _raise_not_ready_error(
        self, saw_a_pod: bool, poll_error: Exception | None
    ) -> NoReturn:
        extra = await self._pod_diagnostics()
        budget = f"{_get_timeout()}s"
        if poll_error is not None and not saw_a_pod:
            # Nothing is known about what the release created, so blaming the chart
            # or the cluster would be a guess.
            _raise_runtime_error(
                f"Could not read the Helm release's pods within {budget}, so it is "
                f"not known whether the sandbox started. See "
                f"{HELM_RELEASE_NOT_READY_URL}.",
                release=self.release_name,
                from_exception=poll_error,
                **extra,
            )
        if not saw_a_pod:
            _raise_runtime_error(
                f"The Helm release created no pods labelled "
                f"app.kubernetes.io/instance={self.release_name} within {budget}. "
                f"Every chart must render at least one Pod, or a controller which "
                f"creates one, carrying that label. See {HELM_RELEASE_NOT_READY_URL}.",
                release=self.release_name,
                **extra,
            )
        _raise_runtime_error(
            f"Helm release did not become ready within {budget}. Please see the docs "
            f"for why this might occur: {HELM_RELEASE_NOT_READY_URL}. Also consider "
            f"increasing the timeout by setting the {INSPECT_HELM_TIMEOUT} "
            f"environment variable.",
            release=self.release_name,
            declared_sandboxes=", ".join(sorted(self._expected_services)),
            **extra,
        )


async def uninstall(
    release_name: str, namespace: str, context_name: str | None, quiet: bool
) -> None:
    """
    Uninstall a Helm release by name.

    The number of concurrent uninstall operations is limited by a semaphore.

    "Release not found" errors are ignored.

    Args:
        release_name: The name of the Helm release to uninstall (e.g. abcdefgh).
        namespace: The Kubernetes namespace in which the release is installed.
        context_name: The kubeconfig context in which to run the `helm uninstall`
          command. If None, the current context is used.
        quiet: If False, allow the output of the `helm uninstall` command to be written
          to this process's stdout/stderr. If True, suppress the output.
    """
    async with _uninstall_semaphore():
        with inspect_trace_action(
            "K8s uninstall Helm chart", release=release_name, namespace=namespace
        ):
            result = await _run_subprocess(
                "helm",
                [
                    "uninstall",
                    release_name,
                    "--namespace",
                    namespace,
                    _get_wait_flag(),
                    "--timeout",
                    f"{_get_uninstall_timeout()}s",
                    # A helm uninstall failure with "release not found" implies that the
                    # release was never successfully installed or has already been
                    # uninstalled. When a helm release fails to install (or the user
                    # cancels the eval), this uninstall function will still be called,
                    # so these errors are common and result in error desensitisation.
                    "--ignore-not-found",
                ]
                + _kubeconfig_context_args(context_name),
                capture_output=True,
            )
            if not quiet:
                sys.stdout.write(result.stdout)
                sys.stderr.write(result.stderr)
            if not result.success:
                _raise_runtime_error(
                    "Helm uninstall failed.",
                    release=release_name,
                    namespace=namespace,
                    returncode=result.returncode,
                    stdout=result.stdout,
                    stderr=result.stderr,
                )


async def get_all_release_names(namespace: str, context_name: str | None) -> list[str]:
    result = await _run_subprocess(
        "helm",
        [
            "list",
            "--namespace",
            namespace,
            "-q",
            "--selector",
            "inspectSandbox=true",
            "--max",
            "0",
        ]
        + _kubeconfig_context_args(context_name),
        capture_output=True,
    )
    return result.stdout.splitlines()


def _raise_runtime_error(
    message: str, from_exception: Exception | None = None, **kwargs: Any
) -> NoReturn:
    formatted = format_log_message(message, **kwargs)
    logger.error(formatted)
    if from_exception:
        raise RuntimeError(formatted) from from_exception
    else:
        raise RuntimeError(formatted)


def _helm_escape(value: str) -> str:
    r"""Escape special characters for Helm's strvals parser.

    Helm's ``--set`` / ``--set-string`` flags use a custom parser that treats
    ``\\``, ``,``, ``.`` and ``=`` as metacharacters.  Each must be escaped
    with a leading backslash so the value is passed through literally.
    """
    # Order matters: escape backslashes first to avoid double-escaping.
    value = value.replace("\\", "\\\\")
    value = value.replace(",", "\\,")
    value = value.replace(".", "\\.")
    value = value.replace("=", "\\=")
    return value


async def _run_subprocess(
    cmd: str, args: list[str], capture_output: bool
) -> ExecResult[str]:
    try:
        proc = await asyncio.create_subprocess_exec(
            cmd,
            *args,
            stdout=asyncio.subprocess.PIPE if capture_output else None,
            stderr=asyncio.subprocess.PIPE if capture_output else None,
        )
        stdout, stderr = await proc.communicate()
    except asyncio.CancelledError:
        try:
            proc.terminate()
            # Use communicate() over wait() to avoid potential deadlock
            # https://docs.python.org/3/library/asyncio-subprocess.html#asyncio.subprocess.Process.wait
            await proc.communicate()
        # Task may have been cancelled before proc was assigned.
        except UnboundLocalError:
            pass
        # Process may have already naturally terminated.
        except ProcessLookupError:
            pass
        raise
    return ExecResult(
        success=proc.returncode == 0,
        returncode=proc.returncode or 1,
        stdout=stdout.decode() if stdout else "",
        stderr=stderr.decode() if stderr else "",
    )


def _get_timeout() -> int:
    return _get_positive_environ_int(INSPECT_HELM_TIMEOUT, DEFAULT_TIMEOUT)


def _get_uninstall_timeout() -> int:
    # Separate from INSPECT_HELM_TIMEOUT, which is legitimately set to hours: an
    # uninstall holds an uninstall permit and, on the error path, a sandbox slot.
    return _get_positive_environ_int(INSPECT_HELM_UNINSTALL_TIMEOUT, DEFAULT_TIMEOUT)


def _install_semaphore() -> AsyncContextManager[object]:
    # Limit concurrent subprocess calls to `helm install` and `helm uninstall`.
    # Use distinct semaphores for each operation to avoid deadlocks where all permits
    # are acquired by the "install" operations which are waiting for cluster resources
    # to be released by the "uninstall" operations.
    # Use Inspect's concurrency function as this ensures each asyncio.Semaphore is
    # unique per event loop.
    return concurrency("helm-install", _get_environ_int("INSPECT_MAX_HELM_INSTALL", 8))


def _uninstall_semaphore() -> AsyncContextManager[object]:
    return concurrency(
        "helm-uninstall", _get_environ_int("INSPECT_MAX_HELM_UNINSTALL", 8)
    )


def _get_positive_environ_int(name: str, default: int) -> int:
    value = _get_environ_int(name, default)
    if value <= 0:
        raise ValueError(f"{name} must be a positive int: '{value}'.")
    return value


def _get_environ_int(name: str, default: int) -> int:
    try:
        return int(os.environ[name])
    except KeyError:
        return default
    except ValueError as e:
        raise ValueError(f"{name} must be an int: '{os.environ[name]}'.") from e


def _labels_arg() -> str:
    """Formats a single --labels argument combining default and user-specified labels.

    Combines the default inspectSandbox=true label with any extra labels from the
    INSPECT_HELM_LABELS environment variable. INSPECT_HELM_LABELS should be a
    comma-separated list of key=value pairs, e.g. ``ci-branch=my-feature,run-id=42``.
    These are added as Helm release labels, queryable via
    ``helm list --selector key=value``.
    """
    extra = os.getenv(INSPECT_HELM_LABELS)
    if extra:
        for item in extra.split(","):
            key = item.split("=", maxsplit=1)[0]
            if key == "inspectSandbox":
                raise ValueError(
                    f"{INSPECT_HELM_LABELS} must not set the 'inspectSandbox' label "
                    f"(it is always set to 'true' automatically)."
                )
        labels = extra + ",inspectSandbox=true"
    else:
        labels = "inspectSandbox=true"
    return f"--labels={labels}"


def _coredns_image_args() -> list[str]:
    """Formats --set argument for coredns image override if configured via env var."""
    image = os.getenv(INSPECT_SANDBOX_COREDNS_IMAGE)
    if image is None:
        return []
    return [f"--set-string=corednsImage={_helm_escape(image)}"]


def _kubeconfig_context_args(context_name: str | None) -> list[str]:
    """Formats --kube-context arguments suitable for passing to a `helm` subprocess."""
    if context_name is None:
        return []
    return ["--kube-context", context_name]


def _pod_ready(pod: PodSnapshot) -> bool:
    """Helm's `isPodReady`, plus the completed-pod case Helm's own check gets wrong."""
    # A pod that ran to completion reports Ready=False with reason PodCompleted, so
    # Helm blocks on it until the timeout. The Ready condition rather than
    # containerStatuses[].ready, because it also accounts for readiness gates.
    return pod.phase == "Succeeded" or pod.ready


def _sandbox_pods(pods: Iterable[PodSnapshot]) -> Iterator[PodSnapshot]:
    """The pods a chart has declared to be sandboxes. Others are not handed over."""
    return (pod for pod in pods if _SERVICE_LABEL in pod.labels)


def _get_expected_services(install_stdout: str, release_name: str) -> frozenset[str]:
    """The sandboxes the manifest Helm rendered declares.

    Named rather than counted, so that readiness is stated in the vocabulary
    `get_sandbox_pods()` hands over and no other pod of the release can satisfy it.

    Raises RuntimeError if Helm's output cannot be read, or if the chart declares no
    sandbox: treating either as "nothing to wait for" is how a release comes to be
    handed over before its pods exist.
    """
    try:
        manifest = json.loads(install_stdout)["manifest"]
        # safe_load_all is lazy, so force the parse inside the try.
        docs = list(yaml.safe_load_all(manifest))
    except Exception as e:
        _raise_runtime_error(
            "Could not read the rendered manifest from Helm, so the sandboxes to wait "
            "for are unknown.",
            release=release_name,
            from_exception=e,
        )
    services = frozenset(_declared_services(docs))
    if not services:
        _raise_runtime_error(
            f"The rendered chart declares no Pod labelled '{_SERVICE_LABEL}', so it "
            f"provides no sandbox. See {HELM_RELEASE_NOT_READY_URL}.",
            release=release_name,
        )
    return services


def _declared_services(docs: Iterable[Any]) -> Iterator[str]:
    """The sandbox names on the pod-producing parts of a rendered manifest."""
    # Only a bare Pod and a pod template make pods. The same label also appears on a
    # controller's own metadata, and on Services and network policies which select
    # pods -- reading those would invent sandboxes that never arrive.
    for doc in docs:
        kind = _dig(doc, "kind")
        if kind == "Pod":
            yield from _service_name(_dig(doc, "metadata"))
        elif kind == "List":
            yield from _declared_services(_dig(doc, "items") or [])
        elif kind == "CronJob":
            yield from _service_name(
                _dig(doc, "spec", "jobTemplate", "spec", "template", "metadata")
            )
        else:
            yield from _service_name(_dig(doc, "spec", "template", "metadata"))


def _service_name(metadata: Any) -> Iterator[str]:
    name = _dig(metadata, "labels", _SERVICE_LABEL)
    if name is not None:
        # Kubernetes label values are strings, whatever the rendered YAML types them as.
        yield str(name)


def _dig(value: Any, *keys: str) -> Any:
    """`value[k1][k2]...`, or None as soon as the shape stops being a mapping."""
    for key in keys:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value

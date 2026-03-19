from __future__ import annotations

import asyncio
import functools
import logging
import os
import re
import sys
from contextlib import contextmanager, suppress
from pathlib import Path
from typing import Any, AsyncContextManager, Generator, Literal, NoReturn, Protocol

from inspect_ai.util import ExecResult, concurrency
from kubernetes.client.exceptions import ApiException  # type: ignore
from shortuuid import uuid

from k8s_sandbox._kubernetes_api import get_default_namespace, k8s_client
from k8s_sandbox._logger import (
    format_log_message,
    inspect_trace_action,
    log_debug,
    log_trace,
)
from k8s_sandbox._pod import Pod

DEFAULT_CHART = Path(__file__).parent / "resources" / "helm" / "agent-env"
DEFAULT_TIMEOUT = 600  # 10 minutes
MAX_INSTALL_ATTEMPTS = 3
_SCHEDULING_POLL_INTERVAL = 10  # seconds between k8s event polls during helm install
INSTALL_RETRY_DELAY_SECONDS = 5
INSPECT_HELM_TIMEOUT = "INSPECT_HELM_TIMEOUT"
INSPECT_HELM_LABELS = "INSPECT_HELM_LABELS"
INSPECT_SANDBOX_COREDNS_IMAGE = "INSPECT_SANDBOX_COREDNS_IMAGE"
HELM_CONTEXT_DEADLINE_EXCEEDED_URL = (
    "https://k8s-sandbox.aisi.org.uk/tips/troubleshooting/"
    "#helm-context-deadline-exceeded"
)

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

    def _generate_release_name(self) -> str:
        return uuid().lower()[:8]

    async def install(self) -> None:
        try:
            async with _install_semaphore():
                with self._values_source.values_file() as values:
                    with inspect_trace_action(
                        "K8s install Helm chart",
                        chart=self._chart_path,
                        release=self.release_name,
                        values=values,
                        namespace=self._namespace,
                        task=self.task_name,
                    ):
                        attempt = 1
                        while True:
                            try:
                                await self._install(values, upgrade=attempt > 1)
                                break
                            except _ResourceQuotaModifiedError:
                                if attempt >= MAX_INSTALL_ATTEMPTS:
                                    raise
                                attempt += 1
                                await asyncio.sleep(INSTALL_RETRY_DELAY_SECONDS)
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
        client = k8s_client(self._context_name)
        loop = asyncio.get_running_loop()
        try:
            pods = await loop.run_in_executor(
                None,
                lambda: client.list_namespaced_pod(
                    self._namespace,
                    label_selector=f"app.kubernetes.io/instance={self.release_name}",
                ),
            )
        except ApiException as e:
            _raise_runtime_error(
                "Failed to list pods.", release=self.release_name, from_exception=e
            )
        if not pods.items:
            _raise_runtime_error("No pods found.", release=self.release_name)
        sandboxes = dict()
        for pod in pods.items:
            assert pod.metadata is not None
            assert pod.metadata.labels is not None
            assert pod.spec is not None
            assert pod.status is not None
            assert pod.status.container_statuses is not None
            service_name = pod.metadata.labels.get("inspect/service")
            # Depending on the Helm chart, some Pods may not have a service label.
            # These should not be considered to be a sandbox pod (as per our docs).
            if service_name is not None:
                default_container_name = pod.spec.containers[0].name
                default_container_restart_count = next(
                    (
                        container_status.restart_count
                        for container_status in pod.status.container_statuses
                        if container_status.name == default_container_name
                    ),
                    0,
                )
                assert pod.metadata.name is not None
                assert pod.metadata.uid is not None
                sandboxes[service_name] = Pod(
                    pod.metadata.name,
                    self._namespace,
                    self._context_name,
                    default_container_name,
                    pod.metadata.uid,
                    default_container_restart_count,
                    self.restarted_container_behavior,
                )
        return sandboxes

    async def _install(self, values: Path | None, upgrade: bool) -> None:
        # Whilst `upgrade --install` could always be used, prefer explicitly using
        # `install` for the first attempt.
        subcommand = ["upgrade", "--install"] if upgrade else ["install"]
        values_args = ["--values", str(values)] if values else []
        watcher = asyncio.create_task(self._watch_for_scheduling_events())
        try:
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
                    _get_wait_flag(),
                    f"--timeout={_get_timeout()}s",
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
        finally:
            watcher.cancel()
            # Watcher is best-effort; never let its exceptions mask Helm output.
            # CancelledError is also suppressed explicitly: it's a BaseException in
            # Python 3.8+, so suppress(Exception) alone won't catch it.
            with suppress(Exception, asyncio.CancelledError):
                await watcher
        if not result.success:
            self._raise_install_error(result)

    async def _watch_for_scheduling_events(self) -> None:
        """Poll for FailedScheduling events and log once if GPU provisioning is needed.

        Runs concurrently with the helm install subprocess. Degrades silently if the
        k8s API is unavailable — it must never cause an install to fail.
        """
        loop = asyncio.get_running_loop()
        try:
            k8s = k8s_client(self._context_name)
        except Exception as e:
            log_debug(
                "Could not initialise k8s client for scheduling watcher.", error=e
            )
            return
        logged = False
        try:
            while not logged:
                await asyncio.sleep(_SCHEDULING_POLL_INTERVAL)
                try:
                    events = await loop.run_in_executor(
                        None,
                        lambda: k8s.list_namespaced_event(
                            self._namespace,
                            field_selector="reason=FailedScheduling",
                        ),
                    )
                except Exception as e:
                    log_debug("Failed to poll scheduling events.", error=e)
                    return
                for event in events.items:
                    obj = event.involved_object
                    if obj.name and self.release_name in obj.name:
                        msg = event.message or ""
                        if "nvidia.com/gpu" in msg:
                            logger.warning(
                                f"K8s: No GPU node is currently available for Helm "
                                f"release '{self.release_name}'. A new GPU node may be "
                                f"provisioning — this can take several minutes."
                            )
                            logged = True
                            break
        except asyncio.CancelledError:
            pass

    def _raise_install_error(self, result: ExecResult[str]) -> NoReturn:
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
        if re.search(r"context deadline exceeded", result.stderr):
            _raise_runtime_error(
                f"Helm install timed out (context deadline exceeded). The configured "
                f"timeout value was {_get_timeout()}s. Please see the docs for why "
                f"this might occur: {HELM_CONTEXT_DEADLINE_EXCEEDED_URL}. Also "
                f"consider increasing the timeout by setting the "
                f"{INSPECT_HELM_TIMEOUT} environment variable.",
                release=self.release_name,
                result=result,
            )
        _raise_runtime_error(
            "Helm install failed.", release=self.release_name, result=result
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
                    f"{_get_timeout()}s",
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
    timeout = _get_environ_int(INSPECT_HELM_TIMEOUT, DEFAULT_TIMEOUT)
    if timeout <= 0:
        raise ValueError(f"{INSPECT_HELM_TIMEOUT} must be a positive int: '{timeout}'.")
    return timeout


def _install_semaphore() -> AsyncContextManager[None]:
    # Limit concurrent subprocess calls to `helm install` and `helm uninstall`.
    # Use distinct semaphores for each operation to avoid deadlocks where all permits
    # are acquired by the "install" operations which are waiting for cluster resources
    # to be released by the "uninstall" operations.
    # Use Inspect's concurrency function as this ensures each asyncio.Semaphore is
    # unique per event loop.
    return concurrency("helm-install", _get_environ_int("INSPECT_MAX_HELM_INSTALL", 8))


def _uninstall_semaphore() -> AsyncContextManager[None]:
    return concurrency(
        "helm-uninstall", _get_environ_int("INSPECT_MAX_HELM_UNINSTALL", 8)
    )


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

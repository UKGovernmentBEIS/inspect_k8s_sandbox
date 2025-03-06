import asyncio
import logging
import os
import re
import sys
from pathlib import Path
from typing import Any, AsyncContextManager, NoReturn

from inspect_ai.util import ExecResult, concurrency, display_counter
from kubernetes.client.rest import ApiException  # type: ignore
from shortuuid import uuid

from k8s_sandbox._kubernetes_api import (
    get_current_context_namespace,
    k8s_client,
)
from k8s_sandbox._logger import (
    format_log_message,
    inspect_trace_action,
    log_message,
    log_trace,
)
from k8s_sandbox._pod import Pod

DEFAULT_CHART = Path(__file__).parent / "resources" / "helm" / "agent-env"
DEFAULT_TIMEOUT = 600  # 10 minutes
MAX_INSTALL_ATTEMPTS = 5
INSTALL_RETRY_DELAY_SECONDS = 5
INSPECT_HELM_TIMEOUT = "INSPECT_HELM_TIMEOUT"
HELM_CONTEXT_DEADLINE_EXCEEDED_URL = (
    "https://k8s-sandbox.ai-safety-institute.org.uk/tips/troubleshooting/"
    "#helm-context-deadline-exceeded"
)

logger = logging.getLogger(__name__)
resource_quota_exceeded_counter = 0


class _ResourceQuotaModifiedError(Exception):
    pass


class _ResourceQuotaExceededError(Exception):
    pass


class Release:
    """A release of a Helm chart."""

    def __init__(
        self,
        task_name: str,
        chart_path: Path | None = None,
        values_path: Path | None = None,
    ) -> None:
        self.task_name = task_name
        self._chart_path = chart_path or DEFAULT_CHART
        self._values_path = values_path
        self._namespace = get_current_context_namespace()
        # The release name is used in pod names too, so limit it to 8 chars.
        self.release_name = self._generate_release_name()

    def _generate_release_name(self) -> str:
        return uuid().lower()[:8]

    async def install(self) -> None:
        async with _install_semaphore():
            with inspect_trace_action(
                "K8s install Helm chart",
                chart=self._chart_path,
                release=self.release_name,
                values=self._values_path,
                namespace=self._namespace,
                task=self.task_name,
            ):
                attempt = 1
                retry_delay = INSTALL_RETRY_DELAY_SECONDS
                while True:
                    try:
                        await self._install(upgrade=attempt > 1)
                        break
                    except (_ResourceQuotaModifiedError, _ResourceQuotaExceededError):
                        if attempt >= MAX_INSTALL_ATTEMPTS:
                            raise
                        attempt += 1
                        await asyncio.sleep(retry_delay)
                        retry_delay *= 2

    async def uninstall(self, quiet: bool) -> None:
        await uninstall(self.release_name, self._namespace, quiet)

    async def get_sandbox_pods(self) -> dict[str, Pod]:
        client = k8s_client()
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
            service_name = pod.metadata.labels.get("inspect/service")
            # Depending on the Helm chart, some Pods may not have a service label.
            # These should not be considered to be a sandbox pod (as per our docs).
            if service_name is not None:
                default_container_name = pod.spec.containers[0].name
                sandboxes[service_name] = Pod(
                    pod.metadata.name, self._namespace, default_container_name
                )
        return sandboxes

    async def _install(self, upgrade: bool) -> None:
        # Whilst `upgrade --install` could always be used, prefer explicitly using
        # `install` for the first attempt.
        subcommand = ["upgrade", "--install"] if upgrade else ["install"]
        values = ["--values", str(self._values_path)] if self._values_path else []
        result = await _run_subprocess(
            "helm",
            subcommand
            + [
                self.release_name,
                str(self._chart_path),
                "--namespace",
                self._namespace,
                "--wait",
                "--timeout",
                f"{_get_timeout()}s",
                "--set",
                # Annotation do not have strict length reqs. Quoting/escaping
                # handled by asyncio.create_subprocess_exec.
                f"annotations.inspectTaskName={self.task_name}",
                # Include a label to identify releases created by Inspect.
                "--labels",
                "inspectSandbox=true",
            ]
            + values,
            capture_output=True,
        )
        if not result.success:
            self._raise_install_error(result)

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
        if match := re.search(
            r"forbidden: exceeded quota: .*requested:.*used:.*limited:.*",
            result.stderr,
        ):
            # Note: Different resource quotas manifest exceeding them in different ways:
            # for a Pod quota, the Helm release will eventually time out with no mention
            # of quotas; for a configmap quota, an error matching the regex will be
            # raised immediately.
            log_trace(
                "exceeded resource quota error whilst installing helm chart.",
                release=self.release_name,
                error=result.stderr,
            )
            global resource_quota_exceeded_counter
            if resource_quota_exceeded_counter == 0:
                # Log only once.
                log_message(
                    logging.WARNING,
                    "K8s resource quota exceeded. Please uninstall any unused Helm "
                    "releases or reduce the level of concurrency in your Inspect eval. "
                    + match.group(),
                )
            resource_quota_exceeded_counter += 1
            display_counter(
                "K8s resource quota", f"{resource_quota_exceeded_counter:,}"
            )
            raise _ResourceQuotaExceededError(result.stderr)
        if re.search(r"INSTALLATION FAILED: context deadline exceeded", result.stderr):
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


async def uninstall(release_name: str, namespace: str, quiet: bool) -> None:
    """
    Uninstall a Helm release by name.

    The number of concurrent uninstall operations is limited by a semaphore.

    "Release not found" errors are ignored.

    Args:
        release_name: The name of the Helm release to uninstall (e.g. abcdefgh).
        namespace: The Kubernetes namespace in which the release is installed.
        quiet: If False, allow the output of the `helm uninstall` command to be written
          to this process's stdout/stderr. If True, suppress the output.
    """

    def _is_release_not_found_error(stderr: str) -> bool:
        # The consequence of a false positive is to discard a useful error message, so
        # err on the side of strictness.
        return (
            re.match(
                rf"^Error: uninstall: Release not loaded: {release_name}: release: not "
                "found$",
                stderr,
                re.IGNORECASE,
            )
            is not None
        )

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
                    "--wait",
                    "--timeout",
                    f"{_get_timeout()}s",
                ],
                capture_output=True,
            )
            # A helm uninstall failure with "release not found" implies that the release
            # was never successfully installed or has already been uninstalled.
            # When a helm release fails to install (or the user cancels the eval), this
            # uninstall function will still be called, so these errors are common and
            # result in error desensitisation.
            is_release_not_found_error = _is_release_not_found_error(result.stderr)
            if not quiet and not is_release_not_found_error:
                sys.stdout.write(result.stdout)
                sys.stderr.write(result.stderr)
            if not result.success and not is_release_not_found_error:
                _raise_runtime_error(
                    "Helm uninstall failed.",
                    release=release_name,
                    namespace=namespace,
                    returncode=result.returncode,
                    stdout=result.stdout,
                    stderr=result.stderr,
                )


async def get_all_release_names(namespace: str) -> list[str]:
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
        ],
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


async def _run_subprocess(
    cmd: str, args: list[str], capture_output: bool
) -> ExecResult[str]:
    proc = await asyncio.create_subprocess_exec(
        cmd,
        *args,
        stdout=asyncio.subprocess.PIPE if capture_output else None,
        stderr=asyncio.subprocess.PIPE if capture_output else None,
    )
    stdout, stderr = await proc.communicate()
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

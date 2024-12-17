import asyncio
import logging
import os
import re
from pathlib import Path
from typing import Any, NoReturn

from inspect_ai.util import ExecResult, concurrency
from kubernetes.client.rest import ApiException  # type: ignore
from shortuuid import uuid

from aisitools.k8s_sandbox._kubernetes_api import (
    get_current_context_namespace,
    k8s_client,
)
from aisitools.k8s_sandbox._logger import format_log_message, sandbox_log
from aisitools.k8s_sandbox._pod import Pod

DEFAULT_CHART = Path(__file__).parent / "resources" / "helm" / "agent-env"
DEFAULT_TIMEOUT = 300
MAX_INSTALL_ATTEMPTS = 3
INSTALL_RETRY_DELAY_SECONDS = 5


logger = logging.getLogger(__name__)


class _ResourceQuotaModifiedError(Exception):
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
            sandbox_log(
                "Installing helm chart.",
                chart=self._chart_path,
                release=self.release_name,
                values=self._values_path,
                namespace=self._namespace,
                task=self.task_name,
            )
            attempt = 1
            while True:
                try:
                    await self._install(upgrade=attempt > 1)
                    break
                except _ResourceQuotaModifiedError:
                    if attempt >= MAX_INSTALL_ATTEMPTS:
                        raise
                    attempt += 1
                    await asyncio.sleep(INSTALL_RETRY_DELAY_SECONDS)

    async def uninstall(self, quiet: bool) -> None:
        await uninstall(self.release_name, quiet)

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
            sandbox_log(
                "resourcequota modified error whilst installing helm chart.",
                release=self.release_name,
                error=result.stderr,
            )
            raise _ResourceQuotaModifiedError(result.stderr)
        _raise_runtime_error(
            "Helm install failed.", release=self.release_name, result=result
        )


async def uninstall(release_name: str, quiet: bool) -> None:
    namespace = get_current_context_namespace()
    async with _uninstall_semaphore():
        sandbox_log(
            "Uninstalling helm release.", release=release_name, namespace=namespace
        )
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
            capture_output=quiet,
        )
    if not result.success:
        captured_output = result.stdout if not quiet else "not captured"
        _raise_runtime_error(
            "Helm uninstall failed.", release=release_name, result=captured_output
        )


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
    if user_configured_timeout := os.environ.get("INSPECT_HELM_TIMEOUT"):
        timeout_int = int(user_configured_timeout)
        if timeout_int <= 0:
            raise ValueError(
                "INSPECT_HELM_TIMEOUT must be a positive int: "
                f"{user_configured_timeout}"
            )
        return timeout_int
    return DEFAULT_TIMEOUT


def _install_semaphore() -> asyncio.Semaphore:
    # Limit concurrent subprocess calls to `helm install` and `helm uninstall`.
    # Use distinct semaphores for each operation to avoid deadlocks where all permits
    # are acquired by the "install" operations which are waiting for cluster resources
    # to be released by the "uninstall" operations.
    # Use Inspect's concurrency function as this ensures each asyncio.Semaphore is
    # unique per event loop.
    return concurrency("helm-install", _get_environ_int("INSPECT_MAX_HELM_INSTALL", 8))


def _uninstall_semaphore() -> asyncio.Semaphore:
    return concurrency(
        "helm-uninstall", _get_environ_int("INSPECT_MAX_HELM_UNINSTALL", 8)
    )


def _get_environ_int(name: str, default: int) -> int:
    try:
        return int(os.environ[name])
    except (KeyError, ValueError):
        return default

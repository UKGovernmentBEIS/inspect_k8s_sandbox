import asyncio
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Generator, Literal, cast, overload

from inspect_ai.util import (
    ExecResult,
    OutputLimitExceededError,
    SandboxEnvironment,
    SandboxEnvironmentConfigType,
    sandboxenv,
)
from pydantic import BaseModel
from rich.prompt import Confirm

from k8s_sandbox._helm import Release, get_all_release_names, uninstall
from k8s_sandbox._kubernetes_api import get_current_context_namespace
from k8s_sandbox._logger import (
    format_log_message,
    inspect_trace_action,
    log_error,
    log_trace,
)
from k8s_sandbox._manager import (
    HelmReleaseManager,
    uninstall_unmanaged_release,
)
from k8s_sandbox._pod import Pod
from k8s_sandbox._prereqs import validate_prereqs


@sandboxenv(name="k8s")
class K8sSandboxEnvironment(SandboxEnvironment):
    """An Inspect sandbox environment for a Kubernetes (k8s) cluster."""

    def __init__(self, release: Release, pod: Pod):
        self.release = release
        self._pod = pod

    @classmethod
    def config_files(cls) -> list[str]:
        return ["values.yaml", "helm-values.yaml"]

    @classmethod
    async def task_init(
        cls, task_name: str, config: SandboxEnvironmentConfigType | None
    ) -> None:
        await validate_prereqs()
        # Sample contexts will be copied from the task context, so initialise the
        # manager in the task context so that task_cleanup() accesses a manager which
        # is tracking the releases for all of the task's samples.
        HelmReleaseManager.get_instance()

    @classmethod
    async def task_cleanup(
        cls, task_name: str, config: SandboxEnvironmentConfigType | None, cleanup: bool
    ) -> None:
        # Uninstall any releases which were not uninstalled by sample_cleanup().
        await HelmReleaseManager.get_instance().uninstall_all(print_only=not cleanup)

    @classmethod
    async def cli_cleanup(cls, id: str | None) -> None:
        if id is not None:
            await uninstall_unmanaged_release(id)
        else:
            await _uninstall_all_unmanaged_releases()

    @classmethod
    async def sample_init(
        cls,
        task_name: str,
        config: SandboxEnvironmentConfigType | None,
        metadata: dict[str, str],
    ) -> dict[str, SandboxEnvironment]:
        async def get_sandboxes(release: Release) -> dict[str, SandboxEnvironment]:
            pods = await release.get_sandbox_pods()
            sandbox_envs: dict[str, SandboxEnvironment] = {}
            for key, pod in pods.items():
                sandbox_envs[key] = cls(release, pod)
            log_trace(f"Available sandboxes: {list(sandbox_envs.keys())}")
            return sandbox_envs

        def reorder_default_first(
            sandboxes: dict[str, SandboxEnvironment],
        ) -> dict[str, SandboxEnvironment]:
            # Inspect expects the default sandbox to be the first sandbox in the dict.
            if "default" in sandboxes:
                default = sandboxes.pop("default")
                return {"default": default, **sandboxes}
            return sandboxes

        release = _create_release(task_name, config)
        await HelmReleaseManager.get_instance().install(release)
        return reorder_default_first(await get_sandboxes(release))

    @classmethod
    async def sample_cleanup(
        cls,
        task_name: str,
        config: SandboxEnvironmentConfigType | None,
        environments: dict[str, SandboxEnvironment],
        interrupted: bool,
    ) -> None:
        # If we were interrupted, wait until the end of the task to cleanup (this
        # enables us to show output for the cleanup operation).
        if interrupted:
            return
        sandbox: K8sSandboxEnvironment = cast(
            K8sSandboxEnvironment, next(iter(environments.values()))
        )
        await HelmReleaseManager.get_instance().uninstall(sandbox.release, quiet=True)

    async def exec(
        self,
        cmd: list[str],
        input: str | bytes | None = None,
        cwd: str | None = None,
        env: dict[str, str] = {},
        user: str | None = None,
        timeout: int | None = None,
    ) -> ExecResult[str]:
        if user is not None:
            raise NotImplementedError(
                "The user parameter for exec() is not yet supported."
            )
        log_kwargs = dict(cmd=cmd, stdin=input, cwd=cwd, env=env, timeout=timeout)
        # Do not log these at error level or re-raise as enriched K8sError.
        expected_exceptions = (
            TimeoutError,
            UnicodeDecodeError,
            PermissionError,
            OutputLimitExceededError,
        )
        op = "K8s execute command in Pod"
        with self._log_op(op, expected_exceptions, **log_kwargs):
            result = await self._pod.exec(cmd, input, cwd, env, timeout)
            log_trace(f"Completed: {op}.", **(log_kwargs | {"result": result}))
            return result

    async def write_file(self, file: str, contents: str | bytes) -> None:
        # Write contents to a temporary file on the client system and pass the file
        # handle.
        with tempfile.NamedTemporaryFile("w+b") as temp_file:
            if isinstance(contents, str):
                temp_file.write(contents.encode("utf-8"))
            else:
                temp_file.write(contents)
            temp_file.seek(0)
            # Do not log these at error level or re-raise as enriched K8sError.
            expected_exceptions = (PermissionError, IsADirectoryError)
            with self._log_op("K8s write file to Pod", expected_exceptions, file=file):
                await self._pod.write_file(temp_file.file, Path(file))

    @overload
    async def read_file(self, file: str, text: Literal[True] = True) -> str: ...

    @overload
    async def read_file(self, file: str, text: Literal[False]) -> bytes: ...

    async def read_file(self, file: str, text: bool = True) -> str | bytes:
        # Create and open a temporary file on the client system which the file will be
        # written to.
        with tempfile.NamedTemporaryFile("w+b") as temp_file:
            # Do not log these at error level or re-raise as enriched K8sError.
            expected_exceptions = (
                FileNotFoundError,
                UnicodeDecodeError,
                PermissionError,
                IsADirectoryError,
                OutputLimitExceededError,
            )
            with self._log_op("K8s read file from Pod", expected_exceptions, file=file):
                await self._pod.read_file(Path(file), temp_file)
                temp_file.seek(0)
                return (
                    temp_file.read() if not text else temp_file.read().decode("utf-8")
                )

    @contextmanager
    def _log_op(
        self, op: str, expected_exceptions: tuple, **log_kwargs
    ) -> Generator[None, None, None]:
        """Logs the lifecycle of an operation and enriches unexpected exceptions.

        The pod name and task name are included all log messages in addition to
        log_kwargs.

        Inspect's trace_action() context manager will log any exceptions at TRACE level.
        No additional handling of "expected" exceptions (e.g. TimeoutError) is
        performed.
        For "unexpected" exceptions (e.g. ApiException), the exception is logged at
        "ERROR" level and re-raised as a K8sError which includes additional context for
        debugging.
        """
        log_kwargs = dict(
            pod=self._pod.info.name, task_name=self.release.task_name, **log_kwargs
        )
        with inspect_trace_action(op, **log_kwargs):
            try:
                yield
            except expected_exceptions:
                raise
            except Exception as e:
                # Whilst Inspect's trace_action will have logged the exception, log it
                # at ERROR level here for user visibility.
                log_error(f"Error during: {op}.", cause=e, **log_kwargs)
                # Enrich the unexpected exception with additional context.
                raise K8sError(f"Error during: {op}.", **log_kwargs) from e


class K8sSandboxEnvironmentConfig(BaseModel, frozen=True):
    """A config Pydantic model for the K8s sandbox environment."""

    # In future, charts from Helm repositories may be supported, hence str over Path.
    chart: str | None = None
    values: Path | None = None


class K8sError(Exception):
    """An error that occurred during a Kubernetes operation.

    This will typically cause the eval to fail.
    """

    def __init__(self, message: str, **kwargs: Any):
        super().__init__(format_log_message(message, **kwargs))


def _create_release(
    task_name: str, config: SandboxEnvironmentConfigType | None
) -> Release:
    def validate_values_file(values: Path | None) -> None:
        if values is not None and not values.is_file():
            raise FileNotFoundError(f"Helm values file not found: '{values}'.")

    def validate_chart_dir(chart: Path | None) -> None:
        if chart is not None and not chart.is_dir():
            raise NotADirectoryError(
                f"Helm chart directory not found: '{chart}'. At present, only "
                "charts from local directories are supported."
            )

    if config is None:
        return Release(task_name)
    if isinstance(config, K8sSandboxEnvironmentConfig):
        chart = Path(config.chart).resolve() if config.chart else None
        validate_chart_dir(chart)
        values = config.values.resolve() if config.values else None
        validate_values_file(values)
        return Release(task_name, chart_path=chart, values_path=values)
    if isinstance(config, str):
        values = Path(config).resolve()
        validate_values_file(values)
        return Release(task_name, values_path=values)
    raise TypeError(f"Invalid config type: {type(config)}.")


async def _uninstall_all_unmanaged_releases():
    namespace = get_current_context_namespace()
    releases = await get_all_release_names(namespace)
    if len(releases) == 0:
        print(f"No Inspect sandbox releases found in '{namespace}' namespace.")
        return
    if not Confirm.ask(
        f"Are you sure you want to uninstall ALL {len(releases)} Inspect sandbox "
        f"release(s) in '{namespace}' namespace? If this is a shared namespace, "
        "this may affect other users.",
    ):
        print("Cancelled.")
        return
    tasks = [uninstall(release, namespace, quiet=False) for release in releases]
    await asyncio.gather(*tasks, return_exceptions=True)
    print("Complete.")

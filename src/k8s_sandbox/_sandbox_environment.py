from __future__ import annotations

import re
import shlex
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Generator, Literal, cast, overload

from inspect_ai.solver._task_state import sample_state
from inspect_ai.util import (
    ComposeConfig,
    ExecResult,
    OutputLimitExceededError,
    SandboxConnection,
    SandboxEnvironment,
    SandboxEnvironmentConfigType,
    is_compose_yaml,
    is_dockerfile,
    sandboxenv,
)
from pydantic import BaseModel, TypeAdapter

from k8s_sandbox._helm import (
    DEFAULT_CHART,
    Release,
    StaticValuesSource,
    ValuesSource,
)
from k8s_sandbox._kubernetes_api import validate_context_name
from k8s_sandbox._logger import (
    format_log_message,
    inspect_trace_action,
    log_error,
    log_trace,
    log_warn,
)
from k8s_sandbox._manager import (
    HelmReleaseManager,
    uninstall_all_unmanaged_releases,
    uninstall_unmanaged_release,
)
from k8s_sandbox._pod import Pod
from k8s_sandbox._pod.executor import PodOpExecutor
from k8s_sandbox._prereqs import validate_prereqs
from k8s_sandbox.compose._compose import (
    ComposeConfigValuesSource,
    ComposeValuesSource,
    is_docker_compose_file,
    parse_docker_config,
)

MIN_DESIRED_SOFT = 100000


@sandboxenv(name="k8s")
class K8sSandboxEnvironment(SandboxEnvironment):
    """An Inspect sandbox environment for a Kubernetes (k8s) cluster."""

    _rlimit_adjusted = False

    def __init__(self, release: Release, pod: Pod, config: _ResolvedConfig):
        self.release = release
        self._pod = pod
        self._config = config
        self._adjust_rlimit()

    def _adjust_rlimit(self):
        if sys.platform != "win32" and not K8sSandboxEnvironment._rlimit_adjusted:
            import resource

            # the linux default of 1024 max open files causes problems
            existing_soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
            # don't try to set soft higher than hard
            desired_soft = min(MIN_DESIRED_SOFT, hard)
            if hard < MIN_DESIRED_SOFT:
                log_warn(
                    f"Unable to increase max open files to {MIN_DESIRED_SOFT}. "
                    f"Setting instead to the hard limit of {desired_soft}."
                )
            if existing_soft < desired_soft:
                log_trace(
                    f"Increasing RLIMIT_NOFILE to {desired_soft}",
                    existing_soft=existing_soft,
                    existing_hard=hard,
                )
                try:
                    resource.setrlimit(resource.RLIMIT_NOFILE, (desired_soft, hard))
                except Exception as e:
                    log_warn(
                        f"Failed to increase maximum open files to {desired_soft}. "
                        f"Continuing with existing soft limit {existing_soft}.",
                        error=str(e),
                    )

            # even if the adjustment failed, there's no point trying again
            K8sSandboxEnvironment._rlimit_adjusted = True

    @classmethod
    def config_files(cls) -> list[str]:
        return [
            "values.yaml",
            "helm-values.yaml",
            "compose.yaml",
            "docker-compose.yaml",
            "Dockerfile",
        ]

    @classmethod
    def is_docker_compatible(cls) -> bool:
        return True

    @classmethod
    async def task_init(
        cls, task_name: str, config: SandboxEnvironmentConfigType | None
    ) -> None:
        await validate_prereqs()
        max_pod_ops = (
            config.max_pod_ops
            if isinstance(config, K8sSandboxEnvironmentConfig)
            else None
        )
        PodOpExecutor.get_instance(max_pod_ops=max_pod_ops)
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
            await uninstall_all_unmanaged_releases()

    @classmethod
    async def sample_init(
        cls,
        task_name: str,
        config: SandboxEnvironmentConfigType | None,
        metadata: dict[str, str],
    ) -> dict[str, SandboxEnvironment]:
        async def get_sandboxes(
            release: Release, config: _ResolvedConfig
        ) -> dict[str, SandboxEnvironment]:
            pods = await release.get_sandbox_pods()
            sandbox_envs: dict[str, SandboxEnvironment] = {}
            for key, pod in pods.items():
                sandbox_envs[key] = cls(release, pod, config)
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

        resolved_config = _validate_and_resolve_k8s_sandbox_config(config)
        state = sample_state()
        sample_uuid = state.uuid if state else None
        extra_values = _metadata_to_extra_values(
            metadata, resolved_config.chart, resolved_config.values
        )
        release = _create_release(
            task_name,
            resolved_config,
            sample_uuid=sample_uuid,
            extra_values=extra_values,
        )
        await HelmReleaseManager.get_instance().install(release)
        return reorder_default_first(await get_sandboxes(release, resolved_config))

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
        env: dict[str, str] | None = {},
        user: str | None = None,
        timeout: int | None = None,
        # Ignored. Inspect docs: "For sandbox implementations this parameter is advisory
        # (they should only use it if potential unreliablity exists in their runtime)."
        timeout_retry: bool = True,
        # To be implemented - see https://github.com/UKGovernmentBEIS/inspect_k8s_sandbox/issues/126
        concurrency: bool = True,
    ) -> ExecResult[str]:
        log_kwargs = dict(cmd=cmd, stdin=input, cwd=cwd, env=env, timeout=timeout)
        # Do not log these at error level or re-raise as enriched K8sError.
        expected_exceptions = (
            TimeoutError,
            UnicodeDecodeError,
            PermissionError,
            OutputLimitExceededError,
        )
        if user is None:
            user = self._config.default_user
        op = "K8s execute command in Pod"
        with self._log_op(op, expected_exceptions, **log_kwargs):
            result = await self._pod.exec(cmd, input, cwd, env or {}, user, timeout)
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

    async def connection(self, *, user: str | None = None) -> SandboxConnection:
        if user is None:
            user = self._config.default_user
        return SandboxConnection(
            type="k8s",
            command=self._get_kubectl_connection_command(user),
            vscode_command=self._get_vscode_connection_command(user),
            container=self._pod.info.default_container_name,
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

    @classmethod
    def config_deserialize(cls, config: dict[str, Any]) -> BaseModel:
        adapter = TypeAdapter[K8sSandboxEnvironmentConfig | ComposeConfig](
            K8sSandboxEnvironmentConfig | ComposeConfig
        )
        return adapter.validate_python(config)

    def _get_kubectl_connection_command(self, user: str | None) -> str:
        kubectl_cmd = [
            "kubectl",
            "exec",
            "-it",
            self._pod.info.name,
            "-n",
            self._pod.info.namespace,
            "-c",
            self._pod.info.default_container_name,
        ]
        if self._pod.info.context_name is not None:
            kubectl_cmd.extend(["--context", self._pod.info.context_name])
        kubectl_cmd.append("--")
        if user is not None:
            kubectl_cmd.extend(["su", "-s", "/bin/bash", "-l", user])
        else:
            kubectl_cmd.extend(["bash", "-l"])
        return shlex.join(kubectl_cmd)

    def _get_vscode_connection_command(self, user: str | None) -> list | None:
        # Do not return a command for options which aren't supported.
        if self._pod.info.context_name is not None:
            return None
        if user is not None:
            return None
        # Note that there is no facility to specify the default container name - the
        # user will be prompted to select one (usually "default").
        return [
            "remote-containers.attachToK8sContainer",
            {
                "name": self._pod.info.name,
                "namespace": self._pod.info.namespace,
            },
        ]


class K8sSandboxEnvironmentConfig(BaseModel, frozen=True):
    """A user-supplied configuration Pydantic model for the K8s sandbox environment."""

    # In future, charts from Helm repositories may be supported, hence str over Path.
    chart: str | None = None
    values: Path | None = None
    context: str | None = None
    """The kubeconfig context name (e.g. if you have multiple clusters)."""
    default_user: str | None = None
    """The user to run commands as in the container if user is not specified."""
    restarted_container_behavior: Literal["warn", "raise"] = "warn"
    max_pod_ops: int | None = None
    """Maximum number of concurrent pod operations. Defaults to cpu_count * 4."""


class K8sError(Exception):
    """An error that occurred during a Kubernetes operation.

    This will typically cause the eval to fail.
    """

    def __init__(self, message: str, **kwargs: Any):
        super().__init__(format_log_message(message, **kwargs))


def _key_to_pascal(key: str) -> str:
    """Convert a metadata key to PascalCase.

    Splits on spaces and camelCase word boundaries, then capitalises each
    word.  For example::

        "foo bar"     -> "FooBar"
        "fooBar"      -> "FooBar"
        "fooBarBaz"   -> "FooBarBaz"
        "FOO"         -> "Foo"
    """
    words: list[str] = []
    for segment in key.split(" "):
        # Split camelCase: insert boundary between a lowercase/digit and an
        # uppercase letter (e.g. "fooBar" -> ["foo", "Bar"]).
        parts = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", segment).split()
        words.extend(parts)
    return "".join(w.capitalize() for w in words if w)


def _metadata_to_extra_values(
    metadata: dict[str, str],
    chart_path: Path | None,
    values_path: Path | None,
) -> dict[str, str]:
    """Convert sample metadata to Helm extra values.

    Each metadata key is converted to PascalCase (handling spaces, hyphens,
    underscores, and camelCase boundaries) then prefixed with
    ``sampleMetadata``.  Only metadata keys that are actually referenced in
    the chart templates or config file are included.

    Args:
        metadata: The sample metadata dict.
        chart_path: Path to the Helm chart directory (uses DEFAULT_CHART if None).
        values_path: Path to the values/config file (may be None).

    Returns:
        A dict of Helm ``--set`` key-value pairs.
    """
    if not metadata:
        return {}

    config_text = _read_chart_config_text(chart_path or DEFAULT_CHART, values_path)

    extra_values: dict[str, str] = {}
    for key, value in metadata.items():
        if not re.fullmatch(r"[a-zA-Z0-9 ]+", key):
            log_warn(
                "Skipping sample metadata key with unsupported characters",
                key=key,
            )
            continue
        pascal = _key_to_pascal(key)
        helm_key = f"sampleMetadata{pascal}"
        if helm_key in config_text:
            extra_values[helm_key] = value
    return extra_values


def _read_chart_config_text(chart_path: Path, values_path: Path | None) -> str:
    """Read all chart files and the config file as a single string."""
    parts: list[str] = []

    for chart_file in chart_path.rglob("*"):
        if chart_file.is_file():
            try:
                parts.append(chart_file.read_text())
            except (OSError, UnicodeDecodeError):
                pass

    if values_path and values_path.is_file():
        try:
            parts.append(values_path.read_text())
        except (OSError, UnicodeDecodeError):
            pass

    return "\n".join(parts)


def _create_release(
    task_name: str,
    config: _ResolvedConfig,
    sample_uuid: str | None = None,
    extra_values: dict[str, str] | None = None,
) -> Release:
    values_source = _create_values_source(config)
    return Release(
        task_name,
        config.chart,
        values_source,
        config.context,
        config.restarted_container_behavior,
        sample_uuid=sample_uuid,
        extra_values=extra_values,
    )


class _ResolvedConfig(BaseModel, frozen=True):
    """An internal model which consolidates configuration options."""

    chart: Path | None
    values: Path | None
    context: str | None
    default_user: str | None
    restarted_container_behavior: Literal["warn", "raise"]
    compose_config: BaseModel | None = None
    max_pod_ops: int | None


def _create_values_source(config: _ResolvedConfig) -> ValuesSource:
    if config.compose_config is not None:
        if config.chart is not None:
            raise ValueError(
                "Automatic conversion from ComposeConfig to helm-values is only "
                "supported when using the built-in Helm chart."
            )

        return ComposeConfigValuesSource(cast(ComposeConfig, config.compose_config))
    if config.values and is_docker_compose_file(config.values):
        if config.chart is not None:
            raise ValueError(
                "Automatic conversion from compose.yaml to helm-values.yaml is only "
                "supported when using the built-in Helm chart."
            )
        return ComposeValuesSource(config.values)
    return StaticValuesSource(config.values)


def _validate_and_resolve_k8s_sandbox_config(
    config: SandboxEnvironmentConfigType | None,
) -> _ResolvedConfig:
    """Validate and consolidate the user-supplied config into a _ReleaseConfig."""

    def validate_values_file(values: Path | None) -> None:
        if values is not None and not values.is_file():
            raise FileNotFoundError(f"Helm values file not found: '{values}'.")

    def validate_chart_dir(chart: Path | None) -> None:
        if chart is not None and not chart.is_dir():
            raise NotADirectoryError(
                f"Helm chart directory not found: '{chart}'. At present, only "
                "charts from local directories are supported."
            )

    def validate_context(context: str | None) -> None:
        # Note: There is a race condition between validating the context name and
        # actually using it because the kubeconfig file could change on disk. Validate
        # it nonetheless to fail fast if possible.
        if context is not None:
            validate_context_name(context)

    if config is None:
        return _ResolvedConfig(
            chart=None,
            values=None,
            context=None,
            default_user=None,
            restarted_container_behavior="warn",
            max_pod_ops=None,
        )
    if isinstance(config, K8sSandboxEnvironmentConfig):
        chart = Path(config.chart).resolve() if config.chart else None
        validate_chart_dir(chart)
        values = config.values.resolve() if config.values else None
        validate_values_file(values)
        validate_context(config.context)
        return _ResolvedConfig(
            chart=chart,
            values=values,
            context=config.context,
            default_user=config.default_user,
            restarted_container_behavior=config.restarted_container_behavior,
            max_pod_ops=config.max_pod_ops,
        )
    if isinstance(config, ComposeConfig):
        return _ResolvedConfig(
            chart=None,
            values=None,
            context=None,
            default_user=None,
            restarted_container_behavior="warn",
            compose_config=config,
        )
    if isinstance(config, str):
        if is_compose_yaml(config) or is_dockerfile(config):
            compose_config = parse_docker_config(config)
            return _ResolvedConfig(
                chart=None,
                values=None,
                context=None,
                default_user=None,
                restarted_container_behavior="warn",
                compose_config=compose_config,
            )
        values = Path(config).resolve()
        validate_values_file(values)
        return _ResolvedConfig(
            chart=None,
            values=values,
            context=None,
            default_user=None,
            restarted_container_behavior="warn",
            max_pod_ops=None,
        )
    raise TypeError(
        f"Invalid 'SandboxEnvironmentConfigType | None' type: {type(config)}."
    )

import re
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Generator, Literal, cast, overload

import yaml
from inspect_ai.util import (
    ExecResult,
    OutputLimitExceededError,
    SandboxEnvironment,
    SandboxEnvironmentConfigType,
    sandboxenv,
)
from pydantic import BaseModel

from k8s_sandbox._compose.compose import ComposeValuesSource, is_docker_compose_file
from k8s_sandbox._helm import (
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
)
from k8s_sandbox._manager import (
    HelmReleaseManager,
    uninstall_all_unmanaged_releases,
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
        # compose.yaml files are not automatically used; they must be explicitly
        # specified as the values file. To reduce risk of a user accidentally using a
        # compose.yaml file over a (e.g. misnamed) helm-values.yaml file.
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
            await uninstall_all_unmanaged_releases()

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
        # Ignored. Inspect docs: "For sandbox implementations this parameter is advisory
        # (they should only use it if potential unreliablity exists in their runtime)."
        timeout_retry: bool = True,
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

    @classmethod
    def config_deserialize(cls, config: dict[str, Any]) -> BaseModel:
        return K8sSandboxEnvironmentConfig(**config)


class K8sSandboxEnvironmentConfig(BaseModel, frozen=True):
    """A config Pydantic model for the K8s sandbox environment."""

    # In future, charts from Helm repositories may be supported, hence str over Path.
    chart: str | None = None
    values: Path | None = None
    context: str | None = None
    """The kubeconfig context name (e.g. if you have multiple clusters)."""


class K8sError(Exception):
    """An error that occurred during a Kubernetes operation.

    This will typically cause the eval to fail.
    """

    def __init__(self, message: str, **kwargs: Any):
        super().__init__(format_log_message(message, **kwargs))


def validate_k8s_name(name: str) -> tuple[bool, str]:
    """
    Validates if a given string can be used as a Kubernetes identifier.

    Most Kubernetes resources must confirm to RFC 1123 naming standards, as per:
    https://kubernetes.io/docs/concepts/overview/working-with-objects/names/#dns-label-names

    If you try to apply a resource that does not conform to RFC 1123, Kubernetes will
    return an error like:

        Invalid value: "agent-env-byzslntg-web_browser": a lowercase RFC 1123 label must
        consist of lower case alphanumeric characters or '-', and must start and end
        with an alphanumeric character (e.g. 'my-name', or '123-abc', regex used for
        validation is '[a-z0-9]([-a-z0-9]*[a-z0-9])?'

    This is, as of the time of writing, located here in the Kubernetes source code:
    https://github.com/kubernetes/kubernetes/blob/68899f8e6d5861e7b6197c51b0dee9f8a486e3e0/staging/src/k8s.io/apimachinery/pkg/util/validation/validation.go#L179

    Args:
        name: The string to validate as a Kubernetes resource name

    Returns:
        A tuple of (is_valid, error_message)
    """
    # DNS-1123 subdomain must consist of lowercase alphanumeric characters, '-' or '.',
    # and must start and end with an alphanumeric character
    if not name:
        return False, "Service name cannot be empty"

    if len(name) > 63:
        return False, f"Service name '{name}' is too long (max 63 characters)"

    # Check if name starts with alphanumeric
    if not name[0].isalnum():
        return False, f"Service name '{name}' must start with an alphanumeric character"

    # Check if name ends with alphanumeric
    if not name[-1].isalnum():
        return False, f"Service name '{name}' must end with an alphanumeric character"

    # Check for valid characters (lowercase alphanumeric, '-', '.')
    if not re.match(r"^[a-z0-9\.\-]+$", name):
        return (
            False,
            f"Service name '{name}' must consist only of lowercase alphanumeric"
            " characters, '-' or '.'",
        )

    return True, ""


def validate_service_names(values_content: dict) -> None:
    """
    Validates that all service names in a values dictionary conform to naming rules.

    Example error:
        ```
        Invalid Kubernetes service name(s) in values file:
            - Service name '-invalid-start' must start with an alphanumeric character
            - Service name 'Invalid_Service' must consist only of lowercase alphanumeric
                characters, '-' or '.'
            - Service name 'invalid-end-' must end with an alphanumeric character

            Service names must:
            - Contain only lowercase alphanumeric characters, '-' or '.'
            - Start and end with an alphanumeric character
            - Be no more than 63 characters long
        ```

    Args:
        values_content: Dictionary containing the parsed YAML values

    Raises:
        ValueError: If any service name doesn't conform to Kubernetes naming rules
    """
    if not values_content or not isinstance(values_content, dict):
        return

    services = values_content.get("services", {})
    if not services or not isinstance(services, dict):
        return

    invalid_services = []
    for service_name in services.keys():
        is_valid, error = validate_k8s_name(service_name)
        if not is_valid:
            invalid_services.append(f"  - {error}")

    if invalid_services:
        error_message = f"""Invalid Kubernetes service name(s) in values file:
{"\n".join(invalid_services)}

Service names must:
  - Contain only lowercase alphanumeric characters, '-' or '.'
  - Start and end with an alphanumeric character
  - Be no more than 63 characters long"""

        log_trace("Values file contains invalid service names")
        raise ValueError(error_message)


def _create_release(
    task_name: str, config: SandboxEnvironmentConfigType | None
) -> Release:
    release_config = _resolve_release_config(config)
    values_source = _create_values_source(release_config)
    return Release(
        task_name, release_config.chart, values_source, release_config.context
    )


class _ReleaseConfig(BaseModel, frozen=True):
    chart: Path | None
    values: Path | None
    context: str | None


def _create_values_source(release_config: _ReleaseConfig) -> ValuesSource:
    if release_config.values and is_docker_compose_file(release_config.values):
        if release_config.chart is not None:
            raise ValueError(
                "Automatic conversion from compose.yaml to helm-values.yaml is only "
                "supported when using the built-in Helm chart."
            )
        return ComposeValuesSource(release_config.values)
    return StaticValuesSource(release_config.values)


def _resolve_release_config(
    config: SandboxEnvironmentConfigType | None,
) -> _ReleaseConfig:
    """Consolidates the many options configuration methods into a _ReleaseConfig."""

    def validate_values_file(values: Path | None) -> None:
        if values is not None:
            if not values.is_file():
                raise FileNotFoundError(f"Helm values file not found: '{values}'.")

            with open(values, "r") as f:
                # Will throw `yaml.YAMLError` if file is invalid YAML
                values_content = yaml.safe_load(f)
                if values_content:
                    validate_service_names(values_content)

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
        return _ReleaseConfig(chart=None, values=None, context=None)
    if isinstance(config, K8sSandboxEnvironmentConfig):
        chart = Path(config.chart).resolve() if config.chart else None
        validate_chart_dir(chart)
        values = config.values.resolve() if config.values else None
        validate_values_file(values)
        validate_context(config.context)
        return _ReleaseConfig(chart=chart, values=values, context=config.context)
    if isinstance(config, str):
        values = Path(config).resolve()
        validate_values_file(values)
        return _ReleaseConfig(chart=None, values=values, context=None)
    raise TypeError(
        f"Invalid 'SandboxEnvironmentConfigType | None' type: {type(config)}."
    )

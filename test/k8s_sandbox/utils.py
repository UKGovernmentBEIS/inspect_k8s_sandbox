from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncGenerator

from inspect_ai.util import SandboxEnvironmentConfigType

from k8s_sandbox._helm import Release, StaticValuesSource
from k8s_sandbox._sandbox_environment import (
    K8sSandboxEnvironment,
    _validate_and_resolve_k8s_sandbox_config,
)


@asynccontextmanager
async def install_sandbox_environments(
    task_name: str,
    values_filename: str | None,
    context_name: str | None = None,
    configs: dict[str, SandboxEnvironmentConfigType] = {},
) -> AsyncGenerator[dict[str, K8sSandboxEnvironment], None]:
    values_path = (
        Path(__file__).parent / "resources" / values_filename
        if values_filename
        else None
    )
    values_source = StaticValuesSource(values_path)
    release = Release(
        task_name=task_name,
        chart_path=None,
        values_source=values_source,
        context_name=context_name,
    )
    try:
        await release.install()
        pods = await release.get_sandbox_pods()
        sandbox_envs = {
            pod_name: K8sSandboxEnvironment(
                release,
                pod,
                _validate_and_resolve_k8s_sandbox_config(configs.get(pod_name)),
            )
            for pod_name, pod in pods.items()
        }
        yield sandbox_envs
    finally:
        await release.uninstall(quiet=True)

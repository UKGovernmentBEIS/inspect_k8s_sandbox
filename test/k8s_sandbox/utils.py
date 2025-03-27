from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncGenerator

from k8s_sandbox._helm import Release, StaticValuesSource
from k8s_sandbox._sandbox_environment import K8sSandboxEnvironment


@asynccontextmanager
async def install_sandbox_environments(
    task_name: str, values_filename: str | None
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
        context_name=None,
    )
    try:
        await release.install()
        pods = await release.get_sandbox_pods()
        sandbox_envs = {
            pod_name: K8sSandboxEnvironment(release, pod)
            for pod_name, pod in pods.items()
        }
        yield sandbox_envs
    finally:
        await release.uninstall(quiet=True)

from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncGenerator

import k8s_sandbox
from k8s_sandbox._helm import Release, StaticValuesSource
from k8s_sandbox._sandbox_environment import K8sSandboxEnvironment, K8sSandboxEnvironmentConfig


@asynccontextmanager
async def install_sandbox_environments(
    task_name: str, values_filename: str | None, context_name: str | None = None, default_users: dict[str, str] | None = None,
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
        sandbox_envs: dict[str, K8sSandboxEnvironment] = {}
        for pod_name, pod in pods.items():
          default_user = default_users.get(pod_name) if default_users else None
          sandbox_envs[pod_name] = K8sSandboxEnvironment(
            release,
            pod,
            config=K8sSandboxEnvironmentConfig(
              default_user=default_user,
            ),
          )
        yield sandbox_envs
    finally:
        await release.uninstall(quiet=True)

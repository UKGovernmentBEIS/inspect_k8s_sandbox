from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncGenerator, cast

from k8s_sandbox._sandbox_environment import (
    K8sSandboxEnvironment,
    K8sSandboxEnvironmentConfig,
)


@asynccontextmanager
async def install_sandbox_environments(
    task_name: str,
    values_filename: str | None,
    context_name: str | None = None,
    default_user: str | None = None,
) -> AsyncGenerator[dict[str, K8sSandboxEnvironment], None]:
    values_path = (
        Path(__file__).parent / "resources" / values_filename
        if values_filename
        else None
    )
    config = K8sSandboxEnvironmentConfig(
        values=values_path, context=context_name, default_user=default_user
    )
    try:
        envs = cast(
            dict[str, K8sSandboxEnvironment],
            await K8sSandboxEnvironment.sample_init(task_name, config, {}),
        )
        yield envs
    finally:
        try:
            sandbox = next(iter(envs.values()))
            await sandbox.release.uninstall(quiet=True)
        except UnboundLocalError:
            # Release wasn't successfully installed.
            pass

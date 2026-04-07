import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncGenerator, Literal, cast

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
    restarted_container_behavior: Literal["warn", "raise"] = "warn",
) -> AsyncGenerator[dict[str, K8sSandboxEnvironment], None]:
    values_path = (
        Path(__file__).parent / "resources" / values_filename
        if values_filename
        else None
    )
    config = K8sSandboxEnvironmentConfig(
        values=values_path,
        context=context_name,
        default_user=default_user,
        restarted_container_behavior=restarted_container_behavior,
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


async def assert_proper_ports_are_open(
    sandbox_env: K8sSandboxEnvironment,
    host_to_mapped_ports,
    retries: int = 3,
    retry_delay: float = 2.0,
) -> None:
    hostname = host_to_mapped_ports["host"]
    open_ports = host_to_mapped_ports["open_ports"]
    closed_ports = host_to_mapped_ports["closed_ports"]

    for attempt in range(retries):
        expected_open_results = [
            await sandbox_env.exec(
                ["nc", "-vz", "-w", "5", hostname, open_port], timeout=10
            )
            for open_port in open_ports
        ]
        expected_closed_results = [
            await sandbox_env.exec(
                ["nc", "-vz", "-w", "5", hostname, closed_port], timeout=10
            )
            for closed_port in closed_ports
        ]

        open_ok = all(r.returncode == 0 for r in expected_open_results)
        closed_ok = all(r.returncode != 0 for r in expected_closed_results)

        if open_ok and closed_ok:
            return

        if attempt < retries - 1:
            await asyncio.sleep(retry_delay)

    for result in expected_open_results:
        assert result.returncode == 0

    for result in expected_closed_results:
        assert result.returncode != 0

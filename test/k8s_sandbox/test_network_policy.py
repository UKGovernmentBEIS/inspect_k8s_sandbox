from typing import AsyncGenerator

import pytest
import pytest_asyncio

from k8s_sandbox._sandbox_environment import K8sSandboxEnvironment
from test.k8s_sandbox.utils import install_sandbox_environments

# Mark all tests in this module as requiring a Kubernetes cluster.
pytestmark = pytest.mark.req_k8s


@pytest_asyncio.fixture(scope="module")
async def sandbox() -> AsyncGenerator[K8sSandboxEnvironment, None]:
    async with install_sandbox_environments(__file__, "netpol-values.yaml") as envs:
        yield envs["default"]


@pytest_asyncio.fixture(scope="module")
async def sandbox_entities_world() -> AsyncGenerator[K8sSandboxEnvironment, None]:
    async with install_sandbox_environments(
        __file__, "netpol-world-values.yaml"
    ) as envs:
        yield envs["default"]


async def test_allowed_fqdn(sandbox: K8sSandboxEnvironment) -> None:
    result = await sandbox.exec(["curl", "-I", "https://google.com"], timeout=10)

    assert result.returncode == 0


async def test_blocked_fqdn(sandbox: K8sSandboxEnvironment) -> None:
    with pytest.raises(TimeoutError):
        await sandbox.exec(["wget", "https://yahoo.com"], timeout=10)


async def test_allowed_cidr(sandbox: K8sSandboxEnvironment) -> None:
    result = await sandbox.exec(["curl", "-I", "1.1.1.1"], timeout=10)

    assert result.returncode == 0


async def test_blocked_cidr(sandbox: K8sSandboxEnvironment) -> None:
    with pytest.raises(TimeoutError):
        await sandbox.exec(["curl", "-I", "8.8.8.8"], timeout=10)


async def test_allowed_entity(sandbox_entities_world: K8sSandboxEnvironment) -> None:
    # allowEntities: ["world"]
    result = await sandbox_entities_world.exec(["curl", "-I", "yahoo.com"], timeout=10)

    assert result.returncode == 0


async def test_pip_install(sandbox: K8sSandboxEnvironment) -> None:
    result = await sandbox.exec(
        [
            "bash",
            "-c",
            "pip install --no-cache-dir --no-input requests > /dev/null 2>&1 && "
            "echo 'success' || echo 'failed'",
        ],
        # Test occasionally failed with TimeoutError when timeout is set to 10
        timeout=30,
    )

    assert result.stdout.strip() == "success"

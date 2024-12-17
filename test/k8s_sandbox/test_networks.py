from typing import AsyncGenerator

import pytest
import pytest_asyncio

from aisitools.k8s_sandbox._sandbox_environment import K8sSandboxEnvironment
from test.aisitools.k8s_sandbox.utils import install_sandbox_environments

# Mark all tests in this module as requiring a Kubernetes cluster.
pytestmark = pytest.mark.req_k8s


@pytest_asyncio.fixture(scope="module")
async def sandbox() -> AsyncGenerator[K8sSandboxEnvironment, None]:
    async with install_sandbox_environments(__file__, "networks-values.yaml") as envs:
        yield envs["default"]


async def can_ping_service(service: str, sandbox: K8sSandboxEnvironment) -> bool:
    result = await sandbox.exec(["ping", "-c", "1", service], timeout=3)
    return "1 packets transmitted, 1 received" in result.stdout


async def test_can_ping_service_on_same_network(
    sandbox: K8sSandboxEnvironment,
):
    assert await can_ping_service("victim1", sandbox)


async def test_cannot_ping_service_on_no_network(
    sandbox: K8sSandboxEnvironment,
):
    assert not await can_ping_service("victim2", sandbox)


async def test_cannot_ping_service_on_different_network(
    sandbox: K8sSandboxEnvironment,
):
    assert not await can_ping_service("victim3", sandbox)

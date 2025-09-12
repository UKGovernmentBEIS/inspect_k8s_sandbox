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


@pytest_asyncio.fixture(scope="module")
async def sandbox_ports() -> AsyncGenerator[K8sSandboxEnvironment, None]:
    async with install_sandbox_environments(__file__, "ports-values.yaml") as envs:
        yield envs["default"]


async def test_allowed_fqdn(sandbox: K8sSandboxEnvironment) -> None:
    result = await sandbox.exec(["curl", "-I", "https://google.com"], timeout=10)

    assert result.returncode == 0


async def test_allowed_fqdn_dns_lookup(sandbox: K8sSandboxEnvironment) -> None:
    result = await sandbox.exec(["getent", "hosts", "google.com"], timeout=10)

    assert result.returncode == 0, result


async def test_blocked_fqdn(sandbox: K8sSandboxEnvironment) -> None:
    result = await sandbox.exec(["wget", "https://yahoo.com"], timeout=10)

    assert result.returncode == 4, result
    assert "Temporary failure in name resolution" in result.stderr
    # If this test is failing, it could be an issue with your cluster's Cilium
    # configuration which is not respecting the DNS rules in the egress policy.
    # E.g. you have an overly permissive egress policy that allows all DNS traffic.


async def test_blocked_fqdn_dns_lookup(sandbox: K8sSandboxEnvironment) -> None:
    result = await sandbox.exec(["getent", "hosts", "yahoo.com"], timeout=10)

    assert result.returncode == 2, result


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


async def test_allowed_entity_dns_lookup(
    sandbox_entities_world: K8sSandboxEnvironment,
) -> None:
    # allowEntities: ["world"]
    result = await sandbox_entities_world.exec(
        ["getent", "hosts", "yahoo.com"], timeout=10
    )

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


async def test_only_specified_ports_are_open(sandbox_ports: K8sSandboxEnvironment):
    open_port = "8080"
    closed_port = "9090"
    expected_open_result = await sandbox_ports.exec(
        ["nc", "-vz", "-w", "5", "target", open_port], timeout=10
    )
    expected_closed_result = await sandbox_ports.exec(
        ["nc", "-vz", "-w", "5", "target", closed_port], timeout=10
    )
    assert expected_open_result.returncode == 0
    assert expected_closed_result.returncode != 0

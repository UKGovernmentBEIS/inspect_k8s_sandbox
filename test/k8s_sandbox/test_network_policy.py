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


@pytest_asyncio.fixture(scope="module")
async def sandbox_ports_no_net() -> AsyncGenerator[K8sSandboxEnvironment, None]:
    async with install_sandbox_environments(
        __file__, "ports-no-net-values.yaml"
    ) as envs:
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


@pytest.mark.parametrize(
    "host_to_mapped_ports",
    [
        {"host": "ports-specified", "open_ports": ["8080"], "closed_ports": ["9090"]},
        {
            "host": "ports-not-specified",
            "open_ports": ["8080", "9090"],
            "closed_ports": [],
        },
        {
            "host": "ports-specified-diff-network",
            "open_ports": [],
            "closed_ports": ["8080", "9090"],
        },
    ],
)
async def test_only_specified_ports_are_open(
    sandbox_ports: K8sSandboxEnvironment, host_to_mapped_ports
):
    await assert_proper_ports_are_open(sandbox_ports, host_to_mapped_ports)


@pytest.mark.parametrize(
    "host_to_mapped_ports",
    [
        {"host": "ports-specified", "open_ports": ["8080"], "closed_ports": ["9090"]},
        {
            "host": "ports-not-specified",
            "open_ports": ["8080", "9090"],
            "closed_ports": [],
        },
    ],
)
async def test_only_specified_ports_are_open_no_networks(
    sandbox_ports_no_net: K8sSandboxEnvironment, host_to_mapped_ports
):
    await assert_proper_ports_are_open(sandbox_ports_no_net, host_to_mapped_ports)


async def assert_proper_ports_are_open(
    sandbox_env: K8sSandboxEnvironment, host_to_mapped_ports
) -> None:
    hostname = host_to_mapped_ports["host"]
    open_ports = host_to_mapped_ports["open_ports"]
    closed_ports = host_to_mapped_ports["closed_ports"]

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

    for result in expected_open_results:
        assert result.returncode == 0

    for result in expected_closed_results:
        assert result.returncode != 0

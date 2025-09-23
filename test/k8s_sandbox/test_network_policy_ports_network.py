from typing import AsyncGenerator

import pytest
import pytest_asyncio

from k8s_sandbox._sandbox_environment import K8sSandboxEnvironment
from test.k8s_sandbox.utils import (
    assert_proper_ports_are_open,
    install_sandbox_environments,
)

# Mark all tests in this module as requiring a Kubernetes cluster.
pytestmark = pytest.mark.req_k8s


@pytest_asyncio.fixture(scope="module")
async def sandbox_ports() -> AsyncGenerator[K8sSandboxEnvironment, None]:
    async with install_sandbox_environments(__file__, "ports-values.yaml") as envs:
        yield envs["default"]


@pytest.mark.parametrize(
    "host_to_mapped_ports",
    [
        {"host": "ports-specified", "open_ports": ["8080"], "closed_ports": ["9090"]},
        {
            "host": "ports-specified-two-networks",
            "open_ports": ["8080"],
            "closed_ports": ["9090"],
        },
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

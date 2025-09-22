from typing import AsyncGenerator

import pytest
import pytest_asyncio

from k8s_sandbox._sandbox_environment import K8sSandboxEnvironment
from test.k8s_sandbox.utils import install_sandbox_environments

from .utils import HostToMappedPorts, assert_proper_ports_are_open

# Mark all tests in this module as requiring a Kubernetes cluster.
pytestmark = pytest.mark.req_k8s

# This fixture & tests are split into their own module because they spin up sufficient
# pods that they cause scheduling issues when run alongside other tests in CI.


@pytest_asyncio.fixture(scope="module")
async def sandbox_ports_no_net() -> AsyncGenerator[K8sSandboxEnvironment, None]:
    async with install_sandbox_environments(
        __file__, "ports-no-net-values.yaml"
    ) as envs:
        yield envs["default"]


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
    sandbox_ports_no_net: K8sSandboxEnvironment, host_to_mapped_ports: HostToMappedPorts
):
    await assert_proper_ports_are_open(sandbox_ports_no_net, host_to_mapped_ports)

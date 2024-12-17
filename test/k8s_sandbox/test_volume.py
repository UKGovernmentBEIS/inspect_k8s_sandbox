from typing import AsyncGenerator

import pytest
import pytest_asyncio

from k8s_sandbox._sandbox_environment import K8sSandboxEnvironment
from test.k8s_sandbox.utils import install_sandbox_environments

# Mark all tests in this module as requiring a Kubernetes cluster.
pytestmark = pytest.mark.req_k8s


@pytest_asyncio.fixture(scope="module")
async def sandbox() -> AsyncGenerator[K8sSandboxEnvironment, None]:
    async with install_sandbox_environments(__file__, "volume-values.yaml") as envs:
        yield envs["default"]


async def test_volumes(sandbox: K8sSandboxEnvironment):
    result = await sandbox.read_file("/mount/test.txt")

    assert result == "test\n"

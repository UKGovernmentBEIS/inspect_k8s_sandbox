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


@pytest_asyncio.fixture(scope="module")
async def image_volume_sandbox() -> AsyncGenerator[K8sSandboxEnvironment, None]:
    async with install_sandbox_environments(
        __file__, "image-volume-compose.yaml"
    ) as envs:
        yield envs["default"]


async def test_service_extension_image_volume(
    image_volume_sandbox: K8sSandboxEnvironment,
):
    # Exercises the x-inspect_k8s_sandbox 'volumes'/'volumeMounts' extension end to
    # end: Compose extension -> converter -> Helm chart -> a real Kubernetes OCI
    # image volume (KEP-4639) mounted into the pod.
    result = await image_volume_sandbox.read_file("/mnt/alpine/etc/alpine-release")

    assert result.startswith("3.19.")

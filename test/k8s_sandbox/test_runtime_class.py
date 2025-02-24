from typing import AsyncGenerator

import pytest
import pytest_asyncio

from k8s_sandbox._sandbox_environment import K8sSandboxEnvironment
from test.k8s_sandbox.utils import install_sandbox_environments

# Mark all tests in this module as requiring a Kubernetes cluster.
pytestmark = pytest.mark.req_k8s


@pytest_asyncio.fixture(scope="module")
async def sandboxes() -> AsyncGenerator[dict[str, K8sSandboxEnvironment], None]:
    async with install_sandbox_environments(
        __file__, "runtime-class-values.yaml"
    ) as envs:
        yield envs


async def test_default(sandboxes: dict[str, K8sSandboxEnvironment]) -> None:
    actual = await _infer_runtime_class(sandboxes["default"])

    assert actual == "gvisor"


async def test_gvisor_specified(sandboxes: dict[str, K8sSandboxEnvironment]) -> None:
    actual = await _infer_runtime_class(sandboxes["gvisor-specified"])

    assert actual == "gvisor"


async def test_runc_specified(sandboxes: dict[str, K8sSandboxEnvironment]) -> None:
    actual = await _infer_runtime_class(sandboxes["runc-specified"])

    assert actual == "runc"


async def test_unspecified(sandboxes: dict[str, K8sSandboxEnvironment]) -> None:
    actual = await _infer_runtime_class(sandboxes["unspecified"])

    assert actual == "gvisor"


async def test_cluster_default_magic_string(
    sandboxes: dict[str, K8sSandboxEnvironment],
) -> None:
    actual = await _infer_runtime_class(sandboxes["cluster-default-magic-string"])

    # The "CLUSTER_DEFAULT" magic string means that runtimeClassName won't be set. runc
    # is the default runtime on the minikube test cluster.
    assert actual == "runc"


async def _infer_runtime_class(sandbox: K8sSandboxEnvironment) -> str:
    result = await sandbox.exec(
        ["sh", "-c", "dmesg | grep -i 'starting gvisor'"], timeout=5
    )
    return "gvisor" if result.returncode == 0 else "runc"

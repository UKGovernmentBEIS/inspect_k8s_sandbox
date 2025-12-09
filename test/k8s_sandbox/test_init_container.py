import logging
import subprocess
import time
from subprocess import CompletedProcess
from typing import AsyncGenerator

import pytest
import pytest_asyncio

from k8s_sandbox._sandbox_environment import K8sSandboxEnvironment
from test.k8s_sandbox.utils import install_sandbox_environments

logger = logging.getLogger(__name__)

pytestmark = pytest.mark.req_k8s


@pytest_asyncio.fixture(scope="module")
async def sandbox() -> AsyncGenerator[K8sSandboxEnvironment, None]:
    async with install_sandbox_environments(
        __file__, "init-container-values.yaml"
    ) as envs:
        yield envs["default"]


def wait_and_get_log(
    pod_name: str, container_name: str, timeout: int = 30
) -> CompletedProcess[str]:
    """Wait for logs to be nonempty or raise a timeout."""
    start = time.time()
    while True:
        result = subprocess.run(
            ["kubectl", "logs", pod_name, "-c", container_name],
            capture_output=True,
            text=True,
        )
        if result.stdout != "":
            return result
        if time.time() - start > timeout:
            raise TimeoutError(f"Expected nonempty log but found {result=}")
        time.sleep(1)


async def test_bar_waits_for_foo(sandbox: K8sSandboxEnvironment):
    """
    Ensure dependencies are respected with initContainers.

    - foo delays before opening port 3306
    - bar has initContainer that waits for foo
    - bar main container only runs after foo is ready.
    This occurs during install_sandbox_environments(...)
    """
    bar_pod_name = f"agent-env-{sandbox.release.release_name}-bar-0"

    init_container_logs = wait_and_get_log(bar_pod_name, "wait-for-foo-connectivity")
    assert (
        "Waiting for foo..." in init_container_logs.stdout
        and "foo is up" in init_container_logs.stdout
    ), f"Unexpected logs in bar init container {init_container_logs.stdout}"
    container_logs = wait_and_get_log(bar_pod_name, "bar")
    expected = "Initialisation can run now"
    actual = container_logs.stdout
    assert expected in actual, f"Unexpected logs in bar: {actual}"

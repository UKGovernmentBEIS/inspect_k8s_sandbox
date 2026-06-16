from typing import AsyncGenerator

import pytest
import pytest_asyncio
from inspect_ai.util._sandbox.self_check import *  # noqa: F401, F403

from k8s_sandbox._sandbox_environment import K8sSandboxEnvironment
from test.k8s_sandbox.utils import install_sandbox_environments

pytestmark = pytest.mark.req_k8s

# Known failures per sandbox, applied as strict xfails in the sandbox_env fixture
# (keyed on the running check's function name). Strict means a check that starts
# passing here is surfaced rather than silently ignored.
_ROOT_XFAILS = {
    # Running as a nonexistent user raises (by design) instead of returning a
    # failed ExecResult, which is what the check expects.
    "test_exec_as_nonexistent_user": "k8s raises for a nonexistent user",
    # Root can read/write regardless of the permission bits.
    "test_read_file_not_allowed": "root can read after chmod -r",
    "test_write_text_file_without_permissions": "root can write after chmod -w",
    "test_write_binary_file_without_permissions": "root can write after chmod -w",
}
_NON_ROOT_XFAILS = {
    "test_exec_as_nonexistent_user": "k8s raises for a nonexistent user",
    # In k8s the container must run as root to exec as a different user.
    "test_exec_as_user": "container must run as root to exec as a different user",
}


@pytest_asyncio.fixture(scope="module")
async def sandboxes() -> AsyncGenerator[dict[str, K8sSandboxEnvironment], None]:
    async with install_sandbox_environments(__file__, "values.yaml") as envs:
        yield envs


@pytest_asyncio.fixture(
    params=[("default", _ROOT_XFAILS), ("nonroot", _NON_ROOT_XFAILS)],
    ids=["root", "non-root"],
)
async def sandbox_env(
    request: pytest.FixtureRequest, sandboxes: dict[str, K8sSandboxEnvironment]
) -> K8sSandboxEnvironment:
    key, xfails = request.param
    reason = xfails.get(request.node.originalname)
    if reason is not None:
        request.node.add_marker(pytest.mark.xfail(reason=reason, strict=True))
    return sandboxes[key]

from typing import AsyncGenerator

import pytest_asyncio
from inspect_ai.util import SandboxEnvironment
from inspect_ai.util._sandbox.self_check import self_check

from k8s_sandbox._sandbox_environment import K8sSandboxEnvironment
from test.k8s_sandbox.utils import install_sandbox_environments


@pytest_asyncio.fixture(scope="module")
async def sandboxes() -> AsyncGenerator[dict[str, K8sSandboxEnvironment], None]:
    async with install_sandbox_environments(__file__, "values.yaml") as envs:
        yield envs


@pytest_asyncio.fixture(scope="module")
async def root(sandboxes: dict[str, K8sSandboxEnvironment]) -> K8sSandboxEnvironment:
    return sandboxes["default"]


@pytest_asyncio.fixture(scope="module")
async def non_root(
    sandboxes: dict[str, K8sSandboxEnvironment],
) -> K8sSandboxEnvironment:
    return sandboxes["nonroot"]


async def test_self_check_k8s_default_root(root: SandboxEnvironment) -> None:
    known_failures = [
        # The user parameter is not supported in K8sSandboxEnvironment.
        "test_exec_as_user",
        "test_exec_as_nonexistent_user",
        # Root can read from files after `chmod -r`.
        "test_read_file_not_allowed",
        # Root can write to files after `chmod -w`.
        "test_write_text_file_without_permissions",
        "test_write_binary_file_without_permissions",
    ]

    return await _run_self_check(root, known_failures)


async def test_self_check_k8s_non_root(non_root: SandboxEnvironment) -> None:
    known_failures = [
        # The user parameter is not supported in K8sSandboxEnvironment.
        "test_exec_as_user",
        "test_exec_as_nonexistent_user",
    ]

    return await _run_self_check(non_root, known_failures)


async def _run_self_check(
    sandbox_env: SandboxEnvironment, known_failures: list[str] = []
) -> None:
    """Self-check is the name of Inspect's test suite which runs against sandboxes."""
    self_check_results = await self_check(sandbox_env)
    failures = []
    for test_name, result in self_check_results.items():
        if result is not True and test_name not in known_failures:
            failures.append(f"Test {test_name} failed: {result}")
    if failures:
        assert False, "\n".join(failures)

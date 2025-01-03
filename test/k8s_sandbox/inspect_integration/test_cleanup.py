import asyncio
from unittest.mock import patch

import pytest
from inspect_ai import Task
from inspect_ai.model import Model

from k8s_sandbox._helm import uninstall
from k8s_sandbox._kubernetes_api import get_current_context_namespace
from k8s_sandbox._sandbox_environment import K8sSandboxEnvironment
from test.k8s_sandbox.inspect_integration.testing_utils.mock_model import (
    MockToolCallModel,
)
from test.k8s_sandbox.inspect_integration.testing_utils.utils import (
    create_task,
    run_and_verify_inspect_eval,
    tool_call,
)

# Mark all tests in this module as requiring a Kubernetes cluster.
pytestmark = pytest.mark.req_k8s


@pytest.fixture
def model() -> Model:
    return MockToolCallModel.from_tool_call(
        tool_call("bash", {"cmd": "echo 'success'"})
    )


@pytest.fixture
def task() -> Task:
    return create_task(__file__, target="success")


def test_with_cleanup(model: Model, task: Task) -> None:
    with patch("k8s_sandbox._helm.uninstall", wraps=uninstall) as spy:
        run_and_verify_inspect_eval(task=task, model=model)

    assert spy.call_count == 1


def test_without_cleanup(model: Model, task: Task) -> None:
    release = "no-clean"

    with patch(
        "k8s_sandbox._helm.Release._generate_release_name",
        return_value=release,
    ):
        with patch("k8s_sandbox._helm.uninstall", wraps=uninstall) as spy:
            run_and_verify_inspect_eval(task=task, model=model, sandbox_cleanup=False)

    assert spy.call_count == 0
    asyncio.run(uninstall(release, get_current_context_namespace(), quiet=False))


def test_cli_cleanup_all_gets_user_confirmation(model: Model, task: Task) -> None:
    release = "no-clean"
    with patch(
        "k8s_sandbox._helm.Release._generate_release_name",
        return_value=release,
    ):
        run_and_verify_inspect_eval(task=task, model=model, sandbox_cleanup=False)

    with patch("k8s_sandbox._helm.uninstall", wraps=uninstall) as spy:
        # We don't want to actually uninstall all releases in this test (the test could
        # be run on a production cluster).
        with patch("rich.prompt.Confirm.ask", return_value=False) as confirm:
            asyncio.run(K8sSandboxEnvironment.cli_cleanup(id=None))

    assert "Are you sure you want to uninstall ALL" in confirm.call_args.args[0]
    assert spy.call_count == 0
    asyncio.run(uninstall(release, get_current_context_namespace(), quiet=False))

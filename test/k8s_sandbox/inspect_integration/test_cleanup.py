import asyncio
from unittest.mock import patch

import pytest

from aisitools.k8s_sandbox._helm import uninstall
from test.aisitools.k8s_sandbox.inspect_integration.testing_utils.mock_model import (
    MockToolCallModel,
)
from test.aisitools.k8s_sandbox.inspect_integration.testing_utils.utils import (
    create_task,
    run_and_verify_inspect_eval,
    tool_call,
)

# Mark all tests in this module as requiring a Kubernetes cluster.
pytestmark = pytest.mark.req_k8s


def test_with_cleanup() -> None:
    model = MockToolCallModel([tool_call("bash", {"cmd": "echo 'success'"})])
    task = create_task(__file__, target="success")

    with patch("aisitools.k8s_sandbox._helm.uninstall", wraps=uninstall) as spy:
        run_and_verify_inspect_eval(task=task, model=model)

    assert spy.call_count == 1


def test_without_cleanup() -> None:
    model = MockToolCallModel([tool_call("bash", {"cmd": "echo 'success'"})])
    task = create_task(__file__, target="success")
    release = "no-clean"

    with patch(
        "aisitools.k8s_sandbox._helm.Release._generate_release_name",
        return_value=release,
    ):
        with patch("aisitools.k8s_sandbox._helm.uninstall", wraps=uninstall) as spy:
            run_and_verify_inspect_eval(task=task, model=model, sandbox_cleanup=False)

    assert spy.call_count == 0
    asyncio.run(uninstall(release, quiet=False))

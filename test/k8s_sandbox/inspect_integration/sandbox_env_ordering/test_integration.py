import pytest
from inspect_ai.tool import bash

from test.aisitools.k8s_sandbox.inspect_integration.testing_utils.mock_model import (
    MockToolCallModel,
)
from test.aisitools.k8s_sandbox.inspect_integration.testing_utils.utils import (
    create_task,
    run_and_verify_inspect_eval,
    tool_call,
)


@pytest.mark.req_k8s
def test_sandbox_env_ordering() -> None:
    model = MockToolCallModel(
        [tool_call("bash", {"cmd": "echo $SERVICE_NAME"})],
    )
    task = create_task(__file__, target="default", tools=[bash()])

    result = run_and_verify_inspect_eval(task=task, model=model)[0]

    assert result.scores is not None
    assert result.scores["match"].value == "C"

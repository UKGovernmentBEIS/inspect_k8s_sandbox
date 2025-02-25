import pytest
from inspect_ai.tool import bash

from test.k8s_sandbox.inspect_integration.testing_utils.mock_model import (
    MockToolCallModel,
)
from test.k8s_sandbox.inspect_integration.testing_utils.utils import (
    create_task,
    run_and_verify_inspect_eval,
    tool_call,
)


@pytest.mark.req_k8s
def test_default_chart_with_docker_compose_yaml() -> None:
    model = MockToolCallModel(
        [tool_call("bash", {"cmd": "echo $TEST_ENV_VAR"})],
    )
    task = create_task(
        __file__,
        target="from_docker_compose",
        tools=[bash()],
        sandbox=("k8s", "compose.yaml"),
    )

    result = run_and_verify_inspect_eval(task=task, model=model)[0]

    assert result.scores is not None
    assert result.scores["match"].value == "C"

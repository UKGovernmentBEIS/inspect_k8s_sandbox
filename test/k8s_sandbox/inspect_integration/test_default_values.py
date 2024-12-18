import pytest

from test.k8s_sandbox.inspect_integration.testing_utils.mock_model import (
    MockToolCallModel,
)
from test.k8s_sandbox.inspect_integration.testing_utils.utils import (
    create_task,
    run_and_verify_inspect_eval,
    tool_call,
)


@pytest.mark.req_k8s
def test_default_chart_with_no_values() -> None:
    model = MockToolCallModel(
        [tool_call("bash", {"cmd": "cat /etc/os-release | grep VERSION_CODENAME"})],
    )
    task = create_task(__file__, target="bookworm")

    result = run_and_verify_inspect_eval(task=task, model=model)[0]

    assert result.scores is not None
    assert result.scores["match"].value == "C"

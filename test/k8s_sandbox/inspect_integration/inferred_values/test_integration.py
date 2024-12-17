import pytest

from test.aisitools.k8s_sandbox.inspect_integration.testing_utils.mock_model import (
    MockToolCallModel,
)
from test.aisitools.k8s_sandbox.inspect_integration.testing_utils.utils import (
    create_task,
    run_and_verify_inspect_eval,
    tool_call,
)


@pytest.mark.req_k8s
def test_default_chart_with_inferred_values_yaml() -> None:
    # Verify that the `values.yaml` file in the task dir is automatically used.
    model = MockToolCallModel(
        [tool_call("bash", {"cmd": "cat /etc/os-release | grep VERSION_CODENAME"})],
    )
    task = create_task(__file__, target="bullseye")

    result = run_and_verify_inspect_eval(task=task, model=model)[0]

    assert result.scores is not None
    assert result.scores["match"].value == "C"

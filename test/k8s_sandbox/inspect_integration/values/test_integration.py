import pytest
from inspect_ai.model import Model

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


@pytest.fixture
def model() -> MockToolCallModel:
    return MockToolCallModel(
        [tool_call("bash", {"cmd": "echo $VALUES_SOURCE"})],
    )


def test_default_chart_with_inferred_values_yaml(model: Model) -> None:
    task = create_task(__file__, target="values.yaml")

    result = run_and_verify_inspect_eval(task=task, model=model)[0]

    assert result.scores is not None
    assert result.scores["match"].value == "C"


def test_default_chart_with_explicit_values_yaml(model: Model) -> None:
    task = create_task(
        __file__, target="my-values.yaml", sandbox=("k8s", "my-values.yaml")
    )

    result = run_and_verify_inspect_eval(task=task, model=model)[0]

    assert result.scores is not None
    assert result.scores["match"].value == "C"

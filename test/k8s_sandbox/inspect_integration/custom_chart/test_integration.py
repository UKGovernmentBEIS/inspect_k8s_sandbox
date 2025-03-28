from pathlib import Path

import pytest
from inspect_ai import eval
from inspect_ai.model import Model
from inspect_ai.util import SandboxEnvironmentSpec

from k8s_sandbox import K8sSandboxEnvironmentConfig
from k8s_sandbox._kubernetes_api import get_current_context_name
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
    return MockToolCallModel(
        [tool_call("bash", {"cmd": "echo $POD_ENV_VAR"})],
    )


def test_custom_chart_default_values(model: Model) -> None:
    task = create_task(
        __file__,
        target="chart-default",
        sandbox=SandboxEnvironmentSpec(
            "k8s", K8sSandboxEnvironmentConfig(chart="my-custom-chart")
        ),
    )

    result = run_and_verify_inspect_eval(task=task, model=model)[0]

    assert result.scores is not None
    assert result.scores["match"].value == "C"


def test_custom_chart_custom_values(model: Model) -> None:
    task = create_task(
        __file__,
        target="overridden-by-values",
        sandbox=SandboxEnvironmentSpec(
            "k8s",
            K8sSandboxEnvironmentConfig(
                chart="my-custom-chart", values=Path("values.yaml")
            ),
        ),
    )

    result = run_and_verify_inspect_eval(task=task, model=model)[0]

    assert result.scores is not None
    assert result.scores["match"].value == "C"


def test_specified_kube_config_context() -> None:
    # We don't have a test to verify that Helm charts can be installed into non-current
    # contexts as we don't assume that the environment running this test has access to
    # multiple Kubernetes clusters.
    current_context = get_current_context_name()
    model = MockToolCallModel(
        [tool_call("bash", {"cmd": "cat /etc/os-release | grep VERSION_CODENAME"})],
    )
    task = create_task(
        __file__,
        target="bookworm",
        sandbox=SandboxEnvironmentSpec(
            "k8s",
            K8sSandboxEnvironmentConfig(context=current_context),
        ),
    )

    result = run_and_verify_inspect_eval(task=task, model=model)[0]

    assert result.scores is not None
    assert result.scores["match"].value == "C"


def test_specified_kube_config_context_flows_to_error_message(model: Model) -> None:
    # Verify that the specified context flows through to the error message.
    task = create_task(
        __file__,
        target="bookworm",
        sandbox=SandboxEnvironmentSpec(
            "k8s",
            K8sSandboxEnvironmentConfig(context="does-not-exist"),
        ),
    )

    result = eval(task, model=model)[0]

    assert result.status == "error"
    assert result.error is not None
    assert (
        "Could not find a context named 'does-not-exist' in the kubeconfig file."
        in result.error.message
    )

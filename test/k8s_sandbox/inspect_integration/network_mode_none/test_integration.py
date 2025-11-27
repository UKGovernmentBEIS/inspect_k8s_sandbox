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
def test_normal_services_can_communicate() -> None:
    """Test that normal services can reach other services via DNS."""
    model = MockToolCallModel(
        [tool_call("bash", {"cmd": "getent hosts other-service"})],
    )
    task = create_task(
        __file__,
        target="other-service",
        tools=[bash()],
        sandbox=("k8s", "compose.yaml"),
    )

    result = run_and_verify_inspect_eval(task=task, model=model)[0]

    assert result.scores is not None
    assert result.scores["match"].value == "C"


@pytest.mark.req_k8s
def test_isolated_service_cannot_communicate() -> None:
    """Test that an isolated service (network_mode: none) cannot reach other services."""
    model = MockToolCallModel(
        # getent hosts will fail since network is isolated
        [tool_call("bash", {"cmd": "getent hosts other-service || echo 'DNS lookup failed'"})],
    )
    task = create_task(
        __file__,
        target="DNS lookup failed",
        tools=[bash()],
        sandbox=("k8s", "isolated-compose.yaml"),
    )

    result = run_and_verify_inspect_eval(task=task, model=model)[0]

    assert result.scores is not None
    assert result.scores["match"].value == "C"

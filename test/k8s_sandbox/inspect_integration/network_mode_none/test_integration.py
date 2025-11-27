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
    """Test that normal services can resolve and connect to other services."""
    # Try TCP connection - "Connection refused" means we reached the host (network reachable)
    cmd = "python -c \"import socket; s=socket.socket(); s.settimeout(5); s.connect(('other-service', 80))\" 2>&1 | grep -q 'refused' && echo 'Network reachable' || echo 'Network blocked'"
    model = MockToolCallModel(
        [tool_call("bash", {"cmd": cmd})],
    )
    task = create_task(
        __file__,
        target="Network reachable",
        tools=[bash()],
        sandbox=("k8s", "compose.yaml"),
    )

    result = run_and_verify_inspect_eval(task=task, model=model)[0]

    assert result.scores is not None
    assert result.scores["match"].value == "C"


@pytest.mark.req_k8s
def test_isolated_service_cannot_communicate() -> None:
    """Test that an isolated service (network_mode: none) cannot connect to other services."""
    # Try TCP connection - timeout or other network error (not "refused") means blocked
    cmd = "python -c \"import socket; s=socket.socket(); s.settimeout(5); s.connect(('other-service', 80))\" 2>&1 | grep -q 'refused' && echo 'Network reachable' || echo 'Network blocked'"
    model = MockToolCallModel(
        [tool_call("bash", {"cmd": cmd})],
    )
    task = create_task(
        __file__,
        target="Network blocked",
        tools=[bash()],
        sandbox=("k8s", "isolated-compose.yaml"),
    )

    result = run_and_verify_inspect_eval(task=task, model=model)[0]

    assert result.scores is not None
    assert result.scores["match"].value == "C"

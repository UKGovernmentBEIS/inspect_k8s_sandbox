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
    """Test that normal services can connect to other services."""
    model = MockToolCallModel(
        [tool_call("bash", {"cmd": _network_test_cmd("other-service")})],
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
    """Test that an isolated service (network_mode: none) cannot connect."""
    model = MockToolCallModel(
        [tool_call("bash", {"cmd": _network_test_cmd("other-service")})],
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


def _network_test_cmd(host: str, port: int = 80, timeout: int = 5) -> str:
    """Returns a command that prints 'Network reachable' or 'Network blocked'."""
    return f"""python -c "
import socket
s = socket.socket()
s.settimeout({timeout})
try:
    s.connect(('{host}', {port}))
    print('Network reachable')
except ConnectionRefusedError:
    print('Network reachable')
except Exception:
    print('Network blocked')
"
"""

"""Integration tests for default service resolution.

Tests the three scenarios from Inspect's sandboxing doc:
https://inspect.aisi.org.uk/sandboxing.html#multiple-environments

1. Service named "default" takes precedence
2. Service with x-default: true is used as default
3. First service is used as fallback
"""

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
def test_explicit_default_service_takes_precedence() -> None:
    """Test that a service named "default" takes precedence over x-default."""
    model = MockToolCallModel(
        [tool_call("bash", {"cmd": "echo $SERVICE_NAME"})],
    )
    task = create_task(
        __file__,
        target="default",
        tools=[bash()],
        sandbox=("k8s", "explicit-default-compose.yaml"),
    )

    result = run_and_verify_inspect_eval(task=task, model=model)[0]

    assert result.scores is not None
    assert result.scores["match"].value == "C"


@pytest.mark.req_k8s
def test_x_default_service() -> None:
    """Test that x-default: true marks a service as the default sandbox."""
    model = MockToolCallModel(
        [tool_call("bash", {"cmd": "echo $SERVICE_NAME"})],
    )
    task = create_task(
        __file__,
        target="agent",
        tools=[bash()],
        sandbox=("k8s", "x-default-compose.yaml"),
    )

    result = run_and_verify_inspect_eval(task=task, model=model)[0]

    assert result.scores is not None
    assert result.scores["match"].value == "C"


@pytest.mark.req_k8s
def test_first_service_as_fallback() -> None:
    """Test that the first service is used when no default or x-default exists."""
    model = MockToolCallModel(
        [tool_call("bash", {"cmd": "echo $SERVICE_NAME"})],
    )
    task = create_task(
        __file__,
        target="first",
        tools=[bash()],
        sandbox=("k8s", "first-service-compose.yaml"),
    )

    result = run_and_verify_inspect_eval(task=task, model=model)[0]

    assert result.scores is not None
    assert result.scores["match"].value == "C"

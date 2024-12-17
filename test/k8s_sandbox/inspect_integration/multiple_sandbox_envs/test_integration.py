import pytest
from inspect_ai.tool import Tool, ToolError, tool
from inspect_ai.util import sandbox

from test.aisitools.k8s_sandbox.inspect_integration.testing_utils.mock_model import (
    MockToolCallModel,
)
from test.aisitools.k8s_sandbox.inspect_integration.testing_utils.utils import (
    create_task,
    run_and_verify_inspect_eval,
    tool_call,
)


@pytest.mark.req_k8s
def test_other_sandbox_env() -> None:
    model = MockToolCallModel(
        [tool_call("bash_other", {"cmd": "echo $TEST_ENV_VAR"})],
    )
    task = create_task(__file__, target="other", tools=[bash_other()])

    result = run_and_verify_inspect_eval(task=task, model=model)[0]

    assert result.scores is not None
    assert result.scores["match"].value == "C"


@tool
def bash_other() -> Tool:
    async def execute(cmd: str) -> str:
        """
        Execute a bash command.

        Args:
          cmd (str): The bash command to execute.

        Returns:
          The output of the command.
        """
        result = await sandbox("other").exec(cmd=["bash", "-c", cmd])
        if result.success:
            return result.stdout
        else:
            raise ToolError(result.stderr)

    return execute

import os
from uuid import uuid4

from inspect_ai import Task, eval
from inspect_ai.dataset import MemoryDataset, Sample
from inspect_ai.log import EvalSample
from inspect_ai.model import (
    ChatCompletionChoice,
    ChatMessageAssistant,
    Model,
    ModelOutput,
)
from inspect_ai.scorer import match
from inspect_ai.solver import generate, use_tools
from inspect_ai.tool import Tool, ToolCall, bash
from inspect_ai.util import SandboxEnvironmentType


def create_task(
    test_dir: str,
    target: str = "",
    sandbox: SandboxEnvironmentType = "k8s",
    tools: list[Tool] | None = None,
) -> Task:
    return Task(
        MemoryDataset(
            samples=[
                Sample(
                    input="This is a test.",
                    target=target,
                )
            ],
            location=os.path.dirname(test_dir),
        ),
        solver=[
            use_tools(tools or [bash(timeout=10)]),
            generate(),
        ],
        name=test_dir,
        sandbox=sandbox,
        scorer=match(),
        max_messages=10,
    )


def run_and_verify_inspect_eval(
    task: Task, model: Model, sandbox_cleanup: bool = True
) -> list[EvalSample]:
    # Run the task with the cwd as the task directory. This allows Inspect to discover
    # any compose.yaml file and resolve relative file paths from challenge.yaml.
    assert task.dataset.location
    log_dir = os.path.join(os.getcwd(), "logs")
    with _ChangeDir(task.dataset.location):
        # log_level="SANDBOX" to facilitate test debugging.
        logs = eval(
            task,
            model=model,
            log_dir=log_dir,
            log_level="SANDBOX",
            sandbox_cleanup=sandbox_cleanup,
        )
    log = logs[0]
    assert log.status == "success"
    assert log.samples
    return log.samples


def tool_call(function: str, arguments: dict[str, str]) -> ToolCall:
    return ToolCall(
        id=uuid4().hex,
        function=function,
        arguments=arguments,
        type="function",
    )


def output_from_tool_call(tool_call: ToolCall) -> ModelOutput:
    return ModelOutput(
        choices=[
            ChatCompletionChoice(
                message=ChatMessageAssistant(
                    content="I'd like to use a tool.",
                    tool_calls=[tool_call],
                    source="generate",
                ),
                stop_reason="tool_calls",
            )
        ]
    )


class _ChangeDir:
    def __init__(self, new_path: str) -> None:
        self.new_path = new_path
        self.saved_path: str | None = None

    def __enter__(self) -> None:
        self.saved_path = os.getcwd()
        os.chdir(self.new_path)

    def __exit__(self, etype, value, traceback) -> None:  # type: ignore
        assert self.saved_path is not None
        os.chdir(self.saved_path)

from __future__ import annotations

from inspect_ai.model import (
    ChatMessage,
    GenerateConfig,
    Model,
    ModelAPI,
    ModelOutput,
    modelapi,
)
from inspect_ai.tool import ToolCall, ToolChoice, ToolInfo

from test.k8s_sandbox.inspect_integration.testing_utils.utils import (
    output_from_tool_call,
)


class MockToolCallModel(Model):
    """Requests a sequence of tool calls and outputs the result of the final call."""

    def __init__(self, tool_calls: list[ToolCall]) -> None:
        super().__init__(MockToolCallModelApi(tool_calls), GenerateConfig())

    @classmethod
    def from_tool_call(cls, tool_call: ToolCall) -> MockToolCallModel:
        return cls([tool_call])

    @classmethod
    def from_tool_calls(cls, tool_calls: list[ToolCall]) -> MockToolCallModel:
        return cls(tool_calls)


@modelapi(name="tool_call_model")
class MockToolCallModelApi(ModelAPI):
    def __init__(self, tool_calls: list[ToolCall]) -> None:
        super().__init__("tool_call_model", None, config=GenerateConfig())
        self._tool_calls = tool_calls

    async def generate(
        self,
        input: list[ChatMessage],
        tools: list[ToolInfo],
        tool_choice: ToolChoice,
        config: GenerateConfig,
    ) -> ModelOutput:
        if self._tool_calls:
            return output_from_tool_call(self._tool_calls.pop(0))
        # If we've used all the tools in the queue, response with the output of the last
        # tool call.
        last_tool_call_output = input[-1].text.strip()
        return ModelOutput.from_content("tool_call_model", last_tool_call_output)

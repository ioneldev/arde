"""Replies for FakeLLM that call tools, so a test can script an agent turn by turn."""

from itertools import count

from arde.application.ports import LLMResponse, ToolCall
from arde.domain.usage import Usage

USAGE = Usage(input_tokens=100, output_tokens=20, cache_read=0, cache_write=0)
_CALL_IDS = count(1)


def tool_call(name: str, **tool_input: object) -> ToolCall:
    return ToolCall(id=f"toolu_{next(_CALL_IDS)}", name=name, input=tool_input)


def calling(*tool_calls: ToolCall) -> LLMResponse:
    blocks = tuple(
        {"type": "tool_use", "id": call.id, "name": call.name, "input": call.input}
        for call in tool_calls
    )
    return LLMResponse(
        tool_calls=tool_calls,
        stop_reason="tool_use",
        usage=USAGE,
        latency_s=0.1,
        assistant_content=blocks,
    )


def tool_results_of(request_messages: tuple[dict[str, object], ...]) -> list[dict[str, object]]:
    """Every tool result the agent sent back to the model, in order."""
    return [
        block
        for message in request_messages
        if message["role"] == "user" and isinstance(message["content"], list)
        for block in message["content"]
        if isinstance(block, dict) and block.get("type") == "tool_result"
    ]

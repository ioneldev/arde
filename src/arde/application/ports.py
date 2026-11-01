"""The interfaces the application needs from the outside world. Adapters implement them."""

from collections.abc import Callable
from typing import Protocol

from pydantic import BaseModel

from arde.application.models_config import RoleConfig
from arde.domain.base import Frozen
from arde.domain.usage import Usage


class Tracer(Protocol):
    """Writes the run's record: structured events for the trace, free text for the debug log.

    Event kinds: run_start, step_start, step_end, model_call, tool_call, check_result, repair,
    correction, warning and run_end. The tracer stamps each event, so callers pass no time.
    """

    def event(self, kind: str, **fields: object) -> None: ...

    def debug(self, text: str) -> None: ...


class ToolDef(Frozen):
    name: str
    description: str
    input_schema: dict[str, object]
    strict: bool = True


class ToolCall(Frozen):
    id: str
    name: str
    input: dict[str, object]


class LLMRequest(Frozen):
    config: RoleConfig
    system: str
    messages: tuple[dict[str, object], ...]
    tools: tuple[ToolDef, ...]


class LLMResponse(Frozen):
    tool_calls: tuple[ToolCall, ...]
    text: str
    stop_reason: str
    usage: Usage
    latency_s: float
    # Sent back unchanged on the next turn, because the API checks thinking blocks byte for byte.
    assistant_content: tuple[object, ...]


class LLMClient(Protocol):
    def complete(self, request: LLMRequest) -> LLMResponse: ...

    def parse[T: BaseModel](
        self, request: LLMRequest, schema: type[T]
    ) -> tuple[T, LLMResponse]: ...


class ToolOutcome(Frozen):
    content: str
    is_error: bool = False
    submitted: object | None = None


class Tool(Frozen):
    definition: ToolDef
    handler: Callable[[dict[str, object]], ToolOutcome]

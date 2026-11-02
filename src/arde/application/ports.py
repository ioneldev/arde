"""The interfaces the application needs from the outside world. Adapters implement them."""

from collections.abc import Callable
from typing import Literal, Protocol

from pydantic import BaseModel

from arde.application.models_config import RoleConfig
from arde.domain.base import Frozen
from arde.domain.errors import ArdeError
from arde.domain.report import ExitCode
from arde.domain.usage import Usage


class Tracer(Protocol):
    """Writes the run's record: structured events for the trace, free text for the debug log.

    Event kinds: run_start, step_start, step_end, model_call, model_error, tool_call, correction,
    agent_end, check_result, repair, warning and run_end. The tracer stamps each event, so callers
    pass no time. A field named summary may be any length: the tracer cuts it, after redaction.
    """

    def event(self, kind: str, **fields: object) -> None: ...

    def debug(self, text: str) -> None: ...


# No default for strict: the API refuses some schemas in strict mode, so each tool decides.
class ToolDef(Frozen):
    name: str
    description: str
    input_schema: dict[str, object]
    strict: bool


class ToolCall(Frozen):
    id: str
    name: str
    input: dict[str, object]


class LLMRequest(Frozen):
    config: RoleConfig
    system: str
    messages: tuple[dict[str, object], ...]
    tools: tuple[ToolDef, ...]


ModelStop = Literal[
    "end_turn",
    "tool_use",
    "stop_sequence",
    "max_tokens",
    "refusal",
    "model_context_window_exceeded",
]


class LLMResponse(Frozen):
    tool_calls: tuple[ToolCall, ...]
    text: str
    stop_reason: ModelStop
    usage: Usage
    latency_s: float
    # Sent back unchanged on the next turn, because the API checks thinking blocks byte for byte.
    assistant_content: tuple[object, ...]
    request_id: str | None = None


# Lives with the port, not the adapter, because application code catches it and may not import
# an adapter.
class ProviderError(ArdeError):
    """The model provider failed in a way the run cannot work around."""

    def __init__(
        self, message: str, *, next_action: str, exit_code: ExitCode = ExitCode.CHECKS_FAILED
    ) -> None:
        super().__init__(message, next_action=next_action, exit_code=exit_code)


class ParseFailed(ProviderError):
    """The reply was paid for but holds no usable value, so it carries what to charge."""

    def __init__(self, message: str, *, next_action: str, response: LLMResponse) -> None:
        super().__init__(message, next_action=next_action)
        self.response = response
        self.usage = response.usage


class LLMClient(Protocol):
    def complete(self, request: LLMRequest) -> LLMResponse: ...

    def parse[T: BaseModel](
        self, request: LLMRequest, schema: type[T]
    ) -> tuple[T, LLMResponse]: ...


class ToolOutcome(Frozen):
    content: str
    is_error: bool = False


class Tool(Frozen):
    definition: ToolDef
    handler: Callable[[dict[str, object]], ToolOutcome]

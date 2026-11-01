"""The one loop every agent runs: ask the model, run its tools, stop on a submit or a limit."""

import textwrap
import traceback
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from pydantic import ValidationError

from arde.application.budget import Budget
from arde.application.models_config import Role, RoleConfig
from arde.application.ports import (
    LLMClient,
    LLMRequest,
    LLMResponse,
    Tool,
    ToolCall,
    ToolDef,
    ToolOutcome,
    Tracer,
)
from arde.domain.base import describe_problems
from arde.domain.errors import ArdeError
from arde.domain.pricing import cost_usd
from arde.domain.report import ExitCode
from arde.domain.usage import ModelCall

StopReason = Literal["turn_limit", "refused", "context_window_exceeded", "no_progress"]
Message = dict[str, object]
ToolInput = dict[str, object]

_MAX_IDLE_TURNS = 3
_MAX_SUMMARY_CHARS = 200
_NO_ERROR_MESSAGE = "The tool reported an error but gave no message."
_SEND_LESS = (
    "Your last reply was cut off at the token limit and was thrown away. "
    "Do the same work with less in one call, and split a large submission if a tool allows it."
)
# Retrying neither of these can help: the same text is refused, and the same history is too long.
_TERMINAL_STOPS: dict[str, StopReason] = {
    "refusal": "refused",
    "model_context_window_exceeded": "context_window_exceeded",
}
_NEXT_ACTIONS: dict[StopReason, str] = {
    "turn_limit": (
        "Read debug.log to see where the agent spent its turns, then raise max_turns for this "
        "role in the models file and run again"
    ),
    "refused": "Read the model's last reply in debug.log, remove what it refused and run again",
    "context_window_exceeded": (
        "The conversation outgrew the model's context window, so a rerun fails the same way. "
        "Read debug.log to see which tool output grew it, and report the problem"
    ),
    "no_progress": (
        "Read the model's replies in debug.log to see why it stopped calling tools. If they were "
        "cut off, raise max_tokens for this role in the models file, then run again"
    ),
}


class AgentStopped(ArdeError):
    """The agent ended any way but an accepted submit, so there is no value to return."""

    def __init__(
        self, *, role: Role, stop: StopReason, turns: int, last_rejection: str | None
    ) -> None:
        super().__init__(
            f"The {role} agent stopped after {turns} turns: {stop}",
            next_action=_NEXT_ACTIONS[stop],
            exit_code=ExitCode.CHECKS_FAILED,
        )
        self.stop = stop
        self.turns = turns
        self.last_rejection = last_rejection


class ToolCrashed(ArdeError):
    """ARDE's own bug or a broken machine: nothing the model could work around."""

    def __init__(self, *, role: Role, tool: str, error: Exception) -> None:
        super().__init__(
            _valid_utf8(f"The {role} tool {tool} crashed: {type(error).__name__}: {error}"),
            next_action="Read the traceback in debug.log, fix the cause, then run again",
            exit_code=ExitCode.CHECKS_FAILED,
        )


# The contract types are frozen dataclasses, not Pydantic models: they hold callables and a value
# of any type, and there is nothing in them for Pydantic to validate.
@dataclass(frozen=True)
class Accepted[T]:
    value: T
    reply: str = "accepted"


@dataclass(frozen=True)
class Rejected:
    reason: str


@dataclass(frozen=True)
class SubmitTool[T]:
    """The tool that ends an agent. Only an Accepted from validate finishes the run."""

    definition: ToolDef
    validate: Callable[[ToolInput], Accepted[T] | Rejected]


@dataclass(frozen=True)
class AgentSpec[T]:
    role: Role
    config: RoleConfig
    system: str
    user: str
    tools: tuple[Tool, ...]
    submit: SubmitTool[T]


# A dataclass, because Pydantic cannot build a schema for a Protocol field.
@dataclass(frozen=True)
class AgentDeps:
    client: LLMClient
    tracer: Tracer
    budget: Budget


@dataclass(frozen=True)
class _ToolTurn[T]:
    results: list[Message]
    accepted: Accepted[T] | None
    rejection: str | None


def run_agent[T](spec: AgentSpec[T], deps: AgentDeps) -> T:
    deps.tracer.debug(f"{spec.role} system prompt:\n{spec.system}\nuser prompt:\n{spec.user}")
    messages = [_user_message(spec.user)]
    idle_turns = 0
    last_rejection: str | None = None

    for turn in range(1, spec.config.max_turns + 1):
        response = _ask_model(messages, spec=spec, deps=deps)

        # Checked before any tool runs, because a refused turn may hold a partial tool call.
        if response.stop_reason in _TERMINAL_STOPS:
            stop = _TERMINAL_STOPS[response.stop_reason]
            raise _stopped(stop, turns=turn, last_rejection=last_rejection, spec=spec, deps=deps)

        # Keyed on the calls, not stop_reason: each tool_use echoed here must get a result next.
        if response.tool_calls and response.stop_reason != "max_tokens":
            messages.append(_assistant_message(response))
            tool_turn = _run_tools(response.tool_calls, spec=spec, tracer=deps.tracer)
            if tool_turn.accepted is not None:
                deps.tracer.event("agent_end", role=spec.role, stop="submitted", turns=turn)
                return tool_turn.accepted.value
            messages.append(_user_message(tool_turn.results))
            last_rejection = tool_turn.rejection or last_rejection
            idle_turns = 0
            continue

        idle_turns += 1
        if idle_turns == _MAX_IDLE_TURNS:
            raise _stopped(
                "no_progress", turns=turn, last_rejection=last_rejection, spec=spec, deps=deps
            )
        messages.extend(_nudge(response, spec=spec, tracer=deps.tracer))

    raise _stopped(
        "turn_limit",
        turns=spec.config.max_turns,
        last_rejection=last_rejection,
        spec=spec,
        deps=deps,
    )


def _user_message(content: object) -> Message:
    return {"role": "user", "content": content}


def _assistant_message(response: LLMResponse) -> Message:
    return {"role": "assistant", "content": list(response.assistant_content)}


def _stopped[T](
    stop: StopReason, *, turns: int, last_rejection: str | None, spec: AgentSpec[T], deps: AgentDeps
) -> AgentStopped:
    deps.tracer.event("agent_end", role=spec.role, stop=stop, turns=turns)
    return AgentStopped(role=spec.role, stop=stop, turns=turns, last_rejection=last_rejection)


# A cut-off turn is dropped, not echoed: a tool call cut off mid-input can have no result. An
# end_turn can come back empty, and the docs resume from that with a user message alone.
def _nudge[T](response: LLMResponse, *, spec: AgentSpec[T], tracer: Tracer) -> list[Message]:
    if response.stop_reason == "max_tokens":
        echoed, reminder = [], _SEND_LESS
    else:
        echoed = [_assistant_message(response)] if response.assistant_content else []
        name = spec.submit.definition.name
        reminder = f"Your turn ended without a call to {name}. Call it now to finish."
    tracer.debug(f"{spec.role} re-prompt:\n{reminder}")
    return [*echoed, _user_message(reminder)]


def _ask_model[T](messages: list[Message], *, spec: AgentSpec[T], deps: AgentDeps) -> LLMResponse:
    request = LLMRequest(
        config=spec.config,
        system=spec.system,
        messages=tuple(messages),
        tools=(*(tool.definition for tool in spec.tools), spec.submit.definition),
    )
    model = spec.config.model
    try:
        response = deps.client.complete(request)
    except ArdeError as error:
        deps.tracer.event("model_error", role=spec.role, model=model, error=str(error))
        raise
    record_model_call(response, role=spec.role, model=model, deps=deps)
    return response


# The charge is in a finally, because a call the API answered is paid for even when the disk
# holding the trace has failed. It comes after the event, because a charge over the cap raises
# and the call that tripped the cap belongs in the trace.
def record_model_call(response: LLMResponse, *, role: Role, model: str, deps: AgentDeps) -> None:
    """Traces and charges one paid call. Every caller of the model comes through here."""
    usage, latency_s = response.usage, response.latency_s
    try:
        deps.tracer.debug(f"{role} model reply ({response.stop_reason}):\n{response.text}")
        deps.tracer.event(
            "model_call",
            role=role,
            model=model,
            usage=usage.model_dump(),
            cost_usd=cost_usd(model, usage),
            stop_reason=response.stop_reason,
            latency_s=latency_s,
        )
    finally:
        deps.budget.charge(ModelCall(role=role, model=model, usage=usage, latency_s=latency_s))


def _run_tools[T](
    calls: tuple[ToolCall, ...], *, spec: AgentSpec[T], tracer: Tracer
) -> _ToolTurn[T]:
    results: list[Message] = []
    rejection: str | None = None
    for call in calls:
        outcome = _run_tool(call, spec=spec, tracer=tracer)
        if isinstance(outcome, Accepted):
            _record_tool_call(call, ToolOutcome(content=outcome.reply), spec=spec, tracer=tracer)
            # The loop ends here and sends no further request, so the calls after an accepted
            # submit need neither a run nor a result.
            return _ToolTurn(results, accepted=outcome, rejection=None)
        _record_tool_call(call, outcome, spec=spec, tracer=tracer)
        if _is_submit(call, spec) and outcome.is_error:
            rejection = outcome.content
        results.append(_tool_result(call, outcome))
    return _ToolTurn(results, accepted=None, rejection=rejection)


def _run_tool[T](
    call: ToolCall, *, spec: AgentSpec[T], tracer: Tracer
) -> ToolOutcome | Accepted[T]:
    tracer.debug(f"{spec.role} tool {call.name} input:\n{call.input}")
    try:
        outcome = _dispatch(call, spec)
    # The model's input broke a rule, and that is the one failure the model can fix.
    except ValidationError as error:
        outcome = ToolOutcome(content=describe_problems(error, whole="the input"), is_error=True)
    except ArdeError as error:
        _record_tool_failure(call, error, spec=spec, tracer=tracer)
        raise
    except Exception as error:
        tracer.debug(f"{spec.role} tool {call.name} raised:\n{traceback.format_exc()}")
        crash = ToolCrashed(role=spec.role, tool=call.name, error=error)
        _record_tool_failure(call, crash, spec=spec, tracer=tracer)
        raise crash from error
    if isinstance(outcome, Accepted):
        return outcome
    cleaned = outcome.model_copy(update={"content": _valid_utf8(outcome.content)})
    tracer.debug(f"{spec.role} tool {call.name} result:\n{cleaned.content}")
    return cleaned


def _dispatch[T](call: ToolCall, spec: AgentSpec[T]) -> ToolOutcome | Accepted[T]:
    if _is_submit(call, spec):
        verdict = spec.submit.validate(call.input)
        if isinstance(verdict, Accepted):
            return verdict
        return ToolOutcome(content=verdict.reason, is_error=True)
    tool = next((tool for tool in spec.tools if tool.definition.name == call.name), None)
    if tool is None:
        names = (*(tool.definition.name for tool in spec.tools), spec.submit.definition.name)
        content = f"There is no tool named {call.name}. Use one of: {', '.join(names)}."
        return ToolOutcome(content=content, is_error=True)
    return tool.handler(call.input)


def _is_submit[T](call: ToolCall, spec: AgentSpec[T]) -> bool:
    return call.name == spec.submit.definition.name


# A file name that is not UTF-8 reaches Python as a lone surrogate, and both the SDK's encoder and
# a UTF-8 file refuse one. The text comes from the target repo, so it must not end the run.
def _valid_utf8(text: str) -> str:
    return text.encode("utf-8", "backslashreplace").decode("utf-8")


def _record_tool_call[T](
    call: ToolCall, outcome: ToolOutcome, *, spec: AgentSpec[T], tracer: Tracer
) -> None:
    ok = not outcome.is_error
    summary = _summary(outcome.content)
    tracer.event("tool_call", role=spec.role, name=call.name, ok=ok, summary=summary)
    if _is_submit(call, spec) and not ok:
        tracer.event("correction", role=spec.role, name=call.name, summary=summary)


def _record_tool_failure[T](
    call: ToolCall, error: ArdeError, *, spec: AgentSpec[T], tracer: Tracer
) -> None:
    summary = _summary(str(error))
    tracer.event("tool_call", role=spec.role, name=call.name, ok=False, summary=summary)


# Whole words only: the tracer redacts a secret by exact match, so half of one would get through.
def _summary(content: str) -> str:
    return textwrap.shorten(
        content,
        width=_MAX_SUMMARY_CHARS,
        placeholder=" ...",
        break_long_words=False,
        break_on_hyphens=False,
    )


# The API answers an error result with no content with HTTP 400, which would end the run over a
# tool that merely said nothing.
def _tool_result(call: ToolCall, outcome: ToolOutcome) -> Message:
    content = (outcome.content or _NO_ERROR_MESSAGE) if outcome.is_error else outcome.content
    return {
        "type": "tool_result",
        "tool_use_id": call.id,
        "content": content,
        "is_error": outcome.is_error,
    }

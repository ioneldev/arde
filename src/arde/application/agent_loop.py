"""The one loop every agent runs: ask the model, run its tools, stop on a submit or a limit."""

import textwrap
import traceback
from dataclasses import dataclass
from typing import Literal, NamedTuple

from arde.application.budget import Budget
from arde.application.models_config import RoleConfig
from arde.application.ports import (
    LLMClient,
    LLMRequest,
    LLMResponse,
    Tool,
    ToolCall,
    ToolOutcome,
    Tracer,
)
from arde.domain.base import Frozen
from arde.domain.errors import ArdeError
from arde.domain.usage import ModelCall

StopReason = Literal["submitted", "turn_limit", "refused", "max_tokens", "no_progress"]
Message = dict[str, object]

_MAX_IDLE_TURNS = 3
_MAX_SUMMARY_CHARS = 200
# Running out of context window cuts a turn off just as max_tokens does. Re-prompting instead
# would pay for the whole window again on each try.
_CUT_OFF_STOP_REASONS = ("max_tokens", "model_context_window_exceeded")


class AgentSpec(Frozen):
    role: str
    system: str
    user: str
    tools: tuple[Tool, ...]
    submit_tool: str


class AgentResult(Frozen):
    submitted: object | None
    stop: StopReason
    turns: int


# A dataclass, because Pydantic cannot build a schema for a Protocol field.
@dataclass(frozen=True)
class AgentDeps:
    client: LLMClient
    config: RoleConfig
    tracer: Tracer
    budget: Budget


class _ToolTurn(NamedTuple):
    results: list[Message]
    accepted: ToolOutcome | None


def run_agent(spec: AgentSpec, deps: AgentDeps) -> AgentResult:
    deps.tracer.debug(f"{spec.role} system prompt:\n{spec.system}\nuser prompt:\n{spec.user}")
    messages = [_user_message(spec.user)]
    idle_turns = 0

    for turn in range(1, deps.config.max_turns + 1):
        response = _ask_model(messages, spec=spec, deps=deps)

        # Checked before any tool runs, because a cut-off turn may hold a partial tool call.
        if response.stop_reason == "refusal":
            return _stopped("refused", turn)
        if response.stop_reason in _CUT_OFF_STOP_REASONS:
            return _stopped("max_tokens", turn)

        # An end_turn can come back empty. The docs resume from that with a user message alone.
        if response.assistant_content:
            messages.append({"role": "assistant", "content": list(response.assistant_content)})

        if response.stop_reason == "tool_use":
            tool_turn = _run_tools(response.tool_calls, spec=spec, deps=deps)
            if tool_turn.accepted is not None:
                return _submitted(tool_turn.accepted, turn)
            messages.append(_user_message(tool_turn.results))
            idle_turns = 0
            continue

        idle_turns += 1
        if idle_turns == _MAX_IDLE_TURNS:
            return _stopped("no_progress", turn)
        # The API resumes a paused turn from the assistant message, so nothing may follow it.
        if response.stop_reason != "pause_turn":
            messages.append(_user_message(_reminder_to_submit(spec)))

    return _stopped("turn_limit", deps.config.max_turns)


def _user_message(content: object) -> Message:
    return {"role": "user", "content": content}


def _reminder_to_submit(spec: AgentSpec) -> str:
    return f"Your turn ended without a call to {spec.submit_tool}. Call it now to finish."


def _stopped(stop: StopReason, turn: int) -> AgentResult:
    return AgentResult(submitted=None, stop=stop, turns=turn)


def _submitted(outcome: ToolOutcome, turn: int) -> AgentResult:
    return AgentResult(submitted=outcome.submitted, stop="submitted", turns=turn)


def _ask_model(messages: list[Message], *, spec: AgentSpec, deps: AgentDeps) -> LLMResponse:
    request = LLMRequest(
        config=deps.config,
        system=spec.system,
        messages=tuple(messages),
        tools=tuple(tool.definition for tool in spec.tools),
    )
    response = deps.client.complete(request)
    _record_model_call(response, spec=spec, deps=deps)
    return response


# Traced before it is charged, because a charge over the cap raises and the call that tripped
# the cap belongs in the trace.
def _record_model_call(response: LLMResponse, *, spec: AgentSpec, deps: AgentDeps) -> None:
    model = deps.config.model
    deps.tracer.event(
        "model_call",
        role=spec.role,
        model=model,
        usage=response.usage.model_dump(),
        stop_reason=response.stop_reason,
        latency_s=response.latency_s,
    )
    deps.budget.charge(
        ModelCall(role=spec.role, model=model, usage=response.usage, latency_s=response.latency_s)
    )


def _run_tools(calls: tuple[ToolCall, ...], *, spec: AgentSpec, deps: AgentDeps) -> _ToolTurn:
    results: list[Message] = []
    for call in calls:
        outcome = _run_tool(call, spec=spec, tracer=deps.tracer)
        _record_tool_call(call, outcome, spec=spec, tracer=deps.tracer)
        # The loop ends here and sends no further request, so the calls after an accepted
        # submit need neither a run nor a result.
        if call.name == spec.submit_tool and not outcome.is_error:
            return _ToolTurn(results, accepted=outcome)
        results.append(_tool_result(call, outcome))
    return _ToolTurn(results, accepted=None)


def _run_tool(call: ToolCall, *, spec: AgentSpec, tracer: Tracer) -> ToolOutcome:
    tool = _find_tool(call.name, spec)
    if tool is None:
        known = ", ".join(tool.definition.name for tool in spec.tools)
        content = f"There is no tool named {call.name}. Use one of: {known}."
        return ToolOutcome(content=content, is_error=True)
    try:
        return tool.handler(call.input)
    except ArdeError:
        raise
    # A failing tool is the model's to work around. An ArdeError above stops the run on purpose.
    except Exception as error:
        tracer.debug(f"{spec.role} tool {call.name} raised:\n{traceback.format_exc()}")
        return ToolOutcome(content=f"{type(error).__name__}: {error}", is_error=True)


def _find_tool(name: str, spec: AgentSpec) -> Tool | None:
    return next((tool for tool in spec.tools if tool.definition.name == name), None)


def _record_tool_call(
    call: ToolCall, outcome: ToolOutcome, *, spec: AgentSpec, tracer: Tracer
) -> None:
    ok = not outcome.is_error
    summary = _summary(outcome.content)
    tracer.event("tool_call", role=spec.role, name=call.name, ok=ok, summary=summary)
    if call.name == spec.submit_tool and not ok:
        tracer.event("correction", role=spec.role, name=call.name, summary=summary)


# Whole words only: the tracer redacts a secret by exact match, so half of one would get through.
def _summary(content: str) -> str:
    return textwrap.shorten(
        content,
        width=_MAX_SUMMARY_CHARS,
        placeholder=" ...",
        break_long_words=False,
        break_on_hyphens=False,
    )


def _tool_result(call: ToolCall, outcome: ToolOutcome) -> Message:
    return {
        "type": "tool_result",
        "tool_use_id": call.id,
        "content": outcome.content,
        "is_error": outcome.is_error,
    }

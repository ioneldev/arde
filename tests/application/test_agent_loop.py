"""Every agent runs this loop, so each way a model turn can end has a scripted test here."""

from collections.abc import Callable
from dataclasses import dataclass, field

import pytest

from arde.adapters.llm.fake import FakeLLM
from arde.application.agent_loop import (
    AgentDeps,
    AgentResult,
    AgentSpec,
    StopReason,
    run_agent,
)
from arde.application.budget import Budget, BudgetExceeded
from arde.application.models_config import RoleConfig
from arde.application.ports import LLMResponse, Tool, ToolCall, ToolDef, ToolOutcome
from arde.domain.errors import ArdeError
from arde.domain.pricing import cost_usd
from arde.domain.report import ExitCode
from arde.domain.usage import Usage

ROLE = "explore"
MODEL = "claude-haiku-5-5"
SYSTEM = "You explore a service."
USER = "Find every route."
SUBMIT = "submit_answer"
READ = "read_file"
USAGE = Usage(input_tokens=100, output_tokens=20, cache_read=10, cache_write=5)
EXPENSIVE = Usage(input_tokens=1_000_000, output_tokens=0, cache_read=0, cache_write=0)
LATENCY_S = 1.5

ToolInput = dict[str, object]


class RecordingTracer:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, object]]] = []
        self.debug_lines: list[str] = []

    def event(self, kind: str, **fields: object) -> None:
        self.events.append((kind, fields))

    def debug(self, text: str) -> None:
        self.debug_lines.append(text)

    def events_of(self, kind: str) -> list[dict[str, object]]:
        return [fields for event_kind, fields in self.events if event_kind == kind]


@dataclass
class ToolSpy:
    """A tool that remembers what it was called with, so a test can prove it never ran."""

    content: str = "tool output"
    inputs: list[ToolInput] = field(default_factory=list)

    def __call__(self, tool_input: ToolInput) -> ToolOutcome:
        self.inputs.append(tool_input)
        return ToolOutcome(content=self.content)


@dataclass
class ScriptedRun:
    result: AgentResult
    llm: FakeLLM
    tracer: RecordingTracer
    budget: Budget


def accept_answer(tool_input: ToolInput) -> ToolOutcome:
    return ToolOutcome(content="accepted", submitted=tool_input["answer"])


def accept_only_42(tool_input: ToolInput) -> ToolOutcome:
    if tool_input["answer"] != 42:
        return ToolOutcome(content="the answer must be 42", is_error=True)
    return accept_answer(tool_input)


def tool_named(name: str, handler: Callable[[ToolInput], ToolOutcome]) -> Tool:
    definition = ToolDef(name=name, description=f"The {name} tool", input_schema={"type": "object"})
    return Tool(definition=definition, handler=handler)


SUBMIT_TOOL = tool_named(SUBMIT, accept_answer)


def spec_with(tools: tuple[Tool, ...]) -> AgentSpec:
    return AgentSpec(role=ROLE, system=SYSTEM, user=USER, tools=tools, submit_tool=SUBMIT)


def deps_for(
    llm: FakeLLM, *, tracer: RecordingTracer, budget: Budget, max_turns: int = 10
) -> AgentDeps:
    config = RoleConfig(model=MODEL, effort="low", max_turns=max_turns, max_tokens=1024)
    return AgentDeps(client=llm, config=config, tracer=tracer, budget=budget)


def run_script(
    script: list[LLMResponse],
    *,
    tools: tuple[Tool, ...] = (SUBMIT_TOOL,),
    max_turns: int = 10,
    max_usd: float = 10.0,
) -> ScriptedRun:
    llm = FakeLLM(script)
    tracer = RecordingTracer()
    budget = Budget(max_usd=max_usd)
    deps = deps_for(llm, tracer=tracer, budget=budget, max_turns=max_turns)
    result = run_agent(spec_with(tools), deps)
    return ScriptedRun(result=result, llm=llm, tracer=tracer, budget=budget)


def reply(
    stop_reason: str,
    *,
    tool_calls: tuple[ToolCall, ...] = (),
    content: tuple[object, ...] = ({"type": "text", "text": "Let me think."},),
    usage: Usage = USAGE,
) -> LLMResponse:
    return LLMResponse(
        tool_calls=tool_calls,
        text="",
        stop_reason=stop_reason,
        usage=usage,
        latency_s=LATENCY_S,
        assistant_content=content,
    )


def call(name: str, *, call_id: str = "toolu_1", **tool_input: object) -> ToolCall:
    return ToolCall(id=call_id, name=name, input=tool_input)


def tool_use_block(tool_call: ToolCall) -> dict[str, object]:
    return {
        "type": "tool_use",
        "id": tool_call.id,
        "name": tool_call.name,
        "input": tool_call.input,
    }


def calling(
    *tool_calls: ToolCall, stop_reason: str = "tool_use", usage: Usage = USAGE
) -> LLMResponse:
    blocks = tuple(tool_use_block(tool_call) for tool_call in tool_calls)
    return reply(stop_reason, tool_calls=tool_calls, content=blocks, usage=usage)


def submit(answer: object = 42, *, call_id: str = "toolu_submit") -> ToolCall:
    return call(SUBMIT, call_id=call_id, answer=answer)


def tool_result(tool_use_id: str, content: str, *, is_error: bool = False) -> dict[str, object]:
    return {
        "type": "tool_result",
        "tool_use_id": tool_use_id,
        "content": content,
        "is_error": is_error,
    }


def stopped(stop: StopReason, *, turns: int) -> AgentResult:
    return AgentResult(submitted=None, stop=stop, turns=turns)


def summary_of(content: str) -> str:
    run = run_script(
        [calling(call(READ)), calling(submit())],
        tools=(tool_named(READ, ToolSpy(content=content)), SUBMIT_TOOL),
    )
    return str(run.tracer.events_of("tool_call")[0]["summary"])


def test_stops_when_submit_handler_accepts() -> None:
    run = run_script([calling(submit(answer=42))])

    assert run.result == AgentResult(submitted=42, stop="submitted", turns=1)


def test_submit_on_the_last_allowed_turn_is_accepted() -> None:
    run = run_script(
        [calling(call(READ)), calling(submit())],
        tools=(tool_named(READ, ToolSpy()), SUBMIT_TOOL),
        max_turns=2,
    )

    assert run.result == AgentResult(submitted=42, stop="submitted", turns=2)


def test_tool_error_returns_as_is_error_and_loop_continues() -> None:
    run = run_script(
        [calling(submit(answer=1, call_id="toolu_bad")), calling(submit(answer=42))],
        tools=(tool_named(SUBMIT, accept_only_42),),
    )

    assert run.result == AgentResult(submitted=42, stop="submitted", turns=2)
    assert run.llm.requests[1].messages[-1] == {
        "role": "user",
        "content": [tool_result("toolu_bad", "the answer must be 42", is_error=True)],
    }
    assert run.tracer.events_of("correction") == [
        {"role": ROLE, "name": SUBMIT, "summary": "the answer must be 42"}
    ]


def test_a_non_submit_tool_error_is_not_a_correction() -> None:
    def fail(_tool_input: ToolInput) -> ToolOutcome:
        return ToolOutcome(content="no such file", is_error=True)

    run = run_script(
        [calling(call(READ)), calling(submit())],
        tools=(tool_named(READ, fail), SUBMIT_TOOL),
    )

    assert run.tracer.events_of("correction") == []


def test_all_tool_results_go_back_in_one_user_message() -> None:
    first = ToolSpy(content="first output")
    second = ToolSpy(content="second output")
    run = run_script(
        [
            calling(call(READ, call_id="toolu_a"), call("grep", call_id="toolu_b")),
            calling(submit()),
        ],
        tools=(tool_named(READ, first), tool_named("grep", second), SUBMIT_TOOL),
    )

    assert [message["role"] for message in run.llm.requests[1].messages] == [
        "user",
        "assistant",
        "user",
    ]
    assert run.llm.requests[1].messages[-1]["content"] == [
        tool_result("toolu_a", "first output"),
        tool_result("toolu_b", "second output"),
    ]


def test_assistant_content_is_echoed_unchanged() -> None:
    read_call = call(READ)
    thinking = {"type": "thinking", "thinking": "", "signature": "opaque"}
    block = tool_use_block(read_call)
    run = run_script(
        [reply("tool_use", tool_calls=(read_call,), content=(thinking, block)), calling(submit())],
        tools=(tool_named(READ, ToolSpy()), SUBMIT_TOOL),
    )

    assert run.llm.requests[1].messages[1] == {"role": "assistant", "content": [thinking, block]}


def test_every_request_carries_the_config_system_prompt_and_tool_definitions() -> None:
    read_tool = tool_named(READ, ToolSpy())
    run = run_script([calling(submit())], tools=(read_tool, SUBMIT_TOOL))

    request = run.llm.requests[0]

    assert request.config.model == MODEL
    assert request.system == SYSTEM
    assert request.messages == ({"role": "user", "content": USER},)
    assert request.tools == (read_tool.definition, SUBMIT_TOOL.definition)


def test_end_turn_without_submit_reprompts_and_counts_a_turn() -> None:
    run = run_script([reply("end_turn"), calling(submit())])

    assert run.result == AgentResult(submitted=42, stop="submitted", turns=2)
    reprompt = run.llm.requests[1].messages[-1]
    assert reprompt["role"] == "user"
    assert SUBMIT in str(reprompt["content"])


def test_stop_sequence_is_reprompted_like_end_turn() -> None:
    run = run_script([reply("stop_sequence"), calling(submit())])

    assert run.result == AgentResult(submitted=42, stop="submitted", turns=2)
    assert SUBMIT in str(run.llm.requests[1].messages[-1]["content"])


def test_an_empty_end_turn_is_reprompted_without_an_empty_assistant_message() -> None:
    run = run_script([reply("end_turn", content=()), calling(submit())])

    assert run.result.stop == "submitted"
    assert [message["role"] for message in run.llm.requests[1].messages] == ["user", "user"]


def test_pause_turn_is_sent_back_with_no_user_message_and_counts_a_turn() -> None:
    run = run_script([reply("pause_turn"), calling(submit())])

    assert run.result == AgentResult(submitted=42, stop="submitted", turns=2)
    assert [message["role"] for message in run.llm.requests[1].messages] == ["user", "assistant"]


def test_no_submit_three_turns_in_a_row_stops_no_progress() -> None:
    run = run_script([reply("end_turn"), reply("end_turn"), reply("end_turn")])

    assert run.result == stopped("no_progress", turns=3)
    assert len(run.llm.requests) == 3


def test_three_paused_turns_in_a_row_stop_no_progress() -> None:
    run = run_script([reply("pause_turn"), reply("pause_turn"), reply("pause_turn")])

    assert run.result == stopped("no_progress", turns=3)


@pytest.mark.parametrize(
    "busy_turn",
    [calling(call(READ)), calling(submit(answer=1))],
    ids=["another_tool", "rejected_submit"],
)
def test_a_turn_with_a_tool_call_resets_the_no_progress_count(busy_turn: LLMResponse) -> None:
    run = run_script(
        [
            reply("end_turn"),
            reply("end_turn"),
            busy_turn,
            reply("end_turn"),
            reply("end_turn"),
            calling(submit(answer=42)),
        ],
        tools=(tool_named(READ, ToolSpy()), tool_named(SUBMIT, accept_only_42)),
    )

    assert run.result == AgentResult(submitted=42, stop="submitted", turns=6)


def test_refusal_stops_with_reason() -> None:
    spy = ToolSpy()
    run = run_script(
        [calling(call(READ), stop_reason="refusal")],
        tools=(tool_named(READ, spy), SUBMIT_TOOL),
    )

    assert run.result == stopped("refused", turns=1)
    assert spy.inputs == []


@pytest.mark.parametrize("stop_reason", ["max_tokens", "model_context_window_exceeded"])
def test_max_tokens_stops_with_reason(stop_reason: str) -> None:
    spy = ToolSpy()
    run = run_script(
        [calling(call(READ), stop_reason=stop_reason)],
        tools=(tool_named(READ, spy), SUBMIT_TOOL),
    )

    assert run.result == stopped("max_tokens", turns=1)
    assert spy.inputs == []


@pytest.mark.parametrize("stop_reason", ["tool_use", "end_turn", "stop_sequence"])
def test_every_tool_use_gets_a_result_in_the_next_message(stop_reason: str) -> None:
    two_calls = calling(
        call(READ, call_id="toolu_a"), call(READ, call_id="toolu_b"), stop_reason=stop_reason
    )
    run = run_script(
        [two_calls, calling(submit())],
        tools=(tool_named(READ, ToolSpy()), SUBMIT_TOOL),
    )

    _, assistant_turn, next_message = run.llm.requests[1].messages
    tool_use_ids = [block["id"] for block in assistant_turn["content"]]
    assert tool_use_ids == ["toolu_a", "toolu_b"]
    assert next_message["content"] == [
        tool_result(tool_use_id, "tool output") for tool_use_id in tool_use_ids
    ]


@pytest.mark.parametrize("stop_reason", ["tool_use", "end_turn", "stop_sequence"])
def test_zero_call_turns_reach_no_progress(stop_reason: str) -> None:
    run = run_script([reply(stop_reason), reply(stop_reason), reply(stop_reason)])

    assert run.result == stopped("no_progress", turns=3)
    assert all(message["content"] for message in run.llm.requests[-1].messages)


def test_turn_limit_stops() -> None:
    run = run_script(
        [calling(call(READ)), calling(call(READ))],
        tools=(tool_named(READ, ToolSpy()), SUBMIT_TOOL),
        max_turns=2,
    )

    assert run.result == stopped("turn_limit", turns=2)
    assert len(run.llm.requests) == 2


def test_tools_after_an_accepted_submit_are_not_run() -> None:
    spy = ToolSpy()
    run = run_script(
        [
            calling(
                call(READ, call_id="toolu_before"),
                submit(),
                call(READ, call_id="toolu_after"),
            )
        ],
        tools=(tool_named(READ, spy), SUBMIT_TOOL),
    )

    assert run.result == AgentResult(submitted=42, stop="submitted", turns=1)
    assert len(spy.inputs) == 1
    assert len(run.llm.requests) == 1


def test_budget_exceeded_propagates() -> None:
    spy = ToolSpy()

    with pytest.raises(BudgetExceeded):
        run_script(
            [calling(call(READ), usage=EXPENSIVE)],
            tools=(tool_named(READ, spy), SUBMIT_TOOL),
            max_usd=0.01,
        )

    assert spy.inputs == []


def test_the_call_that_trips_the_budget_is_still_traced() -> None:
    llm = FakeLLM([calling(submit(), usage=EXPENSIVE)])
    tracer = RecordingTracer()
    deps = deps_for(llm, tracer=tracer, budget=Budget(max_usd=0.01))

    with pytest.raises(BudgetExceeded):
        run_agent(spec_with((SUBMIT_TOOL,)), deps)

    assert len(tracer.events_of("model_call")) == 1


def test_each_call_is_charged_to_the_role_under_the_configured_model() -> None:
    run = run_script([reply("end_turn"), calling(submit())])

    totals = run.budget.totals()[ROLE]

    assert totals.calls == 2
    assert totals.latency_s == 2 * LATENCY_S
    assert totals.cost_usd == pytest.approx(2 * cost_usd(MODEL, USAGE))


def test_unknown_tool_name_returns_tool_error() -> None:
    run = run_script([calling(call("delete_repo", call_id="toolu_x")), calling(submit())])

    assert run.result.stop == "submitted"
    returned = run.llm.requests[1].messages[-1]["content"]
    assert isinstance(returned, list)
    assert returned[0]["tool_use_id"] == "toolu_x"
    assert returned[0]["is_error"] is True
    assert "delete_repo" in returned[0]["content"]
    assert SUBMIT in returned[0]["content"]


def test_a_handler_that_raises_returns_as_is_error_and_is_logged() -> None:
    def explode(_tool_input: ToolInput) -> ToolOutcome:
        raise ValueError("the disk is on fire")

    run = run_script(
        [calling(call(READ, call_id="toolu_x")), calling(submit())],
        tools=(tool_named(READ, explode), SUBMIT_TOOL),
    )

    assert run.result.stop == "submitted"
    returned = run.llm.requests[1].messages[-1]["content"]
    assert isinstance(returned, list)
    assert returned[0]["is_error"] is True
    assert "the disk is on fire" in returned[0]["content"]
    assert any("the disk is on fire" in line for line in run.tracer.debug_lines)
    assert run.tracer.events_of("tool_call")[0]["ok"] is False


def test_a_submit_handler_that_raises_is_a_correction() -> None:
    def explode(_tool_input: ToolInput) -> ToolOutcome:
        raise ValueError("the schema did not match")

    run = run_script(
        [calling(submit()), calling(submit())],
        tools=(tool_named(SUBMIT, explode),),
        max_turns=2,
    )

    assert run.result == stopped("turn_limit", turns=2)
    assert len(run.tracer.events_of("correction")) == 2


def test_an_arde_error_from_a_handler_propagates() -> None:
    def stop_the_run(_tool_input: ToolInput) -> ToolOutcome:
        raise ArdeError("stop", next_action="look", exit_code=ExitCode.CHECKS_FAILED)

    with pytest.raises(ArdeError):
        run_script([calling(call(READ))], tools=(tool_named(READ, stop_the_run), SUBMIT_TOOL))


def test_an_interrupt_during_a_handler_propagates() -> None:
    def interrupted(_tool_input: ToolInput) -> ToolOutcome:
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        run_script([calling(call(READ))], tools=(tool_named(READ, interrupted), SUBMIT_TOOL))


def test_every_call_is_traced_with_usage_and_latency() -> None:
    run = run_script([reply("end_turn"), calling(submit())])

    expected = {
        "role": ROLE,
        "model": MODEL,
        "usage": USAGE.model_dump(),
        "latency_s": LATENCY_S,
    }
    assert run.tracer.events_of("model_call") == [
        {**expected, "stop_reason": "end_turn"},
        {**expected, "stop_reason": "tool_use"},
    ]


def test_every_tool_call_is_traced() -> None:
    run = run_script([calling(call("missing_tool")), calling(submit())])

    assert run.tracer.events_of("tool_call")[1] == {
        "role": ROLE,
        "name": SUBMIT,
        "ok": True,
        "summary": "accepted",
    }
    assert run.tracer.events_of("tool_call")[0]["ok"] is False


def test_prompts_reach_the_debug_log_and_never_the_trace() -> None:
    run = run_script([calling(submit())])

    debug_log = "\n".join(run.tracer.debug_lines)
    assert SYSTEM in debug_log
    assert USER in debug_log
    assert SYSTEM not in str(run.tracer.events)
    assert USER not in str(run.tracer.events)


def test_a_long_tool_result_is_cut_short_in_the_trace() -> None:
    summary = summary_of("word " * 10_000)

    assert summary.startswith("word word")
    assert len(summary) <= 200


# The tracer redacts a secret by exact match, so half of one would reach the trace unredacted.
def test_the_summary_drops_a_word_it_cannot_fit_whole() -> None:
    secret = "sk-ant-" + "k" * 100

    summary = summary_of("word " * 30 + secret)

    assert "sk-ant" not in summary
    assert "kkk" not in summary

"""Every agent runs this loop, so each way a model turn can end has a scripted test here."""

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from pydantic import BaseModel, SecretStr
from recording_tracer import RecordingTracer

from arde.adapters.llm.fake import FakeLLM
from arde.adapters.tracer import JsonlTracer
from arde.application.agent_loop import (
    Accepted,
    AgentDeps,
    AgentSpec,
    AgentStopped,
    Rejected,
    StopReason,
    SubmitTool,
    ToolCrashed,
    record_model_call,
    run_agent,
)
from arde.application.budget import Budget, BudgetExceeded
from arde.application.models_config import RoleConfig
from arde.application.ports import (
    LLMClient,
    LLMRequest,
    LLMResponse,
    ProviderError,
    Tool,
    ToolCall,
    ToolDef,
    ToolOutcome,
)
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
NO_ERROR_MESSAGE = "The tool reported an error but gave no message."
FILE_NAME_THAT_IS_NOT_UTF8 = "caf\udce9.go"

ToolInput = dict[str, object]


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
    llm: FakeLLM
    tracer: RecordingTracer
    budget: Budget
    result: object = None
    stopped: AgentStopped | None = None

    def end_of_agent(self) -> dict[str, object]:
        ends = self.tracer.events_of("agent_end")
        assert len(ends) == 1
        return ends[0]


def accept_answer(tool_input: ToolInput) -> Accepted[object]:
    return Accepted(tool_input["answer"])


def accept_only_42(tool_input: ToolInput) -> Accepted[object] | Rejected:
    if tool_input["answer"] != 42:
        return Rejected("the answer must be 42")
    return accept_answer(tool_input)


def definition_named(name: str) -> ToolDef:
    return ToolDef(
        name=name, description=f"The {name} tool", input_schema={"type": "object"}, strict=False
    )


def tool_named(name: str, handler: Callable[[ToolInput], ToolOutcome]) -> Tool:
    return Tool(definition=definition_named(name), handler=handler)


def submit_tool(
    validate: Callable[[ToolInput], Accepted[object] | Rejected] = accept_answer,
) -> SubmitTool[object]:
    return SubmitTool(definition=definition_named(SUBMIT), validate=validate)


def config_with(max_turns: int) -> RoleConfig:
    return RoleConfig(model=MODEL, effort="low", max_turns=max_turns, max_tokens=1024)


def spec_with(
    tools: tuple[Tool, ...], submit: SubmitTool[object], *, max_turns: int = 10
) -> AgentSpec[object]:
    return AgentSpec(
        role=ROLE,
        config=config_with(max_turns),
        system=SYSTEM,
        user=USER,
        tools=tools,
        submit=submit,
    )


def run_script(
    script: list[LLMResponse],
    *,
    tools: tuple[Tool, ...] = (),
    validate: Callable[[ToolInput], Accepted[object] | Rejected] = accept_answer,
    max_turns: int = 10,
    max_usd: float = 10.0,
) -> ScriptedRun:
    run = ScriptedRun(llm=FakeLLM(script), tracer=RecordingTracer(), budget=Budget(max_usd=max_usd))
    spec = spec_with(tools, submit_tool(validate), max_turns=max_turns)
    deps = AgentDeps(client=run.llm, tracer=run.tracer, budget=run.budget)
    try:
        run.result = run_agent(spec, deps)
    except AgentStopped as stopped:
        run.stopped = stopped
    return run


def reply(
    stop_reason: str,
    *,
    tool_calls: tuple[ToolCall, ...] = (),
    content: tuple[object, ...] = ({"type": "text", "text": "Let me think."},),
    text: str = "",
    usage: Usage = USAGE,
) -> LLMResponse:
    return LLMResponse(
        tool_calls=tool_calls,
        text=text,
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


def assert_stopped(run: ScriptedRun, stop: StopReason, *, turns: int) -> None:
    assert run.stopped is not None
    assert (run.stopped.stop, run.stopped.turns) == (stop, turns)
    assert run.end_of_agent() == {"role": ROLE, "stop": stop, "turns": turns}


def summary_of(content: str) -> str:
    run = run_script(
        [calling(call(READ)), calling(submit())],
        tools=(tool_named(READ, ToolSpy(content=content)),),
    )
    return str(run.tracer.events_of("tool_call")[0]["summary"])


def test_stops_when_the_submit_is_accepted_and_returns_its_value() -> None:
    run = run_script([calling(submit(answer=42))])

    assert run.result == 42
    assert run.end_of_agent() == {"role": ROLE, "stop": "submitted", "turns": 1}


def test_a_falsy_value_is_still_an_accepted_submit() -> None:
    run = run_script([calling(submit(answer=0))])

    assert run.result == 0
    assert run.stopped is None


def test_submit_on_the_last_allowed_turn_is_accepted() -> None:
    run = run_script(
        [calling(call(READ)), calling(submit())],
        tools=(tool_named(READ, ToolSpy()),),
        max_turns=2,
    )

    assert run.result == 42
    assert run.end_of_agent()["turns"] == 2


def test_a_rejected_submit_returns_as_an_error_and_the_loop_continues() -> None:
    run = run_script(
        [calling(submit(answer=1, call_id="toolu_bad")), calling(submit(answer=42))],
        validate=accept_only_42,
    )

    assert run.result == 42
    assert run.llm.requests[1].messages[-1] == {
        "role": "user",
        "content": [tool_result("toolu_bad", "the answer must be 42", is_error=True)],
    }
    assert run.tracer.events_of("correction") == [
        {"role": ROLE, "name": SUBMIT, "summary": "the answer must be 42"}
    ]


def test_a_rejected_submit_with_sibling_calls_gets_a_result_for_every_tool_use() -> None:
    sibling_first = ToolSpy(content="before")
    sibling_after = ToolSpy(content="after")
    run = run_script(
        [
            calling(
                call(READ, call_id="toolu_before"),
                submit(answer=1, call_id="toolu_bad"),
                call("grep", call_id="toolu_after"),
            ),
            calling(submit(answer=42)),
        ],
        tools=(tool_named(READ, sibling_first), tool_named("grep", sibling_after)),
        validate=accept_only_42,
    )

    assert run.result == 42
    assert run.llm.requests[1].messages[-1]["content"] == [
        tool_result("toolu_before", "before"),
        tool_result("toolu_bad", "the answer must be 42", is_error=True),
        tool_result("toolu_after", "after"),
    ]


def test_a_non_submit_tool_error_is_not_a_correction() -> None:
    def fail(_tool_input: ToolInput) -> ToolOutcome:
        return ToolOutcome(content="no such file", is_error=True)

    run = run_script(
        [calling(call(READ)), calling(submit())],
        tools=(tool_named(READ, fail),),
    )

    assert run.tracer.events_of("correction") == []


def test_an_error_result_with_no_text_is_sent_with_a_stand_in_text() -> None:
    def fail_silently(_tool_input: ToolInput) -> ToolOutcome:
        return ToolOutcome(content="", is_error=True)

    run = run_script(
        [calling(call(READ, call_id="toolu_x")), calling(submit())],
        tools=(tool_named(READ, fail_silently),),
    )

    assert run.llm.requests[1].messages[-1]["content"] == [
        tool_result("toolu_x", NO_ERROR_MESSAGE, is_error=True)
    ]


def test_an_empty_result_that_is_not_an_error_is_sent_as_it_is() -> None:
    run = run_script(
        [calling(call(READ, call_id="toolu_x")), calling(submit())],
        tools=(tool_named(READ, ToolSpy(content="")),),
    )

    assert run.llm.requests[1].messages[-1]["content"] == [tool_result("toolu_x", "")]


def test_all_tool_results_go_back_in_one_user_message() -> None:
    first = ToolSpy(content="first output")
    second = ToolSpy(content="second output")
    run = run_script(
        [
            calling(call(READ, call_id="toolu_a"), call("grep", call_id="toolu_b")),
            calling(submit()),
        ],
        tools=(tool_named(READ, first), tool_named("grep", second)),
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
        tools=(tool_named(READ, ToolSpy()),),
    )

    assert run.llm.requests[1].messages[1] == {"role": "assistant", "content": [thinking, block]}


def test_every_request_carries_the_config_system_prompt_and_tool_definitions() -> None:
    read_tool = tool_named(READ, ToolSpy())
    run = run_script([calling(submit())], tools=(read_tool,))

    request = run.llm.requests[0]

    assert request.config.model == MODEL
    assert request.system == SYSTEM
    assert request.messages == ({"role": "user", "content": USER},)
    assert request.tools == (read_tool.definition, definition_named(SUBMIT))


def test_end_turn_without_submit_reprompts_and_counts_a_turn() -> None:
    run = run_script([reply("end_turn"), calling(submit())])

    assert run.result == 42
    assert run.end_of_agent()["turns"] == 2
    reprompt = run.llm.requests[1].messages[-1]
    assert reprompt["role"] == "user"
    assert SUBMIT in str(reprompt["content"])


def test_stop_sequence_is_reprompted_like_end_turn() -> None:
    run = run_script([reply("stop_sequence"), calling(submit())])

    assert run.result == 42
    assert SUBMIT in str(run.llm.requests[1].messages[-1]["content"])


def test_an_empty_end_turn_is_reprompted_without_an_empty_assistant_message() -> None:
    run = run_script([reply("end_turn", content=()), calling(submit())])

    assert run.result == 42
    assert [message["role"] for message in run.llm.requests[1].messages] == ["user", "user"]


def test_no_submit_three_turns_in_a_row_stops_no_progress() -> None:
    run = run_script([reply("end_turn"), reply("end_turn"), reply("end_turn")])

    assert_stopped(run, "no_progress", turns=3)
    assert len(run.llm.requests) == 3


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
        tools=(tool_named(READ, ToolSpy()),),
        validate=accept_only_42,
    )

    assert run.result == 42
    assert run.end_of_agent()["turns"] == 6


def test_a_refusal_stops_the_agent_before_any_tool_runs() -> None:
    spy = ToolSpy()
    run = run_script(
        [calling(call(READ), stop_reason="refusal")],
        tools=(tool_named(READ, spy),),
    )

    assert_stopped(run, "refused", turns=1)
    assert spy.inputs == []


def test_running_out_of_context_window_is_terminal_with_its_own_stop() -> None:
    spy = ToolSpy()
    run = run_script(
        [calling(call(READ), stop_reason="model_context_window_exceeded")],
        tools=(tool_named(READ, spy),),
    )

    assert_stopped(run, "context_window_exceeded", turns=1)
    assert spy.inputs == []
    assert len(run.llm.requests) == 1


def test_a_cut_off_turn_is_dropped_and_the_model_is_told_to_send_less() -> None:
    spy = ToolSpy()
    run = run_script(
        [calling(call(READ, path="partial"), stop_reason="max_tokens"), calling(submit())],
        tools=(tool_named(READ, spy),),
    )

    assert run.result == 42
    assert spy.inputs == []
    first, reminder = run.llm.requests[1].messages
    assert first == {"role": "user", "content": USER}
    assert reminder["role"] == "user"
    assert "less" in str(reminder["content"])
    assert run.tracer.events_of("tool_call")[0]["name"] == SUBMIT


def test_a_cut_off_submit_is_not_accepted() -> None:
    run = run_script(
        [calling(submit(answer=7), stop_reason="max_tokens"), calling(submit(answer=42))]
    )

    assert run.result == 42


def test_cut_off_turns_count_toward_the_idle_limit() -> None:
    cut_off = reply("max_tokens")

    run = run_script([cut_off, reply("end_turn"), cut_off])

    assert_stopped(run, "no_progress", turns=3)


def test_every_tool_use_gets_a_result_whatever_the_stop_reason() -> None:
    two_calls = calling(
        call(READ, call_id="toolu_a"), call(READ, call_id="toolu_b"), stop_reason="end_turn"
    )
    run = run_script(
        [two_calls, calling(submit())],
        tools=(tool_named(READ, ToolSpy()),),
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

    assert_stopped(run, "no_progress", turns=3)
    assert all(message["content"] for message in run.llm.requests[-1].messages)


def test_turn_limit_stops() -> None:
    run = run_script(
        [calling(call(READ)), calling(call(READ))],
        tools=(tool_named(READ, ToolSpy()),),
        max_turns=2,
    )

    assert_stopped(run, "turn_limit", turns=2)
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
        tools=(tool_named(READ, spy),),
    )

    assert run.result == 42
    assert len(spy.inputs) == 1
    assert len(run.llm.requests) == 1


def test_agent_stopped_carries_the_last_rejection_for_the_caller() -> None:
    run = run_script(
        [calling(submit(answer=1)), reply("end_turn"), reply("end_turn"), reply("end_turn")],
        validate=accept_only_42,
    )

    assert run.stopped is not None
    assert run.stopped.last_rejection == "the answer must be 42"


def test_a_later_tool_turn_does_not_forget_the_last_rejection() -> None:
    run = run_script(
        [calling(submit(answer=1)), calling(call(READ)), calling(call(READ))],
        tools=(tool_named(READ, ToolSpy()),),
        validate=accept_only_42,
        max_turns=3,
    )

    assert run.stopped is not None
    assert run.stopped.last_rejection == "the answer must be 42"


def test_agent_stopped_has_no_last_rejection_when_nothing_was_rejected() -> None:
    run = run_script([reply("end_turn"), reply("end_turn"), reply("end_turn")])

    assert run.stopped is not None
    assert run.stopped.last_rejection is None


@pytest.mark.parametrize(
    ("script", "stop"),
    [
        ([reply("refusal")], "refused"),
        ([reply("model_context_window_exceeded")], "context_window_exceeded"),
        ([reply("end_turn"), reply("end_turn"), reply("end_turn")], "no_progress"),
        ([calling(call("anything"))] * 10, "turn_limit"),
    ],
)
def test_each_stop_is_an_arde_error_naming_its_own_next_action(
    script: list[LLMResponse], stop: StopReason
) -> None:
    run = run_script(script)

    assert isinstance(run.stopped, ArdeError)
    assert run.stopped.stop == stop
    assert ROLE in str(run.stopped)
    assert stop in str(run.stopped)
    assert run.stopped.next_action
    assert run.stopped.exit_code is ExitCode.CHECKS_FAILED


def test_each_stop_gives_different_advice() -> None:
    stops = [
        run_script([reply("refusal")]).stopped,
        run_script([reply("model_context_window_exceeded")]).stopped,
        run_script([reply("end_turn")] * 3).stopped,
        run_script([calling(call("anything"))] * 10).stopped,
    ]

    assert len({stopped.next_action for stopped in stops if stopped is not None}) == 4


def test_no_call_is_sent_once_spending_reaches_the_cap_exactly() -> None:
    llm = FakeLLM([calling(call(READ), usage=EXPENSIVE), calling(submit())])
    budget = Budget(max_usd=cost_usd(MODEL, EXPENSIVE))
    spec = spec_with((tool_named(READ, ToolSpy()),), submit_tool())

    with pytest.raises(BudgetExceeded):
        run_agent(spec, AgentDeps(client=llm, tracer=RecordingTracer(), budget=budget))

    assert len(llm.requests) == 1


def test_budget_exceeded_propagates() -> None:
    spy = ToolSpy()

    with pytest.raises(BudgetExceeded):
        run_script(
            [calling(call(READ), usage=EXPENSIVE)],
            tools=(tool_named(READ, spy),),
            max_usd=0.01,
        )

    assert spy.inputs == []


def deps_with(llm: LLMClient, *, tracer: RecordingTracer, max_usd: float = 10.0) -> AgentDeps:
    return AgentDeps(client=llm, tracer=tracer, budget=Budget(max_usd=max_usd))


def test_the_call_that_trips_the_budget_is_still_traced() -> None:
    llm = FakeLLM([calling(submit(), usage=EXPENSIVE)])
    tracer = RecordingTracer()
    deps = deps_with(llm, tracer=tracer, max_usd=0.01)

    with pytest.raises(BudgetExceeded):
        run_agent(spec_with((), submit_tool()), deps)

    assert len(tracer.events_of("model_call")) == 1


def test_each_call_is_charged_to_the_role_under_the_configured_model() -> None:
    run = run_script([reply("end_turn"), calling(submit())])

    totals = run.budget.totals()[ROLE]

    assert totals.calls == 2
    assert totals.latency_s == 2 * LATENCY_S
    assert totals.cost_usd == pytest.approx(2 * cost_usd(MODEL, USAGE))


class TracerThatCannotWrite(RecordingTracer):
    def event(self, kind: str, **fields: object) -> None:
        raise OSError("disk full")


def test_a_tracer_failure_cannot_lose_a_paid_call_from_the_totals() -> None:
    deps = AgentDeps(
        client=FakeLLM([]), tracer=TracerThatCannotWrite(), budget=Budget(max_usd=10.0)
    )

    with pytest.raises(OSError, match="disk full"):
        record_model_call(reply("end_turn"), role=ROLE, model=MODEL, deps=deps)

    assert deps.budget.totals()[ROLE].calls == 1


def test_a_recorded_call_is_traced_and_charged_without_any_agent() -> None:
    tracer = RecordingTracer()
    deps = deps_with(FakeLLM([]), tracer=tracer)

    record_model_call(reply("end_turn"), role="classifier", model=MODEL, deps=deps)

    assert tracer.events_of("model_call")[0]["role"] == "classifier"
    assert deps.budget.totals()["classifier"].calls == 1


def test_unknown_tool_name_returns_tool_error() -> None:
    run = run_script([calling(call("delete_repo", call_id="toolu_x")), calling(submit())])

    assert run.result == 42
    returned = run.llm.requests[1].messages[-1]["content"]
    assert isinstance(returned, list)
    assert returned[0]["tool_use_id"] == "toolu_x"
    assert returned[0]["is_error"] is True
    assert "delete_repo" in returned[0]["content"]
    assert SUBMIT in returned[0]["content"]


class Page(BaseModel):
    number: int


def parse_page(tool_input: ToolInput) -> ToolOutcome:
    return ToolOutcome(content=str(Page.model_validate(tool_input).number))


def test_a_validation_error_from_a_handler_returns_to_the_model() -> None:
    run = run_script(
        [calling(call(READ, call_id="toolu_x", number="many")), calling(submit())],
        tools=(tool_named(READ, parse_page),),
    )

    assert run.result == 42
    returned = run.llm.requests[1].messages[-1]["content"]
    assert isinstance(returned, list)
    assert returned[0]["is_error"] is True
    assert "number" in returned[0]["content"]
    assert run.tracer.events_of("tool_call")[0]["ok"] is False


def test_a_validation_error_from_a_submit_is_a_correction_and_quotes_no_input() -> None:
    def validate(tool_input: ToolInput) -> Accepted[object]:
        return Accepted(Page.model_validate(tool_input))

    run = run_script(
        [calling(call(SUBMIT, number="sk-ant-quoted")), calling(call(SUBMIT, number=7))],
        validate=validate,
    )

    assert run.result == Page(number=7)
    assert len(run.tracer.events_of("correction")) == 1
    assert "sk-ant-quoted" not in str(run.llm.requests[1].messages[-1]["content"])


def crashes_with(error: Exception) -> Callable[[ToolInput], ToolOutcome]:
    def handler(_tool_input: ToolInput) -> ToolOutcome:
        raise error

    return handler


@pytest.mark.parametrize(
    "error",
    [
        AttributeError("'NoneType' object has no attribute 'splitlines'"),
        KeyError("profile"),
        OSError(28, "No space left on device"),
        ValueError("the disk is on fire"),
    ],
    ids=["attribute_error", "key_error", "disk_full", "value_error"],
)
def test_a_crash_in_a_tool_stops_the_run_and_is_never_sent_to_the_model(error: Exception) -> None:
    llm = FakeLLM([calling(call(READ)), calling(submit())])
    tracer = RecordingTracer()
    spec = spec_with((tool_named(READ, crashes_with(error)),), submit_tool())

    with pytest.raises(ToolCrashed) as raised:
        run_agent(spec, deps_with(llm, tracer=tracer))

    assert isinstance(raised.value, ArdeError)
    assert READ in str(raised.value)
    assert raised.value.next_action
    assert len(llm.requests) == 1
    assert any("Traceback" in line for line in tracer.debug_lines)
    assert tracer.events_of("tool_call")[-1]["ok"] is False


def test_a_crash_in_a_submit_validator_is_not_a_correction() -> None:
    def validate(tool_input: ToolInput) -> Accepted[object]:
        return Accepted(tool_input["profile"])

    llm = FakeLLM([calling(submit())])
    tracer = RecordingTracer()

    with pytest.raises(ToolCrashed):
        run_agent(spec_with((), submit_tool(validate)), deps_with(llm, tracer=tracer))

    assert tracer.events_of("correction") == []


def test_an_arde_error_from_a_handler_propagates_with_a_record_of_the_tool() -> None:
    def stop_the_run(_tool_input: ToolInput) -> ToolOutcome:
        raise ArdeError("stop", next_action="look", exit_code=ExitCode.CHECKS_FAILED)

    llm = FakeLLM([calling(call(READ))])
    tracer = RecordingTracer()
    spec = spec_with((tool_named(READ, stop_the_run),), submit_tool())

    with pytest.raises(ArdeError, match="stop") as raised:
        run_agent(spec, deps_with(llm, tracer=tracer))

    assert not isinstance(raised.value, ToolCrashed)
    assert tracer.events_of("tool_call") == [
        {"role": ROLE, "name": READ, "ok": False, "summary": "stop"}
    ]


def test_an_interrupt_during_a_handler_propagates() -> None:
    def interrupted(_tool_input: ToolInput) -> ToolOutcome:
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        run_script([calling(call(READ))], tools=(tool_named(READ, interrupted),))


class ClientThatFails(LLMClient):
    def __init__(self, error: ProviderError) -> None:
        self._error = error

    def complete(self, request: LLMRequest) -> LLMResponse:
        raise self._error

    def parse[T: BaseModel](self, request: LLMRequest, schema: type[T]) -> tuple[T, LLMResponse]:
        raise self._error


def test_a_failed_model_call_leaves_a_record_before_the_error_propagates() -> None:
    message = "Anthropic is overloaded: busy (HTTP 529, request id req_1)"
    tracer = RecordingTracer()
    deps = deps_with(ClientThatFails(ProviderError(message, next_action="Wait")), tracer=tracer)

    with pytest.raises(ProviderError):
        run_agent(spec_with((), submit_tool()), deps)

    assert tracer.events_of("model_error") == [{"role": ROLE, "model": MODEL, "error": message}]


def test_every_call_is_traced_with_usage_cost_and_latency() -> None:
    run = run_script([reply("end_turn"), calling(submit())])

    expected = {
        "role": ROLE,
        "model": MODEL,
        "usage": USAGE.model_dump(),
        "cost_usd": cost_usd(MODEL, USAGE),
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


def test_an_accepted_submit_traces_its_own_reply() -> None:
    def accept(_tool_input: ToolInput) -> Accepted[object]:
        return Accepted(1, reply="saved 3 facts")

    run = run_script([calling(submit())], validate=accept)

    assert run.tracer.events_of("tool_call")[0]["summary"] == "saved 3 facts"


def test_prompts_reach_the_debug_log_and_never_the_trace() -> None:
    run = run_script([calling(submit())])

    debug_log = "\n".join(run.tracer.debug_lines)
    assert SYSTEM in debug_log
    assert USER in debug_log
    assert SYSTEM not in str(run.tracer.events)
    assert USER not in str(run.tracer.events)


def test_the_model_text_reaches_the_debug_log_even_when_the_agent_stops() -> None:
    run = run_script([reply("refusal", text="I will not do that")])

    assert any("I will not do that" in line for line in run.tracer.debug_lines)


def test_tool_inputs_and_full_results_reach_the_debug_log() -> None:
    long_result = "word " * 10_000
    run = run_script(
        [calling(call(READ, path="handlers/price.go")), calling(submit())],
        tools=(tool_named(READ, ToolSpy(content=long_result)),),
    )

    debug_log = "\n".join(run.tracer.debug_lines)
    assert "handlers/price.go" in debug_log
    assert long_result in debug_log


@pytest.mark.parametrize(
    ("script", "expected"),
    [
        ([reply("end_turn"), calling(submit())], SUBMIT),
        ([reply("max_tokens"), calling(submit())], "less"),
    ],
    ids=["no_submit", "cut_off"],
)
def test_reprompts_reach_the_debug_log(script: list[LLMResponse], expected: str) -> None:
    run = run_script(script)

    reprompt = run.llm.requests[1].messages[-1]["content"]
    assert expected in str(reprompt)
    assert any(str(reprompt) in line for line in run.tracer.debug_lines)


# Passed whole, because only the tracer redacts, and a secret cut in half would not match.
def test_a_long_tool_result_reaches_the_tracer_whole() -> None:
    assert summary_of("word " * 10_000) == "word " * 10_000


FAKE_SECRET = "sk-ant-fake-secret-for-this-test-only"


def trace_kinds(run_dir: Path) -> list[str]:
    lines = (run_dir / "trace.jsonl").read_text(encoding="utf-8").splitlines()
    return [json.loads(line)["kind"] for line in lines]


def test_a_run_through_the_real_tracer_leaves_both_files_with_no_secret(tmp_path: Path) -> None:
    llm = FakeLLM(
        [
            reply("end_turn", text="I will read the file."),
            calling(call(READ, path="config.go")),
            calling(submit()),
        ]
    )
    read_secret = ToolSpy(content=f"token = {FAKE_SECRET}")
    run_dir = tmp_path / "run"
    deps = AgentDeps(
        client=llm,
        tracer=JsonlTracer(run_dir, secrets=(SecretStr(FAKE_SECRET),)),
        budget=Budget(max_usd=10.0),
    )

    run_agent(spec_with((tool_named(READ, read_secret),), submit_tool()), deps)

    trace = (run_dir / "trace.jsonl").read_text(encoding="utf-8")
    debug_log = (run_dir / "debug.log").read_text(encoding="utf-8")
    assert FAKE_SECRET not in trace + debug_log
    assert "token = [REDACTED]" in debug_log
    assert "I will read the file." in debug_log
    assert "config.go" in debug_log
    assert trace_kinds(run_dir) == [
        "model_call",
        "model_call",
        "tool_call",
        "model_call",
        "tool_call",
        "agent_end",
    ]


def real_tracer_run(handler: Callable[[ToolInput], ToolOutcome], run_dir: Path) -> FakeLLM:
    llm = FakeLLM([calling(call(READ, call_id="toolu_x")), calling(submit())])
    tracer = JsonlTracer(run_dir / "run", secrets=(SecretStr("sk-ant-unused-here"),))
    deps = AgentDeps(client=llm, tracer=tracer, budget=Budget(max_usd=10.0))
    run_agent(spec_with((tool_named(READ, handler),), submit_tool()), deps)
    return llm


def assert_encodable_as_utf8_on_the_wire(llm: FakeLLM) -> None:
    json.dumps(llm.requests[1].messages[-1], ensure_ascii=False).encode("utf-8")


def test_a_file_name_that_is_not_utf8_reaches_neither_the_tracer_nor_the_sdk(
    tmp_path: Path,
) -> None:
    llm = real_tracer_run(ToolSpy(content=f"files: {FILE_NAME_THAT_IS_NOT_UTF8}"), tmp_path)

    assert_encodable_as_utf8_on_the_wire(llm)
    assert r"caf\udce9.go" in llm.requests[1].messages[-1]["content"][0]["content"]


def test_a_rejection_quoting_a_bad_file_name_is_cleaned_too() -> None:
    def reject(_tool_input: ToolInput) -> Rejected:
        return Rejected(f"cannot read {FILE_NAME_THAT_IS_NOT_UTF8}")

    run = run_script(
        [calling(submit()), reply("end_turn"), reply("end_turn"), reply("end_turn")],
        validate=reject,
    )

    assert_encodable_as_utf8_on_the_wire(run.llm)
    assert run.stopped is not None
    assert run.stopped.last_rejection is not None
    run.stopped.last_rejection.encode("utf-8")


def test_a_crash_naming_a_bad_file_name_still_prints(tmp_path: Path) -> None:
    def crash(_tool_input: ToolInput) -> ToolOutcome:
        raise ValueError(f"cannot parse {FILE_NAME_THAT_IS_NOT_UTF8}")

    with pytest.raises(ToolCrashed) as raised:
        real_tracer_run(crash, tmp_path)

    str(raised.value).encode("utf-8")

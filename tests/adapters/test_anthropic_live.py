"""Calls the real Anthropic API. Run with make eval, which loads the key from .env. Costs money."""

import os
from collections.abc import Callable
from dataclasses import dataclass, field

import pytest
from pydantic import BaseModel
from recording_tracer import RecordingTracer

from arde.adapters.llm.anthropic_client import AnthropicClient
from arde.application.agent_loop import AgentDeps, AgentResult, AgentSpec, run_agent
from arde.application.budget import Budget
from arde.application.models_config import RoleConfig
from arde.application.ports import (
    LLMClient,
    LLMRequest,
    LLMResponse,
    ProviderError,
    Tool,
    ToolDef,
    ToolOutcome,
)
from arde.domain.base import strict_tool_schema
from arde.domain.facts import Wiki
from arde.domain.pricing import cost_usd
from arde.domain.profile import RepoProfile

pytestmark = pytest.mark.eval

HAIKU = RoleConfig(model="claude-haiku-5-5", effort="low", max_turns=3, max_tokens=1024)
OPUS = RoleConfig(model="claude-opus-5-5", effort="high", max_turns=4, max_tokens=4000)
# A runaway loop stops here, because the budget raises as soon as a call pushes spend past it.
SPEND_CAP_USD = 0.50

ToolInput = dict[str, object]


class ObservingClient(LLMClient):
    """Passes every call to the real client and keeps what went over, for the test to inspect."""

    def __init__(self, real: LLMClient) -> None:
        self._real = real
        self.requests: list[LLMRequest] = []
        self.responses: list[LLMResponse] = []

    def complete(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        response = self._real.complete(request)
        self.responses.append(response)
        return response

    def parse[T: BaseModel](self, request: LLMRequest, schema: type[T]) -> tuple[T, LLMResponse]:
        self.requests.append(request)
        parsed, response = self._real.parse(request, schema)
        self.responses.append(response)
        return parsed, response

    def block_types_per_turn(self) -> list[tuple[str, list[str]]]:
        return [
            (response.stop_reason, [_block_type(block) for block in response.assistant_content])
            for response in self.responses
        ]


def _block_type(block: object) -> str:
    return str(getattr(block, "type", None) or block)


@dataclass
class Live:
    # Out of the repr, because pytest prints a failing test's arguments with their repr.
    api_key: str = field(repr=False)
    tracer: RecordingTracer = field(default_factory=RecordingTracer)
    budget: Budget = field(default_factory=lambda: Budget(max_usd=SPEND_CAP_USD))

    def __post_init__(self) -> None:
        self.client = ObservingClient(AnthropicClient.from_api_key(self.api_key, self.tracer))

    def run(self, spec: AgentSpec, config: RoleConfig) -> AgentResult:
        deps = AgentDeps(client=self.client, config=config, tracer=self.tracer, budget=self.budget)
        return run_agent(spec, deps)

    def print_spend(self, label: str) -> None:
        for role, totals in self.budget.totals().items():
            print(
                f"[spend] {label} ({role}): {totals.calls} calls, in={totals.input_tokens} "
                f"out={totals.output_tokens} cache_read={totals.cache_read} "
                f"cache_write={totals.cache_write} cost=${totals.cost_usd:.5f} "
                f"latency={totals.latency_s:.1f}s"
            )
        print(f"[turns] {label}: {self.client.block_types_per_turn()}")

    # Compared as booleans, because a failed comparison of the key itself would print it.
    def assert_key_never_logged(self) -> None:
        key_was_logged = any(self.api_key in line for line in self.tracer.debug_lines)
        assert not key_was_logged, "the API key reached the debug log"
        logged_a_request_id = any("request id req_" in line for line in self.tracer.debug_lines)
        assert logged_a_request_id, "the adapter should log the request id"


@pytest.fixture
def live() -> Live:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        pytest.fail("ANTHROPIC_API_KEY is not set: run these tests with make eval, not pytest")
    return Live(api_key=api_key)


def tool(name: str, description: str, properties: dict[str, object], handler: Callable) -> Tool:  # type: ignore[type-arg]
    schema = {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }
    return Tool(
        definition=ToolDef(name=name, description=description, input_schema=schema),
        handler=handler,
    )


def accept_answer(tool_input: ToolInput) -> ToolOutcome:
    return ToolOutcome(content="accepted", submitted=tool_input["answer"])


def submit_tool(answer_type: str) -> Tool:
    return tool(
        "submit_answer",
        "Submit the final answer. Calling this ends the task.",
        {"answer": {"type": answer_type}},
        accept_answer,
    )


def spec_for(*, system: str, user: str, tools: tuple[Tool, ...]) -> AgentSpec:
    return AgentSpec(
        role="live", system=system, user=user, tools=tools, submit_tool="submit_answer"
    )


def test_haiku_calls_a_trivial_tool_through_run_agent(live: Live) -> None:
    spec = spec_for(
        system="Answer by calling submit_answer. Write nothing else.",
        user="What is 2 + 3?",
        tools=(submit_tool("integer"),),
    )

    result = live.run(spec, HAIKU)

    live.print_spend("haiku trivial tool")
    assert result == AgentResult(submitted=5, stop="submitted", turns=1)
    live.assert_key_never_logged()


def test_opus_echoed_thinking_block_is_accepted_on_the_second_turn(live: Live) -> None:
    look_up = tool(
        "look_up_color",
        "Look up the color registered for a numeric code.",
        {"code": {"type": "string"}},
        lambda tool_input: ToolOutcome(content="blue"),
    )
    spec = spec_for(
        system=(
            "Work the sum out first. Then call look_up_color with the sum as its code, and call "
            "submit_answer with the color it returned. Never guess the color."
        ),
        user="The sum to work out is 4817 + 9286 - 1375.",
        tools=(look_up, submit_tool("string")),
    )

    result = live.run(spec, OPUS)

    live.print_spend("opus two turns")
    assert result == AgentResult(submitted="blue", stop="submitted", turns=2)
    first_turn_blocks = live.client.block_types_per_turn()[0][1]
    assert "thinking" in first_turn_blocks, "the echo is proven only if turn 1 held thinking"
    echoed = live.client.requests[1].messages[1]["content"]
    assert [_block_type(block) for block in echoed] == first_turn_blocks
    live.assert_key_never_logged()


# The API refuses an empty error result (HTTP 400), so the adapter sends a stand-in text. An empty
# result that is not an error goes through as it is.
@pytest.mark.parametrize("is_error", [True, False])
def test_a_tool_result_with_empty_content_is_accepted(live: Live, is_error: bool) -> None:
    probe = tool(
        "probe",
        "Probe the service.",
        {"target": {"type": "string"}},
        lambda tool_input: ToolOutcome(content="", is_error=is_error),
    )
    spec = spec_for(
        system="Call probe once. Whatever it returns, then call submit_answer with 1.",
        user="The target is: api.",
        tools=(probe, submit_tool("integer")),
    )

    result = live.run(spec, HAIKU)

    live.print_spend(f"empty result, is_error={is_error}")
    assert result == AgentResult(submitted=1, stop="submitted", turns=2)
    sent_result = live.client.requests[1].messages[-1]["content"][0]
    assert (sent_result["content"], sent_result["is_error"]) == ("", is_error)
    live.assert_key_never_logged()


OPUS_ONE_SHOT = RoleConfig(model="claude-opus-5-5", effort="low", max_turns=1, max_tokens=200)


def request_with_one_tool(schema: dict[str, object], *, strict: bool) -> LLMRequest:
    submit = ToolDef(name="submit_it", description="Submit it.", input_schema=schema, strict=strict)
    return LLMRequest(
        config=OPUS_ONE_SHOT,
        system="Reply with the single word OK. Do not call any tool.",
        messages=({"role": "user", "content": "Hello"},),
        tools=(submit,),
    )


# Observed on 2026-11-02: the API refuses both schemas in strict mode. The rejection is not billed.
# Code validation holds the guarantee, so a tool with such a schema is built with strict=False.
# When this test fails on the rejection, the API has relaxed and strict can be tried again.
@pytest.mark.parametrize(
    ("model", "rejection"),
    [
        (RepoProfile, "'additionalProperties: object' is not supported"),
        (Wiki, "Schema type 'oneOf' is not supported"),
    ],
    ids=["open dict in RepoProfile", "oneOf in the Fact union"],
)
def test_a_schema_the_api_refuses_in_strict_mode_works_with_strict_off(
    live: Live, model: type[BaseModel], rejection: str
) -> None:
    schema = strict_tool_schema(model)

    with pytest.raises(ProviderError) as refused:
        live.client.complete(request_with_one_tool(schema, strict=True))
    response = live.client.complete(request_with_one_tool(schema, strict=False))

    print(f"[strict] {model.__name__} strict=True was refused: {refused.value}")
    print(
        f"[spend] {model.__name__} strict=False accepted: {response.usage.model_dump()} "
        f"cost=${cost_usd(OPUS_ONE_SHOT.model, response.usage):.5f}"
    )
    assert rejection in str(refused.value)
    assert response.stop_reason in ("end_turn", "max_tokens")
    live.assert_key_never_logged()


class Verdict(BaseModel):
    label: str


def test_haiku_structured_output_comes_back_parsed(live: Live) -> None:
    request = LLMRequest(
        config=HAIKU,
        system="Label the text as positive or negative.",
        messages=({"role": "user", "content": "I love this."},),
        tools=(),
    )

    verdict, response = live.client.parse(request, Verdict)

    print(
        f"[spend] haiku parse: {response.usage.model_dump()} "
        f"cost=${cost_usd(HAIKU.model, response.usage):.5f}"
    )
    assert verdict.label.lower() == "positive"
    live.assert_key_never_logged()

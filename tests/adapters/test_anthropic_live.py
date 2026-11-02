"""Calls the real Anthropic API. Run with make eval, which loads the key from .env. Costs money."""

import json
import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import BaseModel, SecretStr

from arde.adapters.llm.anthropic_client import AnthropicClient
from arde.adapters.tracer import JsonlTracer
from arde.application.agent_loop import (
    Accepted,
    AgentDeps,
    AgentSpec,
    SubmitTool,
    record_model_call,
    run_agent,
)
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
from arde.application.tool_schema import strict_tool_schema
from arde.domain.facts import Wiki
from arde.domain.pricing import cost_usd
from arde.domain.profile import DiscoveredProfile

pytestmark = pytest.mark.eval

HAIKU = RoleConfig(model="claude-haiku-5-5", effort="low", max_turns=3, max_tokens=1024)
OPUS = RoleConfig(model="claude-opus-5-5", effort="high", max_turns=4, max_tokens=4000)
OPUS_FOR_CACHING = RoleConfig(model="claude-opus-5-5", effort="low", max_turns=4, max_tokens=2000)
REPO_ROOT = Path(__file__).resolve().parents[2]
# A runaway loop stops here, because the budget raises as soon as a call pushes spend past it.
SPEND_CAP_USD = 0.50

ToolInput = dict[str, object]
ToolHandler = Callable[[ToolInput], ToolOutcome]


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
    # A SecretStr, because pytest prints the arguments of every frame of a failure with their repr.
    api_key: SecretStr
    holds_api_key: Callable[[str], bool]
    run_dir: Path
    budget: Budget = field(default_factory=lambda: Budget(max_usd=SPEND_CAP_USD))

    def __post_init__(self) -> None:
        self.tracer = JsonlTracer(self.run_dir, secrets=(self.api_key,))
        self.client = ObservingClient(AnthropicClient.from_api_key(self.api_key, self.tracer))

    def deps(self) -> AgentDeps:
        return AgentDeps(client=self.client, tracer=self.tracer, budget=self.budget)

    def run(self, spec: AgentSpec[object]) -> object:
        return run_agent(spec, self.deps())

    def print_spend(self, label: str) -> None:
        for role, totals in self.budget.totals().items():
            print(
                f"[spend] {label} ({role}): {totals.calls} calls, in={totals.input_tokens} "
                f"out={totals.output_tokens} cache_read={totals.cache_read} "
                f"cache_write={totals.cache_write} cost=${totals.cost_usd:.5f} "
                f"latency={totals.latency_s:.1f}s"
            )
        print(f"[turns] {label}: {self.client.block_types_per_turn()}")

    def trace_text(self) -> str:
        return (self.run_dir / "trace.jsonl").read_text(encoding="utf-8")

    def debug_text(self) -> str:
        return (self.run_dir / "debug.log").read_text(encoding="utf-8")

    def traced_kinds(self) -> list[str]:
        return [json.loads(line)["kind"] for line in self.trace_text().splitlines()]

    # A verdict, not a comparison, because a failed comparison of the key itself would print it.
    def assert_key_was_never_written(self) -> None:
        assert self.debug_text(), "nothing was logged, so this check would prove nothing"
        key_was_written = self.holds_api_key(self.trace_text() + self.debug_text())
        assert not key_was_written, "the API key reached trace.jsonl or debug.log"


@pytest.fixture(scope="session")
def eval_runs() -> Path:
    return REPO_ROOT / "runs" / "eval" / datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


# The folder survives the run so its files can be read, and is printed so it can be found. The key
# is checked after the test, not by it, so a test that fails on its own assertion is checked too.
@pytest.fixture
def live(
    api_key: SecretStr,
    holds_api_key: Callable[[str], bool],
    eval_runs: Path,
    request: pytest.FixtureRequest,
) -> Iterator[Live]:
    run_dir = eval_runs / re.sub(r"\W+", "-", request.node.name)
    session = Live(api_key=api_key, holds_api_key=holds_api_key, run_dir=run_dir)
    yield session
    print(f"[trace] {run_dir}")
    session.assert_key_was_never_written()


def tool_definition(*, name: str, description: str, properties: dict[str, object]) -> ToolDef:
    schema = {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }
    return ToolDef(name=name, description=description, input_schema=schema, strict=True)


def tool(
    *, name: str, description: str, properties: dict[str, object], handler: ToolHandler
) -> Tool:
    definition = tool_definition(name=name, description=description, properties=properties)
    return Tool(definition=definition, handler=handler)


def accept_answer(tool_input: ToolInput) -> Accepted[object]:
    return Accepted(tool_input["answer"])


def submit_tool(answer_type: str) -> SubmitTool[object]:
    definition = tool_definition(
        name="submit_answer",
        description="Submit the final answer. Calling this ends the task.",
        properties={"answer": {"type": answer_type}},
    )
    return SubmitTool(definition=definition, validate=accept_answer)


def spec_for(
    *,
    config: RoleConfig,
    system: str,
    user: str,
    tools: tuple[Tool, ...] = (),
    submit: SubmitTool[object],
) -> AgentSpec[object]:
    return AgentSpec(
        role="classifier", config=config, system=system, user=user, tools=tools, submit=submit
    )


def test_haiku_calls_a_trivial_tool_through_run_agent(live: Live) -> None:
    spec = spec_for(
        config=HAIKU,
        system="Answer by calling submit_answer. Write nothing else.",
        user="What is 2 + 3?",
        submit=submit_tool("integer"),
    )

    result = live.run(spec)

    live.print_spend("haiku trivial tool")
    assert result == 5
    assert "request id req_" in live.debug_text()
    assert live.traced_kinds() == ["model_call", "tool_call", "agent_end"]


def test_opus_echoed_thinking_block_is_accepted_on_the_second_turn(live: Live) -> None:
    look_up = tool(
        name="look_up_color",
        description="Look up the color registered for a numeric code.",
        properties={"code": {"type": "string"}},
        handler=lambda tool_input: ToolOutcome(content="blue"),
    )
    spec = spec_for(
        config=OPUS,
        system=(
            "Work the sum out first. Then call look_up_color with the sum as its code, and call "
            "submit_answer with the color it returned. Never guess the color."
        ),
        user="The sum to work out is 4817 + 9286 - 1375.",
        tools=(look_up,),
        submit=submit_tool("string"),
    )

    result = live.run(spec)

    live.print_spend("opus two turns")
    assert result == "blue"
    assert len(live.client.responses) == 2
    first_turn_blocks = live.client.block_types_per_turn()[0][1]
    assert "thinking" in first_turn_blocks, "the echo is proven only if turn 1 held thinking"
    echoed = live.client.requests[1].messages[1]["content"]
    assert [_block_type(block) for block in echoed] == first_turn_blocks


# The API refuses an empty error result (HTTP 400), so the loop sends a stand-in text. An empty
# result that is not an error goes through as it is.
@pytest.mark.parametrize("is_error", [True, False])
def test_a_tool_result_with_empty_content_is_accepted(live: Live, is_error: bool) -> None:
    probe = tool(
        name="probe",
        description="Probe the service.",
        properties={"target": {"type": "string"}},
        handler=lambda tool_input: ToolOutcome(content="", is_error=is_error),
    )
    spec = spec_for(
        config=HAIKU,
        system="Call probe once. Whatever it returns, then call submit_answer with 1.",
        user="The target is: api.",
        tools=(probe,),
        submit=submit_tool("integer"),
    )

    result = live.run(spec)

    live.print_spend(f"empty result, is_error={is_error}")
    assert result == 1
    assert len(live.client.responses) == 2
    sent_result = live.client.requests[1].messages[-1]["content"][0]
    assert bool(sent_result["content"]) is is_error
    assert sent_result["is_error"] is is_error


OPUS_ONE_SHOT = RoleConfig(model="claude-opus-5-5", effort="low", max_turns=1, max_tokens=200)


def request_with_one_tool(schema: dict[str, object], *, strict: bool) -> LLMRequest:
    submit = ToolDef(name="submit_it", description="Submit it.", input_schema=schema, strict=strict)
    return LLMRequest(
        config=OPUS_ONE_SHOT,
        system="Reply with the single word OK. Do not call any tool.",
        messages=({"role": "user", "content": "Hello"},),
        tools=(submit,),
    )


# Observed on 2026-11-02: the API refuses both schemas in strict mode.
# Code validation holds the guarantee, so a tool with such a schema is built with strict=False.
# When this test fails on the rejection, the API has relaxed and strict can be tried again.
@pytest.mark.parametrize(
    ("model", "rejection"),
    [
        (DiscoveredProfile, "'additionalProperties: object' is not supported"),
        (Wiki, "Schema type 'oneOf' is not supported"),
    ],
    ids=["open dict in DiscoveredProfile", "oneOf in the Fact union"],
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


# Long enough to pass the minimum a prefix needs before the API will cache it.
PADDING = " ".join(
    f"Rule {number}: every statement must trace to a checked fact." for number in range(90)
)


def test_the_second_turn_of_an_agent_reads_its_prompt_from_the_cache(live: Live) -> None:
    look_up = tool(
        name="look_up_color",
        description="Look up the color registered for a numeric code.",
        properties={"code": {"type": "string"}},
        handler=lambda tool_input: ToolOutcome(content="blue"),
    )
    spec = spec_for(
        config=OPUS_FOR_CACHING,
        system=f"Call look_up_color, then submit_answer with its result. {PADDING}",
        user="The code is 7.",
        tools=(look_up,),
        submit=submit_tool("string"),
    )

    result = live.run(spec)

    live.print_spend("opus cache hit")
    assert result == "blue"
    first_turn, second_turn = live.client.responses
    print(f"[cache] first={first_turn.usage} second={second_turn.usage}")
    assert first_turn.usage.cache_write + first_turn.usage.cache_read > 0
    assert second_turn.usage.cache_read > 0


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
    record_model_call(response, role="classifier", model=HAIKU.model, deps=live.deps())

    live.print_spend("haiku parse")
    assert verdict.label.lower() == "positive"
    assert live.traced_kinds() == ["model_call"]

"""Unit tests stand on the fake, so a script that runs out must fail loudly, not hang or loop."""

import pytest

from arde.adapters.llm.fake import FakeLLM
from arde.application.models_config import RoleConfig
from arde.application.ports import LLMRequest, LLMResponse
from arde.domain.usage import Usage

CONFIG = RoleConfig(model="claude-haiku-5-5", effort="low", max_turns=1, max_tokens=1024)
REQUEST = LLMRequest(config=CONFIG, system="", messages=(), tools=())


def response_with(text: str) -> LLMResponse:
    return LLMResponse(
        tool_calls=(),
        text=text,
        stop_reason="end_turn",
        usage=Usage(input_tokens=0, output_tokens=0, cache_read=0, cache_write=0),
        latency_s=0.0,
        assistant_content=(),
    )


def test_replies_follow_the_script_in_order() -> None:
    llm = FakeLLM([response_with("first"), response_with("second")])

    replies = [llm.complete(REQUEST).text, llm.complete(REQUEST).text]

    assert replies == ["first", "second"]


def test_every_request_is_recorded() -> None:
    llm = FakeLLM([response_with("only")])

    llm.complete(REQUEST)

    assert llm.requests == [REQUEST]


def test_an_exhausted_script_raises() -> None:
    llm = FakeLLM([])

    with pytest.raises(RuntimeError, match="script ran out"):
        llm.complete(REQUEST)


def test_replaying_leaves_the_callers_script_alone() -> None:
    script = [response_with("only")]
    llm = FakeLLM(script)

    llm.complete(REQUEST)

    assert len(script) == 1

"""The Anthropic adapter, run against a stand-in for the SDK client: no network and no key."""

import json
import threading
import traceback
from collections.abc import Callable, Iterator
from http.server import BaseHTTPRequestHandler
from socketserver import ThreadingTCPServer
from types import TracebackType
from typing import Self

import anthropic
import httpx2
import pytest
from anthropic.types import Message
from pydantic import BaseModel, SecretStr
from recording_tracer import RecordingTracer

from arde.adapters.llm import anthropic_client
from arde.adapters.llm.anthropic_client import AnthropicClient
from arde.application.agent_loop import Accepted, AgentDeps, AgentSpec, SubmitTool, run_agent
from arde.application.budget import Budget
from arde.application.models_config import RoleConfig
from arde.application.ports import (
    LLMRequest,
    ParseFailed,
    ProviderError,
    Refusal,
    ToolDef,
)
from arde.domain.errors import ArdeError
from arde.domain.report import ExitCode
from arde.domain.usage import Usage

MESSAGES_URL = "https://api.anthropic.com/v1/messages"
REQUEST_ID = "req_0123"
CONFIG = RoleConfig(model="claude-opus-5-5", effort="high", max_turns=3, max_tokens=2000)
READ_FILE = ToolDef(
    name="read_file",
    description="Read one file.",
    input_schema={
        "type": "object",
        "properties": {"path": {"type": "string"}},
        "required": ["path"],
        "additionalProperties": False,
    },
    strict=True,
)
READ_FILE_PARAMS = {
    "name": "read_file",
    "description": "Read one file.",
    "input_schema": READ_FILE.input_schema,
    "strict": True,
}
STREAM_HEADERS = {"content-type": "text/event-stream", "request-id": REQUEST_ID}
REPORTED_USAGE = Usage(input_tokens=3, output_tokens=4, cache_read=0, cache_write=0)
REQUEST = LLMRequest(
    config=CONFIG,
    system="You explore a service.",
    messages=({"role": "user", "content": "Find every route."},),
    tools=(READ_FILE,),
)


class Verdict(BaseModel):
    label: str


class FakeClock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


class StubMessages:
    """Stands in for client.messages and the stream it opens: records the call, then answers."""

    request_id = REQUEST_ID

    def __init__(
        self, outcome: object, *, clock: FakeClock | None = None, call_takes_s: float = 0.0
    ) -> None:
        self._outcome = outcome
        self._clock = clock
        self._call_takes_s = call_takes_s
        self.streamed: list[dict[str, object]] = []

    def stream(self, **kwargs: object) -> Self:
        self.streamed.append(kwargs)
        return self

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback_: TracebackType | None,
    ) -> None:
        return None

    def get_final_message(self) -> object:
        if self._clock is not None:
            self._clock.now += self._call_takes_s
        if isinstance(self._outcome, Exception):
            raise self._outcome
        return self._outcome

    # The SDK asserts this way when a stream delivered no event, and the stub delivers none.
    @property
    def current_message_snapshot(self) -> object:
        raise AssertionError


class StubSdk:
    def __init__(self, messages: StubMessages) -> None:
        self.messages = messages


def usage_fields(**overrides: int | None) -> dict[str, int | None]:
    return {"input_tokens": 3, "output_tokens": 4, **overrides}


def message_with(
    content: list[dict[str, object]] | None = None,
    *,
    stop_reason: str | None = "end_turn",
    usage: dict[str, int | None] | None = None,
    stop_details: dict[str, object] | None = None,
) -> Message:
    return Message.model_validate(
        {
            "id": "msg_1",
            "type": "message",
            "role": "assistant",
            "model": CONFIG.model,
            "content": content if content is not None else [{"type": "text", "text": "done"}],
            "stop_reason": stop_reason,
            "stop_details": stop_details,
            "stop_sequence": None,
            "usage": usage or usage_fields(),
        }
    )


def verdict_reply(label: str, *, stop_reason: str = "end_turn") -> Message:
    text = Verdict(label=label).model_dump_json()
    return message_with([{"type": "text", "text": text}], stop_reason=stop_reason)


def client_returning(
    outcome: object, tracer: RecordingTracer | None = None
) -> tuple[AnthropicClient, StubMessages]:
    messages = StubMessages(outcome)
    client = AnthropicClient(StubSdk(messages), tracer or RecordingTracer())
    return client, messages


def status_error(
    error_type: type[anthropic.APIStatusError],
    *,
    status: int,
    detail: str,
    body: object = None,
) -> anthropic.APIStatusError:
    request = httpx2.Request("POST", MESSAGES_URL)
    response = httpx2.Response(status, request=request, headers={"request-id": REQUEST_ID})
    return error_type(detail, response=response, body=body)


def connection_error() -> anthropic.APIConnectionError:
    return anthropic.APIConnectionError(request=httpx2.Request("POST", MESSAGES_URL))


def timeout_error() -> anthropic.APITimeoutError:
    return anthropic.APITimeoutError(httpx2.Request("POST", MESSAGES_URL))


def test_request_shape() -> None:
    client, sdk = client_returning(message_with())

    client.complete(REQUEST)

    assert sdk.streamed == [
        {
            "model": "claude-opus-5-5",
            "max_tokens": 2000,
            "system": "You explore a service.",
            "messages": [{"role": "user", "content": "Find every route."}],
            "tools": [READ_FILE_PARAMS],
            "cache_control": {"type": "ephemeral"},
            "output_config": {"effort": "high"},
        }
    ]


def test_every_call_streams_on_the_wire() -> None:
    sent: list[dict[str, object]] = []
    reply = api_reply([{"type": "text", "text": '{"label": "ok"}'}], "end_turn")
    client = AnthropicClient(sdk_over_mock_api(reply, sent), RecordingTracer())

    client.complete(REQUEST)
    client.parse(REQUEST, Verdict)

    assert [body["stream"] for body in sent] == [True, True]


# Above 21,333 the SDK refuses a call that does not stream, which is why every call streams.
@pytest.mark.parametrize("call", ["complete", "parse"])
def test_a_large_max_tokens_is_sent_on_both_paths(call: str) -> None:
    sent: list[dict[str, object]] = []
    reply = api_reply([{"type": "text", "text": '{"label": "ok"}'}], "end_turn")
    client = AnthropicClient(sdk_over_mock_api(reply, sent), RecordingTracer())
    large = REQUEST.model_copy(update={"config": CONFIG.model_copy(update={"max_tokens": 64_000})})

    client.complete(large) if call == "complete" else client.parse(large, Verdict)

    assert sent[0]["max_tokens"] == 64_000


def test_a_tool_that_is_not_strict_omits_the_field() -> None:
    loose_read_file = READ_FILE.model_copy(update={"strict": False})
    client, sdk = client_returning(message_with())

    client.complete(REQUEST.model_copy(update={"tools": (loose_read_file,)}))

    assert "strict" not in sdk.streamed[0]["tools"][0]


def test_a_request_without_tools_sends_no_tools_field() -> None:
    client, sdk = client_returning(message_with())

    client.complete(REQUEST.model_copy(update={"tools": ()}))

    assert "tools" not in sdk.streamed[0]


def test_usage_maps_cache_fields() -> None:
    usage = usage_fields(
        input_tokens=11,
        output_tokens=22,
        cache_read_input_tokens=33,
        cache_creation_input_tokens=44,
    )
    client, _ = client_returning(message_with(usage=usage))

    response = client.complete(REQUEST)

    assert response.usage == Usage(input_tokens=11, output_tokens=22, cache_read=33, cache_write=44)


def test_a_missing_cache_count_is_zero() -> None:
    usage = usage_fields(cache_read_input_tokens=None, cache_creation_input_tokens=None)
    client, _ = client_returning(message_with(usage=usage))

    response = client.complete(REQUEST)

    assert (response.usage.cache_read, response.usage.cache_write) == (0, 0)


def test_tool_calls_and_assistant_content_come_from_the_same_blocks() -> None:
    reply = message_with(
        [
            {"type": "thinking", "thinking": "", "signature": "sig-1"},
            {"type": "text", "text": "Reading both."},
            {"type": "tool_use", "id": "toolu_1", "name": "read_file", "input": {"path": "a.go"}},
            {"type": "tool_use", "id": "toolu_2", "name": "read_file", "input": {"path": "b.go"}},
        ],
        stop_reason="tool_use",
    )
    client, _ = client_returning(reply)

    response = client.complete(REQUEST)

    assert [(call.id, call.name, call.input) for call in response.tool_calls] == [
        ("toolu_1", "read_file", {"path": "a.go"}),
        ("toolu_2", "read_file", {"path": "b.go"}),
    ]
    assert response.text == "Reading both."
    assert response.stop_reason == "tool_use"
    assert len(response.assistant_content) == 4
    pairs = zip(response.assistant_content, reply.content, strict=True)
    assert all(echoed is original for echoed, original in pairs)


def test_a_refusal_with_no_content_maps_to_an_empty_response() -> None:
    client, _ = client_returning(message_with([], stop_reason="refusal"))

    response = client.complete(REQUEST)

    assert (response.stop_reason, response.tool_calls, response.assistant_content) == (
        "refusal",
        (),
        (),
    )


def test_latency_is_measured_around_the_call() -> None:
    clock = FakeClock()
    messages = StubMessages(message_with(), clock=clock, call_takes_s=2.5)
    client = AnthropicClient(StubSdk(messages), RecordingTracer(), clock=clock)

    response = client.complete(REQUEST)

    assert response.latency_s == 2.5


# Only the stream knows it: a streamed final message has no _request_id, as a plain reply does.
def test_the_request_id_comes_from_the_stream() -> None:
    reply = api_reply([{"type": "text", "text": "done"}], "end_turn")
    client = AnthropicClient(sdk_over_mock_api(reply, []), RecordingTracer())

    assert client.complete(REQUEST).request_id == REQUEST_ID


def test_provider_error_is_an_arde_error_the_application_can_catch() -> None:
    error = ProviderError("The provider failed", next_action="Run again")

    assert isinstance(error, ArdeError)
    assert error.exit_code == ExitCode.CHECKS_FAILED
    assert error.next_action == "Run again"


def test_provider_error_can_say_the_setup_is_wrong() -> None:
    error = ProviderError("Bad key", next_action="Fix it", exit_code=ExitCode.BAD_USAGE)

    assert error.exit_code == ExitCode.BAD_USAGE


def raises_provider_error(
    sdk_error: Exception, tracer: RecordingTracer | None = None
) -> tuple[ProviderError, StubMessages]:
    client, messages = client_returning(sdk_error, tracer)
    with pytest.raises(ProviderError) as raised:
        client.complete(REQUEST)
    return raised.value, messages


def test_auth_error_becomes_provider_error_with_next_action() -> None:
    error, _ = raises_provider_error(
        status_error(anthropic.AuthenticationError, status=401, detail="invalid x-api-key")
    )

    assert "rejected the API key" in str(error)
    assert "invalid x-api-key" in str(error)
    assert REQUEST_ID in str(error)
    assert "ANTHROPIC_API_KEY" in error.next_action


def test_the_api_message_replaces_the_sdk_dump_of_the_whole_body() -> None:
    body = {
        "type": "error",
        "error": {"type": "authentication_error", "message": "invalid x-api-key"},
        "request_id": REQUEST_ID,
    }
    sdk_error = status_error(
        anthropic.AuthenticationError, status=401, detail=f"Error code: 401 - {body}", body=body
    )

    error, _ = raises_provider_error(sdk_error)

    assert str(error) == (
        f"Anthropic rejected the API key: invalid x-api-key (HTTP 401, request id {REQUEST_ID})"
    )


def test_rate_limit_after_retries_becomes_provider_error() -> None:
    error, messages = raises_provider_error(
        status_error(anthropic.RateLimitError, status=429, detail="rate limit reached")
    )

    assert "rate limit" in str(error).lower()
    assert "Wait" in error.next_action
    # The SDK has already retried by the time it raises. A second layer would multiply the waits.
    assert len(messages.streamed) == 1


def test_connection_error_becomes_provider_error() -> None:
    error, _ = raises_provider_error(connection_error())

    assert "reach Anthropic" in str(error)
    assert "network" in error.next_action


def test_a_timeout_is_told_apart_from_a_refused_connection() -> None:
    error, _ = raises_provider_error(timeout_error())

    assert str(error).startswith("The request to Anthropic timed out")


@pytest.mark.parametrize(
    ("sdk_error", "problem", "next_action_mentions"),
    [
        (
            status_error(anthropic.PermissionDeniedError, status=403, detail="no access"),
            "not allowed",
            "model",
        ),
        (
            status_error(anthropic.NotFoundError, status=404, detail="model: nope"),
            "model",
            "ARDE_MODELS_FILE",
        ),
        (
            status_error(anthropic.BadRequestError, status=400, detail="schema too big"),
            "invalid",
            "ARDE_MODELS_FILE",
        ),
        (
            status_error(anthropic.OverloadedError, status=529, detail="Overloaded"),
            "overloaded",
            "again",
        ),
        (
            status_error(anthropic.InternalServerError, status=500, detail="oops"),
            "server error",
            "again",
        ),
        (
            status_error(
                anthropic.APIStatusError,
                status=402,
                detail="Your credit balance is too low",
                body={
                    "type": "error",
                    "error": {"type": "billing_error", "message": "Your credit balance is too low"},
                },
            ),
            "billing",
            "Console",
        ),
        (
            status_error(anthropic.RequestTooLargeError, status=413, detail="too large"),
            "too large",
            "size limit",
        ),
        (
            status_error(anthropic.UnprocessableEntityError, status=422, detail="bad"),
            "call failed",
            "report",
        ),
    ],
)
def test_every_api_status_error_names_a_next_action(
    sdk_error: anthropic.APIStatusError, problem: str, next_action_mentions: str
) -> None:
    error, _ = raises_provider_error(sdk_error)

    assert problem in str(error)
    assert str(sdk_error) in str(error)
    assert REQUEST_ID in str(error)
    assert next_action_mentions in error.next_action


def test_a_bad_request_says_it_may_be_a_setting_or_a_bug() -> None:
    error, _ = raises_provider_error(
        status_error(anthropic.BadRequestError, status=400, detail="no such effort")
    )

    assert "request id" in error.next_action


def test_an_error_the_sdk_raises_for_other_reasons_is_wrapped_too() -> None:
    error, _ = raises_provider_error(anthropic.CredentialsError("no credentials"))

    assert "no credentials" in str(error)
    assert error.next_action


def forbidden() -> anthropic.APIStatusError:
    return status_error(anthropic.PermissionDeniedError, status=403, detail="no")


def unknown_model() -> anthropic.APIStatusError:
    return status_error(anthropic.NotFoundError, status=404, detail="model: nope")


def rejected_key() -> anthropic.APIStatusError:
    return status_error(anthropic.AuthenticationError, status=401, detail="invalid x-api-key")


def rate_limited() -> anthropic.APIStatusError:
    return status_error(anthropic.RateLimitError, status=429, detail="slow down")


def server_error() -> anthropic.APIStatusError:
    return status_error(anthropic.InternalServerError, status=500, detail="oops")


def overloaded() -> anthropic.APIStatusError:
    return status_error(anthropic.OverloadedError, status=529, detail="busy")


@pytest.mark.parametrize("failure", [rejected_key, forbidden, unknown_model])
def test_a_bad_key_or_model_is_bad_usage(failure: Callable[[], Exception]) -> None:
    error, _ = raises_provider_error(failure())

    assert error.exit_code is ExitCode.BAD_USAGE


@pytest.mark.parametrize(
    "failure", [rate_limited, server_error, overloaded, connection_error, timeout_error]
)
def test_an_outage_during_a_run_stays_exit_1(failure: Callable[[], Exception]) -> None:
    error, _ = raises_provider_error(failure())

    assert error.exit_code is ExitCode.CHECKS_FAILED


# A transport error's chain holds the request headers, so nothing may link back to any SDK error.
@pytest.mark.parametrize(
    "failure", [rejected_key, rate_limited, overloaded, connection_error, timeout_error]
)
def test_no_failure_keeps_a_link_to_the_sdk_error(failure: Callable[[], Exception]) -> None:
    error, _ = raises_provider_error(failure())

    assert error.__cause__ is None
    assert error.__context__ is None


def test_parse_returns_the_validated_value_and_the_response() -> None:
    client, _ = client_returning(verdict_reply("ok"))

    verdict, response = client.parse(REQUEST, Verdict)

    assert verdict == Verdict(label="ok")
    assert response.usage == Usage(input_tokens=3, output_tokens=4, cache_read=0, cache_write=0)


def test_parse_sends_the_schema_beside_effort_on_the_cached_streaming_path() -> None:
    client, sdk = client_returning(verdict_reply("ok"))

    client.parse(REQUEST, Verdict)

    assert sdk.streamed == [
        {
            "model": "claude-opus-5-5",
            "max_tokens": 2000,
            "system": "You explore a service.",
            "messages": [{"role": "user", "content": "Find every route."}],
            "tools": [READ_FILE_PARAMS],
            "cache_control": {"type": "ephemeral"},
            "output_config": {
                "effort": "high",
                "format": {"type": "json_schema", "schema": anthropic.transform_schema(Verdict)},
            },
        }
    ]


@pytest.mark.parametrize(
    ("stop_reason", "advice"),
    [
        ("max_tokens", "max_tokens"),
        ("refusal", "declined"),
        ("tool_use", "stop reason"),
        ("stop_sequence", "stop reason"),
    ],
)
def test_parse_refuses_a_reply_that_did_not_end_normally(stop_reason: str, advice: str) -> None:
    client, _ = client_returning(verdict_reply("ok", stop_reason=stop_reason))

    with pytest.raises(ParseFailed) as raised:
        client.parse(REQUEST, Verdict)

    assert stop_reason in str(raised.value)
    assert advice in raised.value.next_action


def test_parse_with_no_text_block_is_a_failure_that_keeps_the_usage() -> None:
    client, _ = client_returning(message_with([], stop_reason="refusal"))

    with pytest.raises(ParseFailed) as raised:
        client.parse(REQUEST, Verdict)

    assert "refusal" in str(raised.value)
    assert raised.value.next_action
    assert raised.value.usage.input_tokens == 3


@pytest.mark.parametrize(
    "text", ['{"label": 5}', "not json at all", ""], ids=["schema_break", "not_json", "empty"]
)
def test_a_paid_reply_that_fails_validation_keeps_its_usage(text: str) -> None:
    client, _ = client_returning(message_with([{"type": "text", "text": text}]))

    with pytest.raises(ParseFailed) as raised:
        client.parse(REQUEST, Verdict)

    assert isinstance(raised.value, ProviderError)
    assert raised.value.usage == REPORTED_USAGE
    assert raised.value.paid_call is not None
    assert raised.value.paid_call.usage == REPORTED_USAGE
    assert "Verdict" in str(raised.value)
    assert raised.value.__cause__ is None


def test_parse_errors_from_the_api_become_provider_errors() -> None:
    client, _ = client_returning(
        status_error(anthropic.RateLimitError, status=429, detail="slow down")
    )

    with pytest.raises(ProviderError):
        client.parse(REQUEST, Verdict)


def api_reply(content: list[dict[str, object]], stop_reason: str | None) -> dict[str, object]:
    return {
        "id": "msg_1",
        "type": "message",
        "role": "assistant",
        "model": CONFIG.model,
        "content": content,
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": usage_fields(),
    }


def block_events(index: int, block: dict[str, object]) -> list[dict[str, object]]:
    if block["type"] == "text":
        opening: dict[str, object] = {"type": "text", "text": ""}
        delta: dict[str, object] = {"type": "text_delta", "text": block["text"]}
    elif block["type"] == "thinking":
        opening = {"type": "thinking", "thinking": "", "signature": ""}
        delta = {"type": "signature_delta", "signature": block["signature"]}
    else:
        opening = {**block, "input": {}}
        delta = {"type": "input_json_delta", "partial_json": json.dumps(block["input"])}
    return [
        {"type": "content_block_start", "index": index, "content_block": opening},
        {"type": "content_block_delta", "index": index, "delta": delta},
        {"type": "content_block_stop", "index": index},
    ]


def stream_events(reply: dict[str, object]) -> list[dict[str, object]]:
    blocks = [
        event
        for index, block in enumerate(reply["content"])
        for event in block_events(index, block)
    ]
    finish = {"stop_reason": reply["stop_reason"], "stop_sequence": None}
    return [
        {"type": "message_start", "message": {**reply, "content": [], "stop_reason": None}},
        *blocks,
        {"type": "message_delta", "delta": finish, "usage": {"output_tokens": 4}},
        {"type": "message_stop"},
    ]


def sse_bytes(events: list[dict[str, object]]) -> bytes:
    return b"".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n".encode() for e in events)


def event_stream(events: list[dict[str, object]]) -> httpx2.Response:
    return httpx2.Response(200, content=sse_bytes(events), headers=STREAM_HEADERS)


def sdk_answering(answer: Callable[[httpx2.Request], httpx2.Response]) -> anthropic.Anthropic:
    return anthropic.Anthropic(
        api_key="sk-test-not-a-real-key",
        max_retries=0,
        http_client=anthropic.DefaultHttpxClient(transport=httpx2.MockTransport(answer)),
    )


def sdk_over_mock_api(
    reply: dict[str, object], sent: list[dict[str, object]]
) -> anthropic.Anthropic:
    def answer(request: httpx2.Request) -> httpx2.Response:
        sent.append(json.loads(request.content))
        return event_stream(stream_events(reply))

    return sdk_answering(answer)


def test_the_sdk_retries_before_the_adapter_gives_up() -> None:
    attempts: list[httpx2.Request] = []

    def always_rate_limited(request: httpx2.Request) -> httpx2.Response:
        attempts.append(request)
        body = {"type": "error", "error": {"type": "rate_limit_error", "message": "slow down"}}
        headers = {"request-id": REQUEST_ID, "retry-after-ms": "1"}
        return httpx2.Response(429, json=body, headers=headers)

    sdk = anthropic.Anthropic(
        api_key="sk-test-not-a-real-key",
        http_client=anthropic.DefaultHttpxClient(
            transport=httpx2.MockTransport(always_rate_limited)
        ),
    )

    with pytest.raises(ProviderError) as raised:
        AnthropicClient(sdk, RecordingTracer()).complete(REQUEST)

    assert len(attempts) == 3
    assert "slow down" in str(raised.value)


def test_a_reply_cut_off_mid_json_is_a_failure_naming_max_tokens() -> None:
    reply = api_reply([{"type": "text", "text": '{"label"'}], "max_tokens")
    client = AnthropicClient(sdk_over_mock_api(reply, []), RecordingTracer())

    with pytest.raises(ParseFailed) as raised:
        client.parse(REQUEST, Verdict)

    assert "max_tokens" in raised.value.next_action


def test_the_wire_request_carries_effort_beside_the_schema_and_strict_tools() -> None:
    sent: list[dict[str, object]] = []
    reply = api_reply([{"type": "text", "text": '{"label": "ok"}'}], "end_turn")
    client = AnthropicClient(sdk_over_mock_api(reply, sent), RecordingTracer())

    verdict, _ = client.parse(REQUEST, Verdict)

    assert verdict == Verdict(label="ok")
    body = sent[0]
    assert body["output_config"]["effort"] == "high"
    assert body["output_config"]["format"]["type"] == "json_schema"
    assert body["tools"][0]["strict"] is True


def test_echoed_thinking_blocks_go_back_on_the_wire_unchanged() -> None:
    first_reply = api_reply(
        [
            {"type": "thinking", "thinking": "", "signature": "sig-abc"},
            {"type": "tool_use", "id": "toolu_1", "name": "read_file", "input": {"path": "a.go"}},
        ],
        "tool_use",
    )
    sent: list[dict[str, object]] = []
    client = AnthropicClient(sdk_over_mock_api(first_reply, sent), RecordingTracer())
    first = client.complete(REQUEST)
    second_turn = (
        *REQUEST.messages,
        {"role": "assistant", "content": list(first.assistant_content)},
        {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": "toolu_1", "content": "ok"}],
        },
    )

    client.complete(REQUEST.model_copy(update={"messages": second_turn}))

    echoed = sent[1]["messages"][1]["content"]
    assert echoed[0] == {"type": "thinking", "thinking": "", "signature": "sig-abc"}
    assert echoed[1]["id"] == "toolu_1"
    assert sent[1]["cache_control"] == {"type": "ephemeral"}


def test_a_failure_that_cannot_succeed_on_a_second_try_is_not_told_to_run_again() -> None:
    billing_body = {"type": "error", "error": {"type": "billing_error", "message": "low"}}
    unrepeatable = [
        status_error(anthropic.APIStatusError, status=402, detail="low", body=billing_body),
        status_error(anthropic.RequestTooLargeError, status=413, detail="too large"),
    ]

    next_actions = [raises_provider_error(sdk_error)[0].next_action for sdk_error in unrepeatable]

    assert [action for action in next_actions if "Run again" in action] == []


def test_a_reply_that_is_valid_json_but_breaks_the_schema_does_not_blame_max_tokens() -> None:
    reply = api_reply([{"type": "text", "text": '{"label": 5}'}], "end_turn")
    client = AnthropicClient(sdk_over_mock_api(reply, []), RecordingTracer())

    with pytest.raises(ParseFailed) as raised:
        client.parse(REQUEST, Verdict)

    assert "max_tokens" not in raised.value.next_action
    assert "Verdict" in str(raised.value)
    assert raised.value.next_action


def status_error_event(error_type: str, message: str) -> bytes:
    error = {"type": "error", "error": {"type": error_type, "message": message}}
    return f"event: error\ndata: {json.dumps(error)}\n\n".encode()


class BreaksOff(httpx2.SyncByteStream):
    """A response body that delivers its first bytes, and then the connection fails."""

    def __init__(self, first: bytes, failure: Exception) -> None:
        self._first = first
        self._failure = failure

    def __iter__(self) -> Iterator[bytes]:
        yield self._first
        raise self._failure


def breaks_off_with(failure: Exception) -> httpx2.Response:
    first = sse_bytes(stream_events(api_reply([], "end_turn"))[:1])
    return httpx2.Response(200, stream=BreaksOff(first, failure), headers=STREAM_HEADERS)


FULL_REPLY = api_reply([{"type": "text", "text": "done"}], "end_turn")
FULL_STREAM = stream_events(FULL_REPLY)


def with_finish(**finish: object) -> list[dict[str, object]]:
    delta = {"stop_sequence": None, **finish}
    return [
        {**event, "delta": delta} if event["type"] == "message_delta" else event
        for event in FULL_STREAM
    ]


def with_stop_reason(stop_reason: str | None) -> list[dict[str, object]]:
    return with_finish(stop_reason=stop_reason)


def with_usage(usage: object) -> list[dict[str, object]]:
    start = {**FULL_STREAM[0]["message"], "usage": usage}
    return [{"type": "message_start", "message": start}, *FULL_STREAM[1:]]


def with_message_field_removed(field: str) -> list[dict[str, object]]:
    start = {key: value for key, value in FULL_STREAM[0]["message"].items() if key != field}
    return [{"type": "message_start", "message": start}, *FULL_STREAM[1:]]


def tool_input_stream(input_json: str) -> list[dict[str, object]]:
    block = {"type": "tool_use", "id": "toolu_1", "name": "read_file", "input": {}}
    delta = {"type": "input_json_delta", "partial_json": input_json}
    return [
        FULL_STREAM[0],
        {"type": "content_block_start", "index": 0, "content_block": block},
        {"type": "content_block_delta", "index": 0, "delta": delta},
        {"type": "content_block_stop", "index": 0},
        *FULL_STREAM[-2:],
    ]


TEXT_DELTA = FULL_STREAM[2]
Answer = Callable[[httpx2.Request], httpx2.Response]


# What a stream cannot be trusted to deliver, each measured against the real SDK. Most of these
# reach the adapter as something other than an AnthropicError. The API had answered in each of
# these, and its first event had said what the prompt used, so each one was paid for.
ANSWERS_THAT_REPORTED_USAGE: dict[str, Answer] = {
    "ends before the stop reason": lambda _: event_stream(FULL_STREAM[:4]),
    "no stop reason": lambda _: event_stream(with_stop_reason(None)),
    "pause_turn with no server tools": lambda _: event_stream(with_stop_reason("pause_turn")),
    "a stop reason that is new": lambda _: event_stream(with_stop_reason("something_new")),
    "connection reset": lambda _: breaks_off_with(httpx2.ReadError("reset")),
    "peer closed": lambda _: breaks_off_with(httpx2.RemoteProtocolError("peer closed")),
    "read timeout": lambda _: breaks_off_with(httpx2.ReadTimeout("slow")),
    "delta for a block that is not there": lambda _: event_stream(
        [FULL_STREAM[0], {**TEXT_DELTA, "index": 5}]
    ),
    "message delta with no delta": lambda _: event_stream(
        [FULL_STREAM[0], {"type": "message_delta", "usage": {"output_tokens": 4}}]
    ),
    "tool input that will not parse": lambda _: event_stream(tool_input_stream("{oops")),
    "tool input that is no object": lambda _: event_stream(tool_input_stream('"partial"')),
}

ANSWERS_WITH_NOTHING_TO_CHARGE: dict[str, Answer] = {
    "html from a proxy": lambda _: httpx2.Response(
        200, content=b"<html>Sign in</html>", headers={"content-type": "text/html"}
    ),
    "json that is no stream": lambda _: httpx2.Response(200, json={"hello": "world"}),
    "empty body": lambda _: httpx2.Response(200, content=b""),
    "broken json in an event": lambda _: httpx2.Response(
        200, content=b"event: message_start\ndata: {oops\n\n", headers=STREAM_HEADERS
    ),
    "events out of order": lambda _: event_stream(FULL_STREAM[1:]),
    "negative usage": lambda _: event_stream(with_usage({"input_tokens": -1, "output_tokens": 1})),
    "usage with no counts": lambda _: event_stream(with_usage({})),
    "usage that is null": lambda _: event_stream(with_usage(None)),
    "usage that is missing": lambda _: event_stream(with_message_field_removed("usage")),
    "message start with no message": lambda _: event_stream([{"type": "message_start"}]),
}

UNUSABLE_ANSWERS = {**ANSWERS_THAT_REPORTED_USAGE, **ANSWERS_WITH_NOTHING_TO_CHARGE}


def call_failing_with(answer: Answer, call: str) -> ProviderError:
    client = AnthropicClient(sdk_answering(answer), RecordingTracer())
    with pytest.raises(ProviderError) as raised:
        client.complete(REQUEST) if call == "complete" else client.parse(REQUEST, Verdict)
    return raised.value


@pytest.mark.parametrize("call", ["complete", "parse"])
@pytest.mark.parametrize("answer", UNUSABLE_ANSWERS.values(), ids=UNUSABLE_ANSWERS.keys())
def test_an_unusable_reply_is_a_provider_error_with_a_next_action(
    answer: Answer, call: str
) -> None:
    error = call_failing_with(answer, call)

    assert error.next_action
    assert error.__cause__ is None
    assert error.__context__ is None


@pytest.mark.parametrize("call", ["complete", "parse"])
@pytest.mark.parametrize(
    "answer", ANSWERS_THAT_REPORTED_USAGE.values(), ids=ANSWERS_THAT_REPORTED_USAGE.keys()
)
def test_a_failure_after_the_api_answered_carries_what_it_reported(
    answer: Answer, call: str
) -> None:
    error = call_failing_with(answer, call)

    assert error.paid_call is not None
    assert error.paid_call.usage == REPORTED_USAGE
    assert error.paid_call.request_id == REQUEST_ID
    assert error.paid_call.latency_s >= 0


@pytest.mark.parametrize("call", ["complete", "parse"])
@pytest.mark.parametrize(
    "answer", ANSWERS_WITH_NOTHING_TO_CHARGE.values(), ids=ANSWERS_WITH_NOTHING_TO_CHARGE.keys()
)
def test_a_failure_with_no_readable_usage_has_nothing_to_charge(answer: Answer, call: str) -> None:
    assert call_failing_with(answer, call).paid_call is None


def test_a_status_error_before_any_reply_has_nothing_to_charge() -> None:
    body = {"type": "error", "error": {"type": "rate_limit_error", "message": "slow down"}}

    error = call_failing_with(lambda _: httpx2.Response(429, json=body), "complete")

    assert error.paid_call is None


# The SDK builds a streamed message without validating it, so a block's text can be None.
TEXT_BLOCK_WITH_NO_TEXT = {
    "type": "content_block_start",
    "index": 0,
    "content_block": {"type": "text"},
}


def test_a_text_block_with_no_text_is_read_as_empty() -> None:
    answer = event_stream([FULL_STREAM[0], TEXT_BLOCK_WITH_NO_TEXT, *FULL_STREAM[3:]])
    client = AnthropicClient(sdk_answering(lambda _: answer), RecordingTracer())

    assert client.complete(REQUEST).text == ""


def test_a_text_block_with_no_text_cannot_hide_the_failure_after_it() -> None:
    def answer(_request: httpx2.Request) -> httpx2.Response:
        first = sse_bytes([FULL_STREAM[0], TEXT_BLOCK_WITH_NO_TEXT])
        return httpx2.Response(
            200, stream=BreaksOff(first, httpx2.ReadError("reset")), headers=STREAM_HEADERS
        )

    error = call_failing_with(answer, "complete")

    assert "ReadError" in str(error)
    assert error.paid_call is not None
    assert error.paid_call.usage == REPORTED_USAGE


def test_a_failed_call_keeps_the_text_and_the_stop_reason_it_got() -> None:
    error = call_failing_with(lambda _: event_stream(with_stop_reason("pause_turn")), "complete")

    assert error.paid_call is not None
    assert error.paid_call.text == "done"
    assert error.paid_call.stop_reason == "pause_turn"


def agent_on(sdk: anthropic.Anthropic, deps_tracer: RecordingTracer) -> AgentDeps:
    return AgentDeps(
        client=AnthropicClient(sdk, deps_tracer),
        tracer=deps_tracer,
        budget=Budget(max_usd=10.0),
    )


SUBMIT_ANYTHING = SubmitTool[object](
    definition=ToolDef(
        name="submit", description="Submit.", input_schema={"type": "object"}, strict=False
    ),
    validate=lambda tool_input: Accepted(tool_input),
)
AGENT = AgentSpec[object](
    role="explore",
    config=CONFIG,
    system="You explore a service.",
    user="Find every route.",
    tools=(),
    submit=SUBMIT_ANYTHING,
)


@pytest.mark.parametrize(
    "answer",
    [
        lambda _: event_stream(with_stop_reason("pause_turn")),
        lambda _: event_stream(FULL_STREAM[:4]),
        lambda _: breaks_off_with(httpx2.ReadError("reset")),
    ],
    ids=["stop_reason_outside_the_port", "stream_ended_early", "connection_dropped"],
)
def test_a_paid_reply_that_fails_reaches_the_budget_and_the_trace(answer: Answer) -> None:
    tracer = RecordingTracer()
    deps = agent_on(sdk_answering(answer), tracer)

    with pytest.raises(ProviderError):
        run_agent(AGENT, deps)

    assert deps.budget.totals()["explore"].input_tokens == REPORTED_USAGE.input_tokens
    assert len(tracer.events_of("model_call")) == 1
    assert len(tracer.events_of("model_error")) == 1


def test_a_failed_call_with_no_reply_charges_nothing_and_still_leaves_a_record() -> None:
    tracer = RecordingTracer()
    answer = UNUSABLE_ANSWERS["html from a proxy"]
    deps = agent_on(sdk_answering(answer), tracer)

    with pytest.raises(ProviderError):
        run_agent(AGENT, deps)

    assert deps.budget.totals() == {}
    assert tracer.events_of("model_call") == []
    assert len(tracer.events_of("model_error")) == 1


def planted_typo(_usage: object) -> object:
    raise AttributeError("'Usage' object has no attribute 'input_tokenz'")


@pytest.mark.parametrize("call", ["complete", "parse"])
def test_a_bug_in_the_adapter_is_not_reported_as_a_network_fault(
    monkeypatch: pytest.MonkeyPatch, call: str
) -> None:
    monkeypatch.setattr(anthropic_client, "_usage", planted_typo)
    reply = api_reply([{"type": "text", "text": '{"label": "ok"}'}], "end_turn")
    client = AnthropicClient(sdk_over_mock_api(reply, []), RecordingTracer())

    with pytest.raises(AttributeError, match="input_tokenz"):
        client.complete(REQUEST) if call == "complete" else client.parse(REQUEST, Verdict)


def test_a_bad_keyword_from_arde_is_not_reported_as_a_network_fault(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(anthropic_client, "_params", lambda _request: {"no_such_setting": 1})
    client = AnthropicClient(sdk_over_mock_api(FULL_REPLY, []), RecordingTracer())

    with pytest.raises(TypeError, match="no_such_setting"):
        client.complete(REQUEST)


def test_a_broken_stream_logs_where_it_failed_without_any_message() -> None:
    tracer = RecordingTracer()

    def answer(_request: httpx2.Request) -> httpx2.Response:
        return breaks_off_with(httpx2.ReadError("Bearer sk-ant-SECRET"))

    with pytest.raises(ProviderError):
        AnthropicClient(sdk_answering(answer), tracer).complete(REQUEST)

    logged = "\n".join(tracer.debug_lines)
    assert "anthropic_client.py" in logged
    assert "_messages.py" in logged
    assert "SECRET" not in logged


REFUSAL_DETAILS = {
    "type": "refusal",
    "category": "cyber",
    "explanation": "This looks like exploit development.",
}


def test_a_refusal_keeps_its_category_and_explanation_from_the_stream() -> None:
    answer = event_stream(with_finish(stop_reason="refusal", stop_details=REFUSAL_DETAILS))
    client = AnthropicClient(sdk_answering(lambda _: answer), RecordingTracer())

    response = client.complete(REQUEST)

    assert response.refusal == Refusal(
        category="cyber", explanation="This looks like exploit development."
    )


def test_a_refusal_with_no_named_category_keeps_that_too() -> None:
    details = {"type": "refusal", "category": None, "explanation": None}
    client, _ = client_returning(message_with([], stop_reason="refusal", stop_details=details))

    assert client.complete(REQUEST).refusal == Refusal(category=None, explanation=None)


def test_a_reply_that_was_not_refused_has_no_refusal() -> None:
    client, _ = client_returning(message_with())

    assert client.complete(REQUEST).refusal is None


def test_a_refused_structured_call_carries_the_detail_to_whoever_charges_it() -> None:
    reply = message_with([], stop_reason="refusal", stop_details=REFUSAL_DETAILS)
    client, _ = client_returning(reply)

    with pytest.raises(ParseFailed) as raised:
        client.parse(REQUEST, Verdict)

    assert raised.value.paid_call is not None
    assert raised.value.paid_call.refusal is not None
    assert raised.value.paid_call.refusal.category == "cyber"


@pytest.mark.parametrize("stop_reason", ["pause_turn", "something_new"])
def test_a_stop_reason_the_loop_cannot_act_on_is_named(stop_reason: str) -> None:
    answer = event_stream(with_stop_reason(stop_reason))
    client = AnthropicClient(sdk_answering(lambda _: answer), RecordingTracer())

    with pytest.raises(ProviderError, match=stop_reason) as raised:
        client.complete(REQUEST)

    assert "stop reason" in raised.value.next_action


def test_a_broken_stream_names_the_network_or_a_proxy_as_the_next_thing_to_check() -> None:
    client = AnthropicClient(
        sdk_answering(lambda _: breaks_off_with(httpx2.ReadError("reset"))), RecordingTracer()
    )

    with pytest.raises(ProviderError, match="ReadError") as raised:
        client.complete(REQUEST)

    assert "ANTHROPIC_BASE_URL" in raised.value.next_action


@pytest.mark.parametrize("error_type", ["overloaded_error", "api_error"])
def test_an_error_inside_a_stream_is_a_provider_error(error_type: str) -> None:
    body = sse_bytes(FULL_STREAM[:1]) + status_error_event(error_type, "try later")

    def answer(_request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, content=body, headers=STREAM_HEADERS)

    client = AnthropicClient(sdk_answering(answer), RecordingTracer())

    with pytest.raises(ProviderError, match="try later") as raised:
        client.complete(REQUEST)

    assert raised.value.paid_call is not None
    assert raised.value.paid_call.usage == REPORTED_USAGE


# The tokens of a broken stream may already be billed, so a silent retry would pay for them twice.
def test_a_stream_that_breaks_after_it_starts_is_not_retried() -> None:
    attempts: list[httpx2.Request] = []

    def answer(request: httpx2.Request) -> httpx2.Response:
        attempts.append(request)
        return breaks_off_with(httpx2.ReadError("reset"))

    sdk = anthropic.Anthropic(
        api_key="sk-test-not-a-real-key",
        http_client=anthropic.DefaultHttpxClient(transport=httpx2.MockTransport(answer)),
    )

    with pytest.raises(ProviderError):
        AnthropicClient(sdk, RecordingTracer()).complete(REQUEST)

    assert len(attempts) == 1


def test_a_broken_stream_logs_only_its_kind() -> None:
    tracer = RecordingTracer()

    def answer(_request: httpx2.Request) -> httpx2.Response:
        return breaks_off_with(httpx2.ReadError("Bearer sk-ant-SECRET"))

    with pytest.raises(ProviderError) as raised:
        AnthropicClient(sdk_answering(answer), tracer).complete(REQUEST)

    logged = "\n".join(tracer.debug_lines)
    assert "ReadError" in logged
    assert "SECRET" not in logged + str(raised.value)


# What pytest prints for a failing test under --showlocals: every frame's arguments and locals.
def pytest_failure_text(raised: pytest.ExceptionInfo[ProviderError]) -> str:
    return str(raised.getrepr(style="long", funcargs=True, showlocals=True))


def links_of(error: BaseException) -> Iterator[BaseException]:
    seen: set[int] = set()
    link: BaseException | None = error
    while link is not None and id(link) not in seen:
        seen.add(id(link))
        yield link
        link = link.__cause__ or link.__context__


BAD_KEYS = {
    "empty": "",
    "only spaces": "   ",
    "trailing newline": "sk-ant-SECRET-0123\n",
    "leading space": " sk-ant-SECRET-0123",
    "inner space": "sk-ant-SECRET 0123",
    "tab": "sk-ant-SECRET\t0123",
    "non-ascii": "sk-ant-SECRET-\u00e9",
}


# Parametrized by name, because pytest prints the arguments of a failing test and the key is one.
@pytest.mark.parametrize("kind", BAD_KEYS)
def test_a_malformed_key_is_never_printed_not_even_with_showlocals(kind: str) -> None:
    with pytest.raises(ProviderError) as raised:
        AnthropicClient.from_api_key(SecretStr(BAD_KEYS[kind]), RecordingTracer())

    assert raised.value.exit_code is ExitCode.BAD_USAGE
    assert "ANTHROPIC_API_KEY" in raised.value.next_action
    assert "SECRET" not in pytest_failure_text(raised) + raised.value.next_action


class RejectingApi(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        self.rfile.read(int(self.headers.get("content-length", 0)))
        error = {"type": "authentication_error", "message": "invalid x-api-key"}
        body = json.dumps({"type": "error", "error": error}).encode()
        self.send_response(401)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.send_header("request-id", REQUEST_ID)
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        pass


# A real socket on the loopback address, because only a real transport refuses a header value.
# Not an HTTPServer: its bind looks up the host name, which took 35 seconds on a laptop.
@pytest.fixture
def loopback_api() -> Iterator[str]:
    server = ThreadingTCPServer(("127.0.0.1", 0), RejectingApi)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


# Module constants, so the literal is not in the test source that pytest prints with a failure.
LEAK_MARKER = "LEAK-PROBE"
KEY_WITH_NEWLINE = f"sk-ant-{LEAK_MARKER}-0123\n"
WELL_FORMED_MARKER = "WELL-FORMED-PROBE"
WELL_FORMED_KEY = f"sk-ant-{WELL_FORMED_MARKER}-0123"


def sdk_on_loopback(api_key: str, base_url: str) -> anthropic.Anthropic:
    return anthropic.Anthropic(api_key=api_key, base_url=base_url, max_retries=0)


def test_the_loopback_api_answers_a_well_formed_key_with_an_auth_error(loopback_api: str) -> None:
    client = AnthropicClient(sdk_on_loopback(WELL_FORMED_KEY, loopback_api), RecordingTracer())

    with pytest.raises(ProviderError) as raised:
        client.complete(REQUEST)

    assert "rejected the API key" in str(raised.value)


# A server's answer holds the key too, on error.request.headers, so nothing of it stays attached.
def test_a_rejected_key_leaves_no_link_to_the_request(loopback_api: str) -> None:
    client = AnthropicClient(sdk_on_loopback(WELL_FORMED_KEY, loopback_api), RecordingTracer())

    with pytest.raises(ProviderError) as raised:
        client.complete(REQUEST)

    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None
    every_link_rendered = [
        "".join(traceback.format_exception(link)) for link in links_of(raised.value)
    ]
    assert WELL_FORMED_MARKER not in "".join(every_link_rendered)


# Built by hand, to get past from_api_key. The transport refuses the header and says so in a
# message that holds the whole value, so no part of that chain may reach the ProviderError.
def test_a_key_the_transport_refuses_is_in_no_link_of_the_error_chain(
    loopback_api: str,
) -> None:
    tracer = RecordingTracer()
    sdk = sdk_on_loopback(KEY_WITH_NEWLINE, loopback_api)

    with pytest.raises(ProviderError) as raised:
        AnthropicClient(sdk, tracer).complete(REQUEST)

    everything_a_log_could_hold = "".join(
        [pytest_failure_text(raised), raised.value.next_action, *tracer.debug_lines]
    )
    assert LEAK_MARKER not in everything_a_log_could_hold
    assert raised.value.__cause__ is None
    # Not even an implicit link: a handler or a logger that follows __context__ would find it.
    assert raised.value.__context__ is None
    every_link_rendered = [
        "".join(traceback.format_exception(link)) for link in links_of(raised.value)
    ]
    assert LEAK_MARKER not in "".join(every_link_rendered)


def test_a_status_failure_logs_its_kind_too() -> None:
    tracer = RecordingTracer()

    raises_provider_error(
        status_error(anthropic.AuthenticationError, status=401, detail="invalid x-api-key"), tracer
    )

    assert any("AuthenticationError" in line for line in tracer.debug_lines)


def test_only_the_kinds_of_a_connection_failure_are_logged_not_its_messages() -> None:
    tracer = RecordingTracer()
    sdk_error = connection_error()
    sdk_error.__cause__ = RuntimeError("Illegal header value b'Bearer sk-ant-SECRET'")

    error, _ = raises_provider_error(sdk_error, tracer)

    logged = "\n".join(tracer.debug_lines)
    assert "RuntimeError" in logged
    assert "SECRET" not in logged
    assert "SECRET" not in "".join(traceback.format_exception(error))


def test_building_from_an_api_key_needs_no_network() -> None:
    client = AnthropicClient.from_api_key(SecretStr("sk-test-not-a-real-key"), RecordingTracer())

    assert isinstance(client, AnthropicClient)

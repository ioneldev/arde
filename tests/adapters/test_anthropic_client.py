"""The Anthropic adapter, run against a stand-in for the SDK client: no network and no key."""

import json
import threading
import traceback
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler
from socketserver import ThreadingTCPServer

import anthropic
import httpx2
import pytest
from anthropic.types import Message, ParsedMessage
from pydantic import BaseModel, SecretStr
from recording_tracer import RecordingTracer

from arde.adapters.llm.anthropic_client import AnthropicClient
from arde.application.models_config import RoleConfig
from arde.application.ports import LLMRequest, ProviderError, ToolDef
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
)
READ_FILE_PARAMS = {
    "name": "read_file",
    "description": "Read one file.",
    "input_schema": READ_FILE.input_schema,
    "strict": True,
}
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
    """Stands in for client.messages: records each call, then returns the outcome or raises it."""

    def __init__(
        self, outcome: object, *, clock: FakeClock | None = None, call_takes_s: float = 0.0
    ) -> None:
        self._outcome = outcome
        self._clock = clock
        self._call_takes_s = call_takes_s
        self.created: list[dict[str, object]] = []
        self.parsed: list[dict[str, object]] = []

    def create(self, **kwargs: object) -> object:
        self.created.append(kwargs)
        return self._answer()

    def parse(self, **kwargs: object) -> object:
        self.parsed.append(kwargs)
        return self._answer()

    def _answer(self) -> object:
        if self._clock is not None:
            self._clock.now += self._call_takes_s
        if isinstance(self._outcome, Exception):
            raise self._outcome
        return self._outcome


class StubSdk:
    def __init__(self, messages: StubMessages) -> None:
        self.messages = messages


def usage_fields(**overrides: int | None) -> dict[str, int | None]:
    return {"input_tokens": 3, "output_tokens": 4, **overrides}


def message_with(
    content: list[dict[str, object]] | None = None,
    *,
    stop_reason: str = "end_turn",
    usage: dict[str, int | None] | None = None,
) -> Message:
    reply = Message.model_validate(
        {
            "id": "msg_1",
            "type": "message",
            "role": "assistant",
            "model": CONFIG.model,
            "content": content if content is not None else [{"type": "text", "text": "done"}],
            "stop_reason": stop_reason,
            "stop_sequence": None,
            "usage": usage or usage_fields(),
        }
    )
    reply._request_id = REQUEST_ID
    return reply


def parsed_message_with(
    content: list[dict[str, object]], *, stop_reason: str = "end_turn"
) -> ParsedMessage[Verdict]:
    reply = ParsedMessage[Verdict].model_validate(
        {
            "id": "msg_1",
            "type": "message",
            "role": "assistant",
            "model": CONFIG.model,
            "content": content,
            "stop_reason": stop_reason,
            "stop_sequence": None,
            "usage": usage_fields(),
        }
    )
    reply._request_id = REQUEST_ID
    return reply


def verdict_reply(label: str) -> ParsedMessage[Verdict]:
    text = Verdict(label=label).model_dump_json()
    return parsed_message_with([{"type": "text", "text": text, "parsed_output": {"label": label}}])


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

    assert sdk.created == [
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


# Opus 5.5 answers a forced tool_choice with a 400 and has thinking always on, so the adapter
# leaves tool_choice, thinking and sampling to the API defaults.
REJECTED_PARAMS = {"temperature", "top_p", "top_k", "tool_choice", "thinking"}


def test_complete_never_sends_what_the_model_rejects() -> None:
    client, sdk = client_returning(message_with())

    client.complete(REQUEST)

    assert REJECTED_PARAMS.isdisjoint(sdk.created[0])


def test_parse_never_sends_what_the_model_rejects() -> None:
    client, sdk = client_returning(verdict_reply("ok"))

    client.parse(REQUEST, Verdict)

    assert REJECTED_PARAMS.isdisjoint(sdk.parsed[0])


def test_a_strict_tool_sends_strict_true() -> None:
    client, sdk = client_returning(message_with())

    client.complete(REQUEST)

    assert [tool["strict"] for tool in sdk.created[0]["tools"]] == [True]


def test_a_tool_that_is_not_strict_omits_the_field() -> None:
    loose_read_file = READ_FILE.model_copy(update={"strict": False})
    client, sdk = client_returning(message_with())

    client.complete(REQUEST.model_copy(update={"tools": (loose_read_file,)}))

    assert "strict" not in sdk.created[0]["tools"][0]


def test_a_text_message_is_sent_as_it_is() -> None:
    client, sdk = client_returning(message_with())

    client.complete(REQUEST)

    assert sdk.created[0]["messages"] == [{"role": "user", "content": "Find every route."}]


def test_a_request_without_tools_sends_no_tools_field() -> None:
    client, sdk = client_returning(message_with())

    client.complete(REQUEST.model_copy(update={"tools": ()}))

    assert "tools" not in sdk.created[0]


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


def test_the_request_id_reaches_the_debug_log() -> None:
    tracer = RecordingTracer()
    client, _ = client_returning(message_with(), tracer)

    client.complete(REQUEST)

    assert any(REQUEST_ID in line for line in tracer.debug_lines)


def test_provider_error_is_an_arde_error_the_application_can_catch() -> None:
    error = ProviderError("The provider failed", next_action="Run again")

    assert isinstance(error, ArdeError)
    assert error.exit_code == ExitCode.CHECKS_FAILED
    assert error.next_action == "Run again"


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
    assert error.exit_code == ExitCode.CHECKS_FAILED


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
    assert len(messages.created) == 1


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
            "request id",
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


def test_an_error_the_sdk_raises_for_other_reasons_is_wrapped_too() -> None:
    error, _ = raises_provider_error(anthropic.CredentialsError("no credentials"))

    assert "no credentials" in str(error)
    assert error.next_action


def test_the_sdk_error_stays_attached_for_the_debug_log() -> None:
    sdk_error = status_error(anthropic.AuthenticationError, status=401, detail="invalid x-api-key")

    error, _ = raises_provider_error(sdk_error)

    assert error.__cause__ is sdk_error


def test_parse_uses_messages_parse_and_returns_parsed_output() -> None:
    client, sdk = client_returning(verdict_reply("ok"))

    verdict, response = client.parse(REQUEST, Verdict)

    assert verdict == Verdict(label="ok")
    assert response.usage == Usage(input_tokens=3, output_tokens=4, cache_read=0, cache_write=0)
    assert sdk.created == []
    assert sdk.parsed == [
        {
            "model": "claude-opus-5-5",
            "max_tokens": 2000,
            "system": "You explore a service.",
            "messages": [{"role": "user", "content": "Find every route."}],
            "tools": [READ_FILE_PARAMS],
            "output_config": {"effort": "high"},
            "output_format": Verdict,
        }
    ]


def test_parse_without_structured_output_is_a_provider_error() -> None:
    client, _ = client_returning(parsed_message_with([], stop_reason="refusal"))

    with pytest.raises(ProviderError) as raised:
        client.parse(REQUEST, Verdict)

    assert "refusal" in str(raised.value)
    assert raised.value.next_action


def test_parse_errors_from_the_api_become_provider_errors() -> None:
    client, _ = client_returning(
        status_error(anthropic.RateLimitError, status=429, detail="slow down")
    )

    with pytest.raises(ProviderError):
        client.parse(REQUEST, Verdict)


def sdk_over_mock_api(
    reply: dict[str, object], sent: list[dict[str, object]]
) -> anthropic.Anthropic:
    def answer(request: httpx2.Request) -> httpx2.Response:
        sent.append(json.loads(request.content))
        return httpx2.Response(200, json=reply, headers={"request-id": REQUEST_ID})

    transport = httpx2.MockTransport(answer)
    return anthropic.Anthropic(
        api_key="sk-test-not-a-real-key",
        http_client=anthropic.DefaultHttpxClient(transport=transport),
    )


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


def api_reply(content: list[dict[str, object]], stop_reason: str) -> dict[str, object]:
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


def test_a_reply_cut_off_mid_json_is_a_provider_error_naming_max_tokens() -> None:
    reply = api_reply([{"type": "text", "text": '{"label"'}], "max_tokens")
    client = AnthropicClient(sdk_over_mock_api(reply, []), RecordingTracer())

    with pytest.raises(ProviderError) as raised:
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


# max_tokens beyond what the SDK allows without streaming: it raises before it sends anything.
@pytest.mark.parametrize("call", ["complete", "parse"])
def test_a_max_tokens_the_sdk_will_not_send_is_a_provider_error(call: str) -> None:
    sent: list[dict[str, object]] = []
    client = AnthropicClient(sdk_over_mock_api(api_reply([], "end_turn"), sent), RecordingTracer())
    too_many = REQUEST.model_copy(
        update={"config": CONFIG.model_copy(update={"max_tokens": 64_000})}
    )

    with pytest.raises(ProviderError) as raised:
        client.complete(too_many) if call == "complete" else client.parse(too_many, Verdict)

    assert "max_tokens" in raised.value.next_action
    assert "ARDE_MODELS_FILE" in raised.value.next_action
    assert "64000" in str(raised.value)
    assert sent == []


def test_a_reply_that_is_valid_json_but_breaks_the_schema_does_not_blame_max_tokens() -> None:
    reply = api_reply([{"type": "text", "text": '{"label": 5}'}], "end_turn")
    client = AnthropicClient(sdk_over_mock_api(reply, []), RecordingTracer())

    with pytest.raises(ProviderError) as raised:
        client.parse(REQUEST, Verdict)

    assert "max_tokens" not in raised.value.next_action
    assert "Verdict" in str(raised.value)
    assert raised.value.next_action


def test_a_reply_that_is_not_json_covers_both_a_cut_off_and_a_partial_refusal() -> None:
    reply = api_reply([{"type": "text", "text": "I cannot help with"}], "refusal")
    client = AnthropicClient(sdk_over_mock_api(reply, []), RecordingTracer())

    with pytest.raises(ProviderError) as raised:
        client.parse(REQUEST, Verdict)

    assert "max_tokens" in raised.value.next_action
    assert "declined" in raised.value.next_action


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


# The one thing that stays attached is the server's answer, so it must hold no key either.
def test_the_server_answer_kept_as_the_cause_holds_no_key(loopback_api: str) -> None:
    client = AnthropicClient(sdk_on_loopback(WELL_FORMED_KEY, loopback_api), RecordingTracer())

    with pytest.raises(ProviderError) as raised:
        client.complete(REQUEST)

    assert isinstance(raised.value.__cause__, anthropic.AuthenticationError)
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

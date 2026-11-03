"""The Anthropic Messages API behind the LLMClient port. The only module that imports the SDK."""

import re
import time
import traceback
from collections.abc import Callable
from typing import Any, NamedTuple, Self

import anthropic
from anthropic.lib.streaming import MessageStream
from anthropic.types import CacheControlEphemeralParam, Message
from pydantic import BaseModel, SecretStr, ValidationError

from arde.adapters.models_file import MODELS_FILE_VARIABLE
from arde.application.ports import (
    LLMClient,
    LLMRequest,
    LLMResponse,
    ModelStop,
    PaidCall,
    ParseFailed,
    ProviderError,
    Refusal,
    ToolCall,
    ToolDef,
    Tracer,
)
from arde.domain.base import describe_problems
from arde.domain.report import ExitCode
from arde.domain.usage import Usage

_PROMPT_CACHE: CacheControlEphemeralParam = {"type": "ephemeral"}
_RUN_AGAIN = "Run again in a few minutes"
_VISIBLE_ASCII = re.compile(r"[\x21-\x7e]+")
_MAX_CHAIN_LENGTH = 10
_RAISE_MAX_TOKENS = (
    "The reply was cut off. Raise max_tokens for this role in the models file "
    f"({MODELS_FILE_VARIABLE}) and run again"
)
_REPORT_THE_STOP_REASON = "Run again, and if it repeats report the stop reason"
_REPORT_THE_PROBLEM = "Run again, and if it repeats report the problem"
# pause_turn is absent on purpose. Only server tools cause it, ARDE sends none, and the loop has
# no way to resume one, so it is refused here with everything else this code cannot act on.
_MODEL_STOPS: dict[str | None, ModelStop] = {
    "end_turn": "end_turn",
    "tool_use": "tool_use",
    "stop_sequence": "stop_sequence",
    "max_tokens": "max_tokens",
    "refusal": "refusal",
    "model_context_window_exceeded": "model_context_window_exceeded",
}
_ADVICE_FOR_NO_VALUE: dict[ModelStop, str] = {
    "max_tokens": _RAISE_MAX_TOKENS,
    "refusal": "The model declined. Read its reply in debug.log, then remove what it refused",
}


class _Failure(NamedTuple):
    sdk_error: type[anthropic.AnthropicError]
    problem: str
    next_action: str
    # For a status the SDK has no class of its own for: it raises a plain APIStatusError.
    status_code: int | None = None
    exit_code: ExitCode = ExitCode.CHECKS_FAILED


# Ordered most specific first: the first entry the error is an instance of wins, so a timeout
# must come before the connection error it extends, and the base class comes last.
_FAILURES = (
    _Failure(
        sdk_error=anthropic.AuthenticationError,
        problem="Anthropic rejected the API key",
        next_action="Set ANTHROPIC_API_KEY to a valid key, then run again",
        exit_code=ExitCode.BAD_USAGE,
    ),
    _Failure(
        sdk_error=anthropic.PermissionDeniedError,
        problem="This API key is not allowed to make this call",
        next_action=(
            "Use a key that may call the model, or fix the model in the models file "
            f"({MODELS_FILE_VARIABLE})"
        ),
        exit_code=ExitCode.BAD_USAGE,
    ),
    _Failure(
        sdk_error=anthropic.NotFoundError,
        problem="Anthropic does not know this model",
        next_action=(
            f"Fix the model name in the models file ({MODELS_FILE_VARIABLE}), then run again"
        ),
        exit_code=ExitCode.BAD_USAGE,
    ),
    _Failure(
        sdk_error=anthropic.RateLimitError,
        problem="Anthropic is rate limiting this key, and the retries did not clear it",
        next_action="Wait a few minutes, then run again",
    ),
    _Failure(
        sdk_error=anthropic.APIStatusError,
        status_code=402,
        problem="Anthropic reports a billing problem on this account",
        next_action="Fix the billing in the Anthropic Console, then run again",
    ),
    _Failure(
        sdk_error=anthropic.RequestTooLargeError,
        problem="The request is too large for Anthropic",
        next_action=(
            "Report this with the request id: the conversation grew past the API's size limit, "
            "so the same run will fail again"
        ),
    ),
    _Failure(
        sdk_error=anthropic.BadRequestError,
        problem="Anthropic found the request invalid",
        next_action=(
            f"Check the settings for this role in the models file ({MODELS_FILE_VARIABLE}). If "
            "they are right, report this with the request id"
        ),
    ),
    _Failure(
        sdk_error=anthropic.OverloadedError,
        problem="Anthropic is overloaded",
        next_action=_RUN_AGAIN,
    ),
    _Failure(
        sdk_error=anthropic.InternalServerError,
        problem="Anthropic had a server error",
        next_action=_RUN_AGAIN,
    ),
    _Failure(
        sdk_error=anthropic.APITimeoutError,
        problem="The request to Anthropic timed out",
        next_action="Run again, and if it repeats check the network connection",
    ),
    _Failure(
        sdk_error=anthropic.APIConnectionError,
        problem="Cannot reach Anthropic",
        next_action="Check the network connection, then run again",
    ),
    _Failure(
        sdk_error=anthropic.AnthropicError,
        problem="The Anthropic call failed",
        next_action="Run again, and if it repeats report the error with its request id",
    ),
)


class AnthropicClient(LLMClient):
    def __init__(
        self,
        sdk: anthropic.Anthropic,
        tracer: Tracer,
        *,
        clock: Callable[[], float] = time.perf_counter,
    ) -> None:
        self._sdk = sdk
        self._tracer = tracer
        self._clock = clock

    # The transport refuses a header value with a space or a control character in it, and the SDK
    # reports that as a lost connection. A key is checked here instead, where the error can say so.
    # A SecretStr, because a traceback prints the arguments of every frame it passes through, and
    # this frame is one of them when the key is refused. The plain value is never put in a local
    # for the same reason: --showlocals prints those.
    @classmethod
    def from_api_key(cls, api_key: SecretStr, tracer: Tracer) -> Self:
        if _VISIBLE_ASCII.fullmatch(api_key.get_secret_value()) is None:
            raise _malformed_key()
        return cls(anthropic.Anthropic(api_key=api_key.get_secret_value()), tracer)

    # A refusal comes back as stop_reason "refusal" for the agent loop to stop on. No server-side
    # fallback is requested: it needs the beta client, and Haiku 5.5, which the classifier uses,
    # has none.
    def complete(self, request: LLMRequest) -> LLMResponse:
        return self._send(_params(request))

    # Validated here rather than by the SDK's parse(), so the usage still exists when the reply
    # fails: the caller has paid for it either way, and must be able to charge it.
    def parse[T: BaseModel](self, request: LLMRequest, schema: type[T]) -> tuple[T, LLMResponse]:
        response = self._send(_params_for_structured_output(request, schema))
        if response.stop_reason != "end_turn":
            raise _no_value_in_reply(schema, response)
        try:
            return schema.model_validate_json(response.text), response
        except ValidationError as error:
            raise _reply_broke_the_schema(schema, error, response=response) from None

    # Every call streams, because the SDK refuses a large max_tokens on a call that does not.
    #
    # The try holds SDK code and nothing else. The request is built before it, so a bad keyword
    # from ARDE stays a TypeError, and the reply is read after it, so a bug here stays a bug.
    #
    # The failure is raised after the handlers, not inside them. Inside a handler Python links
    # the SDK error as __context__ even for "from None", and a transport error's chain holds the
    # request headers, the API key among them.
    def _send(self, params: dict[str, Any]) -> LLMResponse:
        started = self._clock()
        opening = self._sdk.messages.stream(**params)
        stream: MessageStream[Any] | None = None
        try:
            with opening as stream:
                reply = stream.get_final_message()
                request_id = stream.request_id
        except anthropic.AnthropicError as error:
            self._log_failure(error)
            failure = _provider_error(error)
        # A stream fails in ways the SDK does not wrap, all measured: AssertionError, ValueError,
        # AttributeError, IndexError and raw transport errors. Those are the kinds a bug raises
        # too, so a list of them would be no narrower. The frames in the log tell the two apart.
        except Exception as error:
            self._log_failure(error)
            failure = _unusable_reply(error)
        else:
            return _to_response(reply, latency_s=self._clock() - started, request_id=request_id)
        failure.paid_call = _reported_so_far(stream, latency_s=self._clock() - started)
        raise failure from None

    # Only the kinds and the frames are logged. The error chained under a transport error can
    # quote the request headers, and the API key is one of them, while a frame holds a file, a line
    # number and a line of source, and no value.
    def _log_failure(self, error: Exception) -> None:
        frames = "".join(traceback.format_tb(error.__traceback__))
        self._tracer.debug(f"The Anthropic call failed: {_kinds_in_chain(error)}\n{frames}")


def _params(request: LLMRequest) -> dict[str, Any]:
    params: dict[str, Any] = {
        "model": request.config.model,
        "max_tokens": request.config.max_tokens,
        "system": request.system,
        "messages": list(request.messages),
        "output_config": {"effort": request.config.effort},
        "cache_control": _PROMPT_CACHE,
    }
    if request.tools:
        params["tools"] = [_tool_param(tool) for tool in request.tools]
    return params


def _params_for_structured_output(request: LLMRequest, schema: type[BaseModel]) -> dict[str, Any]:
    params = _params(request)
    output_format = {"type": "json_schema", "schema": anthropic.transform_schema(schema)}
    return {**params, "output_config": {**params["output_config"], "format": output_format}}


def _tool_param(tool: ToolDef) -> dict[str, Any]:
    param: dict[str, Any] = {
        "name": tool.name,
        "description": tool.description,
        "input_schema": tool.input_schema,
    }
    if tool.strict:
        param["strict"] = True
    return param


def _to_response(reply: Message, *, latency_s: float, request_id: str | None) -> LLMResponse:
    paid_call = _paid_call(reply, latency_s=latency_s, request_id=request_id)
    if paid_call is None:
        raise _no_readable_usage()
    stop_reason = _MODEL_STOPS.get(reply.stop_reason)
    if stop_reason is None:
        raise _unusable_stop_reason(reply, paid_call)
    return LLMResponse(
        stop_reason=stop_reason,
        tool_calls=_tool_calls(reply, paid_call),
        assistant_content=tuple(reply.content),
        usage=paid_call.usage,
        latency_s=paid_call.latency_s,
        text=paid_call.text,
        request_id=paid_call.request_id,
        refusal=paid_call.refusal,
    )


# The stream's first event reports the prompt, so a stream that breaks later has already said what
# most of the call cost. The SDK's property asserts that a first event arrived.
def _reported_so_far(stream: MessageStream[Any] | None, *, latency_s: float) -> PaidCall | None:
    if stream is None:
        return None
    try:
        snapshot = stream.current_message_snapshot
    except AssertionError:
        return None
    return _paid_call(snapshot, latency_s=latency_s, request_id=stream.request_id)


# The SDK builds a streamed message without validating it, so a block's text can be None, and that
# must not cost the usage this reads.
def _paid_call(reply: Message, *, latency_s: float, request_id: str | None) -> PaidCall | None:
    usage = _readable_usage(reply)
    if usage is None:
        return None
    return PaidCall(
        usage=usage,
        latency_s=latency_s,
        stop_reason=reply.stop_reason,
        text="\n".join(block.text or "" for block in reply.content if block.type == "text"),
        request_id=request_id,
        refusal=_refusal(reply),
    )


# None for a usage that is absent or breaks the rules of Usage, because neither can be charged.
def _readable_usage(reply: Message) -> Usage | None:
    if reply.usage is None:
        return None
    try:
        return _usage(reply.usage)
    except ValidationError:
        return None


def _refusal(reply: Message) -> Refusal | None:
    details = reply.stop_details
    if details is None:
        return None
    return Refusal(category=details.category, explanation=details.explanation)


def _tool_calls(reply: Message, paid_call: PaidCall) -> tuple[ToolCall, ...]:
    try:
        return tuple(
            ToolCall(id=block.id, name=block.name, input=block.input)
            for block in reply.content
            if block.type == "tool_use"
        )
    except ValidationError as error:
        failure = _unreadable_tool_call(error, paid_call)
    raise failure from None


def _usage(usage: anthropic.types.Usage) -> Usage:
    return Usage(
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        cache_read=usage.cache_read_input_tokens or 0,
        cache_write=usage.cache_creation_input_tokens or 0,
    )


def _provider_error(error: anthropic.AnthropicError) -> ProviderError:
    failure = next(failure for failure in _FAILURES if _applies_to(failure, error))
    return ProviderError(
        f"{failure.problem}: {_detail(error)}",
        next_action=failure.next_action,
        exit_code=failure.exit_code,
    )


def _applies_to(failure: _Failure, error: anthropic.AnthropicError) -> bool:
    if not isinstance(error, failure.sdk_error):
        return False
    return failure.status_code is None or getattr(error, "status_code", None) == failure.status_code


def _detail(error: anthropic.AnthropicError) -> str:
    if not isinstance(error, anthropic.APIStatusError):
        return str(error)
    request_id = error.request_id or "unknown"
    return f"{_api_message(error)} (HTTP {error.status_code}, request id {request_id})"


# The SDK's own text dumps the whole response body, which also repeats the request id.
def _api_message(error: anthropic.APIStatusError) -> str:
    api_error = error.body.get("error") if isinstance(error.body, dict) else None
    message = api_error.get("message") if isinstance(api_error, dict) else None
    return message if isinstance(message, str) else error.message


def _malformed_key() -> ProviderError:
    return ProviderError(
        "The API key is empty, or holds a space, a line break or another character a key "
        "cannot have",
        next_action="Set ANTHROPIC_API_KEY to the key alone, with nothing before or after it",
        exit_code=ExitCode.BAD_USAGE,
    )


def _kinds_in_chain(error: BaseException) -> str:
    kinds: list[str] = []
    link: BaseException | None = error
    while link is not None and len(kinds) < _MAX_CHAIN_LENGTH:
        kinds.append(type(link).__name__)
        link = link.__cause__ or link.__context__
    return " <- ".join(kinds)


# Names the kind alone, because the message of a transport error can quote the request headers.
def _unusable_reply(error: Exception) -> ProviderError:
    return ProviderError(
        f"The reply from Anthropic broke off or could not be read ({type(error).__name__})",
        next_action=(
            "Check the network connection, and any proxy set in ANTHROPIC_BASE_URL, then run again"
        ),
    )


def _no_readable_usage() -> ProviderError:
    return ProviderError(
        "The reply from Anthropic did not say what it used, so it cannot be charged",
        next_action=_REPORT_THE_PROBLEM,
    )


def _unusable_stop_reason(reply: Message, paid_call: PaidCall) -> ProviderError:
    return ProviderError(
        f"The reply ended with a stop reason ARDE cannot act on: {reply.stop_reason}",
        next_action=_REPORT_THE_STOP_REASON,
        paid_call=paid_call,
    )


def _unreadable_tool_call(error: ValidationError, paid_call: PaidCall) -> ProviderError:
    return ProviderError(
        "The reply from Anthropic held a tool call ARDE cannot read: "
        f"{describe_problems(error, whole='the tool call')}",
        next_action=_REPORT_THE_PROBLEM,
        paid_call=paid_call,
    )


def _no_value_in_reply(schema: type[BaseModel], response: LLMResponse) -> ParseFailed:
    return ParseFailed(
        f"The model returned no {schema.__name__} (stop reason: {response.stop_reason})",
        next_action=_ADVICE_FOR_NO_VALUE.get(response.stop_reason, _REPORT_THE_STOP_REASON),
        response=response,
    )


def _reply_broke_the_schema(
    schema: type[BaseModel], error: ValidationError, *, response: LLMResponse
) -> ParseFailed:
    return ParseFailed(
        f"The model's reply broke a rule of {schema.__name__}: "
        f"{describe_problems(error, whole='the reply')}",
        next_action="Run again, and if it repeats report the problem with the rule it broke",
        response=response,
    )

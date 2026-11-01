"""The Anthropic Messages API behind the LLMClient port. The only module that imports the SDK."""

import time
from collections.abc import Callable
from typing import Any, NamedTuple, Self

import anthropic
from anthropic.types import CacheControlEphemeralParam, Message, ParsedMessage
from pydantic import BaseModel, ValidationError

from arde.application.models_config import MODELS_FILE_VARIABLE
from arde.application.ports import (
    LLMClient,
    LLMRequest,
    LLMResponse,
    ProviderError,
    ToolCall,
    ToolDef,
    Tracer,
)
from arde.domain.usage import Usage

_PROMPT_CACHE: CacheControlEphemeralParam = {"type": "ephemeral"}
_RUN_AGAIN = "Run again in a few minutes"
_NO_ERROR_MESSAGE = "The tool reported an error but gave no message."


class _Failure(NamedTuple):
    sdk_error: type[anthropic.AnthropicError]
    problem: str
    next_action: str


# Ordered most specific first: the first entry the error is an instance of wins, so a timeout
# must come before the connection error it extends, and the base class comes last.
_FAILURES = (
    _Failure(
        sdk_error=anthropic.AuthenticationError,
        problem="Anthropic rejected the API key",
        next_action="Set ANTHROPIC_API_KEY to a valid key, then run again",
    ),
    _Failure(
        sdk_error=anthropic.PermissionDeniedError,
        problem="This API key is not allowed to make this call",
        next_action=(
            "Use a key that may call the model, or fix the model in the models file "
            f"({MODELS_FILE_VARIABLE})"
        ),
    ),
    _Failure(
        sdk_error=anthropic.NotFoundError,
        problem="Anthropic does not know this model",
        next_action=(
            f"Fix the model name in the models file ({MODELS_FILE_VARIABLE}), then run again"
        ),
    ),
    _Failure(
        sdk_error=anthropic.RateLimitError,
        problem="Anthropic is rate limiting this key, and the retries did not clear it",
        next_action="Wait a few minutes, then run again",
    ),
    _Failure(
        sdk_error=anthropic.BadRequestError,
        problem="Anthropic found the request invalid",
        next_action="Report this with the request id: ARDE built a request the API refuses",
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

    @classmethod
    def from_api_key(cls, api_key: str, tracer: Tracer) -> Self:
        return cls(anthropic.Anthropic(api_key=api_key), tracer)

    # A refusal comes back as stop_reason "refusal" for the agent loop to stop on. No server-side
    # fallback is requested: it needs the beta client, and Haiku 5.5, which the classifier uses,
    # has none.
    def complete(self, request: LLMRequest) -> LLMResponse:
        def ask() -> Message:
            # Unpacked keyword arguments hide that stream is never sent, so mypy cannot pick the
            # overload that returns a Message. The annotation states what it would have found.
            reply: Message = self._sdk.messages.create(
                **_params(request), cache_control=_PROMPT_CACHE
            )
            return reply

        _, response = self._send(ask)
        return response

    # The SDK's parse() takes no cache_control, so structured calls go out without one.
    def parse[T: BaseModel](self, request: LLMRequest, schema: type[T]) -> tuple[T, LLMResponse]:
        def ask() -> ParsedMessage[T]:
            return self._sdk.messages.parse(**_params(request), output_format=schema)

        try:
            reply, response = self._send(ask)
        except ValidationError as error:
            raise _unparsable_reply(schema, error) from error
        if reply.parsed_output is None:
            raise _missing_structured_output(schema, response)
        return reply.parsed_output, response

    def _send[M: Message](self, ask: Callable[[], M]) -> tuple[M, LLMResponse]:
        started = self._clock()
        try:
            reply = ask()
        except anthropic.AnthropicError as error:
            raise _provider_error(error) from error
        latency_s = self._clock() - started
        self._tracer.debug(f"{reply.model} request id {reply._request_id}")
        return reply, _to_response(reply, latency_s=latency_s)


def _params(request: LLMRequest) -> dict[str, Any]:
    params: dict[str, Any] = {
        "model": request.config.model,
        "max_tokens": request.config.max_tokens,
        "system": request.system,
        "messages": [_with_error_text(message) for message in request.messages],
        "output_config": {"effort": request.config.effort},
    }
    if request.tools:
        params["tools"] = [_tool_param(tool) for tool in request.tools]
    return params


# The API answers an error result with no content with HTTP 400, which would end the run over a
# tool that merely said nothing. The caller's message is copied, not edited.
def _with_error_text(message: dict[str, object]) -> dict[str, object]:
    content = message.get("content")
    if not isinstance(content, list):
        return message
    return {**message, "content": [_error_result_with_text(block) for block in content]}


def _error_result_with_text(block: object) -> object:
    if not isinstance(block, dict):
        return block
    is_empty_error = (
        block.get("type") == "tool_result"
        and block.get("is_error") is True
        and not block.get("content")
    )
    return {**block, "content": _NO_ERROR_MESSAGE} if is_empty_error else block


def _tool_param(tool: ToolDef) -> dict[str, Any]:
    param: dict[str, Any] = {
        "name": tool.name,
        "description": tool.description,
        "input_schema": tool.input_schema,
    }
    if tool.strict:
        param["strict"] = True
    return param


def _to_response(reply: Message, *, latency_s: float) -> LLMResponse:
    tool_calls = tuple(
        ToolCall(id=block.id, name=block.name, input=block.input)
        for block in reply.content
        if block.type == "tool_use"
    )
    text = "\n".join(block.text for block in reply.content if block.type == "text")
    return LLMResponse(
        tool_calls=tool_calls,
        text=text,
        stop_reason=reply.stop_reason or "unknown",
        usage=_usage(reply.usage),
        latency_s=latency_s,
        assistant_content=tuple(reply.content),
    )


def _usage(usage: anthropic.types.Usage) -> Usage:
    return Usage(
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        cache_read=usage.cache_read_input_tokens or 0,
        cache_write=usage.cache_creation_input_tokens or 0,
    )


def _provider_error(error: anthropic.AnthropicError) -> ProviderError:
    failure = next(failure for failure in _FAILURES if isinstance(error, failure.sdk_error))
    return ProviderError(f"{failure.problem}: {_detail(error)}", next_action=failure.next_action)


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


def _unparsable_reply(schema: type[BaseModel], error: ValidationError) -> ProviderError:
    first_problem = error.errors()[0]["msg"]
    return ProviderError(
        f"The model's reply did not parse as {schema.__name__}: {first_problem}",
        next_action=(
            f"Raise max_tokens for this role in the models file ({MODELS_FILE_VARIABLE}), "
            "then run again"
        ),
    )


def _missing_structured_output(schema: type[BaseModel], response: LLMResponse) -> ProviderError:
    return ProviderError(
        f"The model returned no {schema.__name__} (stop reason: {response.stop_reason})",
        next_action="Run again, and if it repeats report the stop reason",
    )

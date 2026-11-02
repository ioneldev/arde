# 0011: Every model request streams

Status: approved 2026-11-02, from the Phase 0 reviews.

**Decision.** The Anthropic adapter sends every request through `messages.stream(...)` and reads the final message. Structured output takes the same path through `output_config.format`, and the adapter validates the reply itself.

**Why.** The SDK refuses a call that does not stream above about 21,333 `max_tokens`, and thinking counts against that limit. Validating after the reply keeps the usage of a paid reply that fails, so `ParseFailed` can carry it to the budget. The same path also sends `cache_control`, which `messages.parse` cannot.

**Cost.** A stream fails in ways the SDK does not wrap: a dropped connection arrives as a raw transport error, a 200 that is no stream as an `AssertionError`, and a malformed event as an `AttributeError`, an `IndexError` or a `ValueError`. Those are the kinds a bug raises too, so a list of them would be no narrower, and `_send` in `src/arde/adapters/llm/anthropic_client.py` keeps a broad `except Exception`. That `try` holds SDK code alone: the request is built before it and the reply is read after it, so a bug in ARDE stays a bug, and the frames of what it does catch go to `debug.log`. The SDK retries before a stream starts but not during one, so a dropped stream ends the run. What the stream had reported is still charged, which covers the prompt, but output tokens arrive in the last event, so those written before the break are not counted.

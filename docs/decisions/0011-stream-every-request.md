# 0011: Every model request streams

Status: approved 2026-11-02, from the Phase 0 reviews.

**Decision.** The Anthropic adapter sends every request through `messages.stream(...)` and reads the final message. Structured output takes the same path through `output_config.format`, and the adapter validates the reply itself.

**Why.** The SDK refuses a call that does not stream above about 21,333 `max_tokens`, and thinking counts against that limit. Validating after the reply keeps the usage of a paid reply that fails, so `ParseFailed` can carry it to the budget. The same path also sends `cache_control`, which `messages.parse` cannot.

**Cost.** A stream fails in ways the SDK does not wrap, so `_send` in `src/arde/adapters/llm/anthropic_client.py` has a broad `except Exception`, around SDK calls only: a dropped connection arrives as a raw transport error, and a 200 that is no stream arrives as an `AssertionError`. Those measured failures are why the catch is broad. The SDK retries before a stream starts but not during one, so a dropped stream ends the run, and its tokens are not counted because the reply never said how many there were.

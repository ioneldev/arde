"""What a model call costs. The budget cap is only as good as this table."""

from arde.domain.base import Frozen
from arde.domain.errors import ArdeError
from arde.domain.report import ExitCode
from arde.domain.usage import Usage

_TOKENS_PER_MILLION = 1_000_000
_CACHE_WRITE_PRICE_FACTOR = 1.25


class Rates(Frozen):
    """US dollars per million tokens."""

    input: float
    output: float
    cache_read: float


class LongPromptRates(Rates):
    above_prompt_tokens: int


class ModelPrice(Rates):
    long_prompt: LongPromptRates | None = None


PRICES: dict[str, ModelPrice] = {
    "claude-opus-5-5": ModelPrice(input=4.00, output=20.00, cache_read=0.20),
    "claude-sonnet-5-5": ModelPrice(input=2.00, output=10.00, cache_read=0.20),
    # A cache read costs 0.1 times the input rate, on each of Haiku's two rate cards.
    "claude-haiku-5-5": ModelPrice(
        input=0.10,
        output=0.50,
        cache_read=0.01,
        long_prompt=LongPromptRates(
            above_prompt_tokens=100_000, input=0.50, output=2.50, cache_read=0.05
        ),
    ),
}


class UnknownModelPrice(ArdeError):
    def __init__(self, model: str) -> None:
        super().__init__(
            f"No price is known for model {model!r}, so its cost cannot be counted",
            next_action=f"Use a model with a known price: {', '.join(PRICES)}",
            exit_code=ExitCode.BAD_USAGE,
        )


# An unknown model raises rather than costing zero, because a zero would let a run pass the cap.
def cost_usd(model: str, usage: Usage) -> float:
    price = PRICES.get(model)
    if price is None:
        raise UnknownModelPrice(model)
    rates = _rates_for(price, usage)
    return (
        _dollars(usage.input_tokens, rates.input)
        + _dollars(usage.output_tokens, rates.output)
        + _dollars(usage.cache_read, rates.cache_read)
        + _dollars(usage.cache_write, rates.input * _CACHE_WRITE_PRICE_FACTOR)
    )


# The API counts a prompt's length with its cached tokens, so input_tokens alone would pick the
# low rate for a long prompt that was mostly served from the cache.
def _rates_for(price: ModelPrice, usage: Usage) -> Rates:
    prompt_tokens = usage.input_tokens + usage.cache_read + usage.cache_write
    long_prompt = price.long_prompt
    if long_prompt is not None and prompt_tokens > long_prompt.above_prompt_tokens:
        return long_prompt
    return price


def _dollars(tokens: int, price_per_million_tokens: float) -> float:
    return tokens * price_per_million_tokens / _TOKENS_PER_MILLION

"""What a model call costs. The budget cap is only as good as this table."""

from arde.domain.base import Frozen
from arde.domain.errors import ArdeError
from arde.domain.report import ExitCode
from arde.domain.usage import Usage

_TOKENS_PER_MILLION = 1_000_000
_CACHE_WRITE_PRICE_FACTOR = 1.25


class ModelPrice(Frozen):
    """US dollars per million tokens."""

    input: float
    output: float
    cache_read: float


PRICES: dict[str, ModelPrice] = {
    "claude-opus-5-5": ModelPrice(input=4.00, output=20.00, cache_read=0.20),
    "claude-sonnet-5-5": ModelPrice(input=2.00, output=10.00, cache_read=0.20),
    # The Haiku cache-read price is an estimate: 0.1 times the input price, not a published rate.
    "claude-haiku-5-5": ModelPrice(input=0.10, output=0.50, cache_read=0.01),
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
    return (
        _dollars(usage.input_tokens, price.input)
        + _dollars(usage.output_tokens, price.output)
        + _dollars(usage.cache_read, price.cache_read)
        + _dollars(usage.cache_write, price.input * _CACHE_WRITE_PRICE_FACTOR)
    )


def _dollars(tokens: int, price_per_million_tokens: float) -> float:
    return tokens * price_per_million_tokens / _TOKENS_PER_MILLION

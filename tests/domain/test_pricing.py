"""The price table behind the budget: a wrong or missing price would let a run overspend."""

import pytest

from arde.domain.errors import ArdeError
from arde.domain.pricing import UnknownModelPrice, cost_usd
from arde.domain.report import ExitCode
from arde.domain.usage import Usage

ONE_MILLION = 1_000_000


def usage_of(
    *, input_tokens: int = 0, output_tokens: int = 0, cache_read: int = 0, cache_write: int = 0
) -> Usage:
    return Usage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read=cache_read,
        cache_write=cache_write,
    )


def test_opus_cost() -> None:
    assert cost_usd("claude-opus-5-5", usage_of(input_tokens=ONE_MILLION)) == 4.0


@pytest.mark.parametrize(
    ("model", "input_price", "output_price", "cache_read_price"),
    [
        ("claude-opus-5-5", 4.0, 20.0, 0.20),
        ("claude-sonnet-5-5", 2.0, 10.0, 0.20),
        ("claude-haiku-5-5", 0.10, 0.50, 0.01),
    ],
)
def test_each_kind_of_token_is_priced_per_million(
    model: str, input_price: float, output_price: float, cache_read_price: float
) -> None:
    assert cost_usd(model, usage_of(input_tokens=ONE_MILLION)) == pytest.approx(input_price)
    assert cost_usd(model, usage_of(output_tokens=ONE_MILLION)) == pytest.approx(output_price)
    assert cost_usd(model, usage_of(cache_read=ONE_MILLION)) == pytest.approx(cache_read_price)


@pytest.mark.parametrize(
    ("model", "input_price"),
    [("claude-opus-5-5", 4.0), ("claude-sonnet-5-5", 2.0), ("claude-haiku-5-5", 0.10)],
)
def test_a_cache_write_costs_one_and_a_quarter_times_the_input_price(
    model: str, input_price: float
) -> None:
    assert cost_usd(model, usage_of(cache_write=ONE_MILLION)) == pytest.approx(input_price * 1.25)


def test_the_cost_of_a_mixed_call_is_the_sum_of_its_parts() -> None:
    mixed = usage_of(input_tokens=2_000, output_tokens=500, cache_read=10_000, cache_write=4_000)

    expected = (2_000 * 4.0 + 500 * 20.0 + 10_000 * 0.20 + 4_000 * 4.0 * 1.25) / ONE_MILLION

    assert cost_usd("claude-opus-5-5", mixed) == pytest.approx(expected)


def test_no_tokens_cost_nothing() -> None:
    assert cost_usd("claude-opus-5-5", usage_of()) == 0.0


def test_an_unknown_model_raises_instead_of_costing_nothing() -> None:
    with pytest.raises(UnknownModelPrice, match="claude-opus-9"):
        cost_usd("claude-opus-9", usage_of(input_tokens=1))


def test_an_unknown_model_error_names_the_next_action() -> None:
    with pytest.raises(ArdeError) as caught:
        cost_usd("claude-opus-9", usage_of(input_tokens=1))

    assert "claude-opus-5-5" in caught.value.next_action
    assert caught.value.exit_code is ExitCode.BAD_USAGE

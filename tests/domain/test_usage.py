"""Token counts can't be negative: a negative count would subtract from the run's spend."""

import pytest
from pydantic import ValidationError

from arde.domain.usage import Usage

TOKEN_FIELDS = ("input_tokens", "output_tokens", "cache_read", "cache_write")


def usage_with(**changes: int) -> Usage:
    counts = {field: 0 for field in TOKEN_FIELDS} | changes
    return Usage(**counts)


def test_zero_tokens_are_accepted() -> None:
    assert usage_with().input_tokens == 0


def test_the_negative_token_count_that_made_a_run_cost_less_than_nothing_is_refused() -> None:
    with pytest.raises(ValidationError, match="input_tokens"):
        usage_with(input_tokens=-10_000_000)


@pytest.mark.parametrize("field", TOKEN_FIELDS)
def test_no_token_count_may_be_negative(field: str) -> None:
    with pytest.raises(ValidationError, match=field):
        usage_with(**{field: -1})

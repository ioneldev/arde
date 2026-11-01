"""The budget is the only thing between a runaway agent and the bill."""

import pytest

from arde.application.budget import Budget, BudgetExceeded
from arde.domain.errors import ArdeError
from arde.domain.pricing import UnknownModelPrice
from arde.domain.report import ExitCode
from arde.domain.usage import ModelCall, Usage

OPUS = "claude-opus-5-5"
HAIKU = "claude-haiku-5-5"
ONE_MILLION = 1_000_000


def call_by(
    role: str,
    *,
    model: str = OPUS,
    input_tokens: int = 0,
    output_tokens: int = 0,
    latency_s: float = 1.0,
) -> ModelCall:
    usage = Usage(
        input_tokens=input_tokens, output_tokens=output_tokens, cache_read=0, cache_write=0
    )
    return ModelCall(role=role, model=model, usage=usage, latency_s=latency_s)


def test_a_charge_under_the_cap_is_accepted() -> None:
    budget = Budget(max_usd=10.0)

    budget.charge(call_by("docs", input_tokens=ONE_MILLION))

    assert budget.totals()["docs"].cost_usd == 4.0


def test_a_charge_that_lands_exactly_on_the_cap_is_accepted() -> None:
    budget = Budget(max_usd=4.0)

    budget.charge(call_by("docs", input_tokens=ONE_MILLION))

    assert budget.totals()["docs"].cost_usd == 4.0


def test_budget_raises_past_cap() -> None:
    budget = Budget(max_usd=4.0)
    budget.charge(call_by("docs", input_tokens=ONE_MILLION))

    with pytest.raises(BudgetExceeded):
        budget.charge(call_by("docs", output_tokens=1))


def test_the_cap_covers_the_whole_run_not_each_role() -> None:
    budget = Budget(max_usd=5.0)
    budget.charge(call_by("discovery", input_tokens=ONE_MILLION))

    with pytest.raises(BudgetExceeded):
        budget.charge(call_by("explore", input_tokens=ONE_MILLION))


def test_the_call_that_crossed_the_cap_is_still_recorded() -> None:
    budget = Budget(max_usd=1.0)

    with pytest.raises(BudgetExceeded):
        budget.charge(call_by("docs", input_tokens=ONE_MILLION))

    assert budget.totals()["docs"].cost_usd == 4.0


def test_budget_exceeded_tells_the_user_what_to_do() -> None:
    budget = Budget(max_usd=1.0)

    with pytest.raises(ArdeError) as caught:
        budget.charge(call_by("docs", input_tokens=ONE_MILLION))

    error = caught.value
    assert isinstance(error, BudgetExceeded)
    assert error.exit_code is ExitCode.BUDGET_EXCEEDED
    assert "max_usd" in error.next_action
    assert "4.0000" in str(error)
    assert "1.0000" in str(error)


def test_budget_totals_per_role() -> None:
    budget = Budget(max_usd=100.0)
    budget.charge(call_by("explore", input_tokens=1_000, output_tokens=200, latency_s=2.0))
    budget.charge(call_by("explore", input_tokens=3_000, output_tokens=400, latency_s=3.5))
    budget.charge(call_by("classifier", model=HAIKU, input_tokens=ONE_MILLION, latency_s=0.5))

    totals = budget.totals()

    assert set(totals) == {"explore", "classifier"}
    assert totals["explore"].calls == 2
    assert totals["explore"].input_tokens == 4_000
    assert totals["explore"].output_tokens == 600
    assert totals["explore"].latency_s == 5.5
    assert totals["explore"].cost_usd == pytest.approx((4_000 * 4.0 + 600 * 20.0) / ONE_MILLION)
    assert totals["classifier"].calls == 1
    assert totals["classifier"].cost_usd == pytest.approx(0.10)


def test_totals_count_cache_tokens() -> None:
    budget = Budget(max_usd=100.0)
    usage = Usage(input_tokens=0, output_tokens=0, cache_read=700, cache_write=300)

    budget.charge(ModelCall(role="docs", model=OPUS, usage=usage, latency_s=1.0))

    assert budget.totals()["docs"].cache_read == 700
    assert budget.totals()["docs"].cache_write == 300


def test_a_budget_with_no_calls_has_no_totals() -> None:
    assert Budget(max_usd=10.0).totals() == {}


def test_changing_the_returned_totals_does_not_change_the_budget() -> None:
    budget = Budget(max_usd=10.0)
    budget.charge(call_by("docs", input_tokens=1_000))

    budget.totals().clear()

    assert "docs" in budget.totals()


def test_a_call_to_an_unpriced_model_is_refused_and_not_recorded() -> None:
    budget = Budget(max_usd=10.0)

    with pytest.raises(UnknownModelPrice):
        budget.charge(call_by("docs", model="claude-opus-9", input_tokens=1))

    assert budget.totals() == {}

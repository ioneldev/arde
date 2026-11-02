"""The cost cap for one run, and the totals per role that the report prints."""

import math

from arde.domain.errors import ArdeError
from arde.domain.pricing import cost_usd
from arde.domain.report import ExitCode
from arde.domain.usage import ModelCall, RoleTotals

_NO_CALLS_YET = RoleTotals(
    calls=0,
    input_tokens=0,
    output_tokens=0,
    cache_read=0,
    cache_write=0,
    cost_usd=0.0,
    latency_s=0.0,
)


class BudgetExceeded(ArdeError):
    # A rerun spends at least as much as this run did, so that is the figure to beat, not the cap.
    def __init__(self, *, spent_usd: float, max_usd: float) -> None:
        super().__init__(
            f"The run spent ${spent_usd:.4f} against a cap of ${max_usd:.4f}",
            next_action=f"Set --max-usd above the ${spent_usd:.4f} spent, then run again",
            exit_code=ExitCode.BUDGET_EXCEEDED,
        )


class InvalidBudgetCap(ArdeError):
    def __init__(self, max_usd: float) -> None:
        super().__init__(
            f"The cost cap must be a positive number of dollars, not {max_usd}",
            next_action="Set --max-usd to a positive number of dollars, for example 10",
            exit_code=ExitCode.BAD_USAGE,
        )


class Budget:
    """Stops a run once it has spent its cap.

    A call's price is known only after it is paid for, so the last call can overshoot the cap by
    its own cost. The cap limits what a run starts, not what its last call costs.
    """

    def __init__(self, max_usd: float) -> None:
        # Every comparison with nan is false, so a nan cap would never trip. An infinite cap
        # never trips either, and a cap of zero or less trips on the first call.
        if not (math.isfinite(max_usd) and max_usd > 0):
            raise InvalidBudgetCap(max_usd)
        self._max_usd = max_usd
        self._totals: dict[str, RoleTotals] = {}

    def refuse_if_spent(self) -> None:
        """Called before each request, because spending exactly the cap does not trip charge."""
        spent_usd = self._spent_usd()
        if spent_usd >= self._max_usd:
            raise BudgetExceeded(spent_usd=spent_usd, max_usd=self._max_usd)

    # The call has already been paid for, so it is recorded before the cap is checked.
    def charge(self, call: ModelCall) -> None:
        cost = cost_usd(call.model, call.usage)
        previous = self._totals.get(call.role, _NO_CALLS_YET)
        self._totals[call.role] = _with_call(previous, call, cost=cost)
        spent_usd = self._spent_usd()
        if spent_usd > self._max_usd:
            raise BudgetExceeded(spent_usd=spent_usd, max_usd=self._max_usd)

    def totals(self) -> dict[str, RoleTotals]:
        return dict(self._totals)

    def _spent_usd(self) -> float:
        return sum(totals.cost_usd for totals in self._totals.values())


def _with_call(totals: RoleTotals, call: ModelCall, *, cost: float) -> RoleTotals:
    return RoleTotals(
        calls=totals.calls + 1,
        input_tokens=totals.input_tokens + call.usage.input_tokens,
        output_tokens=totals.output_tokens + call.usage.output_tokens,
        cache_read=totals.cache_read + call.usage.cache_read,
        cache_write=totals.cache_write + call.usage.cache_write,
        cost_usd=totals.cost_usd + cost,
        latency_s=totals.latency_s + call.latency_s,
    )

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
    def __init__(self, *, spent_usd: float, max_usd: float) -> None:
        super().__init__(
            f"The run spent ${spent_usd:.4f}, past its cap of ${max_usd:.4f}",
            next_action=f"Raise max_usd above ${max_usd:.4f} and run again",
            exit_code=ExitCode.BUDGET_EXCEEDED,
        )


class InvalidBudgetCap(ArdeError):
    def __init__(self, max_usd: float) -> None:
        super().__init__(
            f"The cost cap must be a positive number of dollars, not {max_usd}",
            next_action="Set max_usd to a positive number of dollars, for example 10",
            exit_code=ExitCode.BAD_USAGE,
        )


class Budget:
    def __init__(self, max_usd: float) -> None:
        # Every comparison with nan is false, so a nan cap would never trip. An infinite cap
        # never trips either, and a cap of zero or less trips on the first call.
        if not (math.isfinite(max_usd) and max_usd > 0):
            raise InvalidBudgetCap(max_usd)
        self._max_usd = max_usd
        self._totals: dict[str, RoleTotals] = {}

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

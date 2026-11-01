"""Token usage of model calls and the totals the budget keeps per role."""

from pydantic import NonNegativeInt

from arde.domain.base import Frozen


# A negative count would subtract from the run's spend and push the cost cap further away.
class Usage(Frozen):
    input_tokens: NonNegativeInt
    output_tokens: NonNegativeInt
    cache_read: NonNegativeInt
    cache_write: NonNegativeInt


class ModelCall(Frozen):
    role: str
    model: str
    usage: Usage
    latency_s: float


class RoleTotals(Frozen):
    calls: int
    input_tokens: int
    output_tokens: int
    cache_read: int
    cache_write: int
    cost_usd: float
    latency_s: float

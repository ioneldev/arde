"""Token usage of model calls and the totals the budget keeps per role."""

from arde.domain.base import Frozen


class Usage(Frozen):
    input_tokens: int
    output_tokens: int
    cache_read: int
    cache_write: int


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

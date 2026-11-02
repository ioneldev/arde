"""What a role's model setting is. Reading one from a file is an adapter's job."""

from typing import Annotated, Literal

from pydantic import Field, field_validator

from arde.domain.base import Frozen
from arde.domain.pricing import PRICES

Role = Literal["discovery", "explore", "docs", "classifier", "judge"]
Effort = Literal["low", "medium", "high", "xhigh", "max"]
# Strict, because YAML reads "yes" as true, and true would otherwise pass as the number 1.
Limit = Annotated[int, Field(strict=True, gt=0)]


class RoleConfig(Frozen):
    model: str
    effort: Effort
    max_turns: Limit
    max_tokens: Limit

    # Checked here rather than at the first charge, which would come after the call is paid for.
    @field_validator("model")
    @classmethod
    def _model_has_a_price(cls, model: str) -> str:
        if model not in PRICES:
            raise ValueError(f"no price is known for {model!r}: use one of {', '.join(PRICES)}")
        return model

"""What a failed check reports, and who has to repair it."""

from typing import Literal

from arde.domain.base import Frozen

Owner = Literal["explore", "docs", "code"]


class Finding(Frozen):
    check: str
    owner: Owner
    subject: str
    message: str
    next_action: str

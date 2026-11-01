"""What changed between the last snapshot and the new copy of the repo."""

from typing import Literal

from arde.domain.base import Frozen

ChangeLabel = Literal[
    "comments-only",
    "contract-breaking",
    "contract-additive",
    "behaviour",
    "data",
    "operational",
    "security-compliance",
    "internal",
    "pipeline",
    "testing",
]


class ChangeReport(Frozen):
    changed_files: tuple[str, ...]
    changed_declarations: tuple[str, ...]
    added_inventory: tuple[str, ...]
    removed_inventory: tuple[str, ...]
    changed_routes: tuple[str, ...]
    changed_probes: tuple[str, ...]
    responses_changed: bool
    diff: str

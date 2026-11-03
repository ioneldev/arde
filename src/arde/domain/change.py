"""What changed between the last snapshot and the new copy of the repo."""

from typing import Literal

from arde.domain.base import Frozen
from arde.domain.facts import Wiki

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


def stale_fact_ids(wiki: Wiki, changes: ChangeReport) -> tuple[str, ...]:
    """The facts that link a declaration, route or file that changed, sorted by id."""
    declarations = set(changes.changed_declarations)
    routes = set(changes.changed_routes)
    files = set(changes.changed_files)
    return tuple(
        sorted(
            fact.id
            for fact in wiki.facts
            if declarations.intersection(fact.links.declarations)
            or routes.intersection(fact.links.routes)
            or files.intersection(fact.links.files)
        )
    )

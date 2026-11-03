"""The output copy serves the routes the input served, no more and no fewer."""

from collections import Counter

from arde.application.checks_runner import CheckContext
from arde.application.ports import LanguageAdapter
from arde.domain.base import printable_name
from arde.domain.findings import Finding

ROUTES_UNCHANGED = "routes_unchanged"
_NEXT_ACTION = (
    "This is a bug in ARDE: its edits changed the routes the service serves. Report it with the "
    "route named."
)


class RoutesUnchanged:
    name = ROUTES_UNCHANGED

    def __init__(self, adapter: LanguageAdapter) -> None:
        self._adapter = adapter

    # Counted, because two registrations can share a key, and losing one of them is a change.
    def run(self, ctx: CheckContext) -> list[Finding]:
        before = Counter(route.key for route in ctx.inventory.routes)
        output = self._adapter.extract_inventory(ctx.output_repo, ctx.profile)
        after = Counter(route.key for route in output.routes)
        gained = [printable_name(key) for key in sorted(after - before)]
        lost = [printable_name(key) for key in sorted(before - after)]
        return [
            *(
                self._finding(key, f"The output copy serves {key}, which the input does not.")
                for key in gained
            ),
            *(self._finding(key, f"The output copy no longer serves {key}.") for key in lost),
        ]

    def _finding(self, key: str, message: str) -> Finding:
        return Finding(
            check=self.name, owner="code", subject=key, message=message, next_action=_NEXT_ACTION
        )

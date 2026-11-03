"""The output copy is laid out as the input was.

A file the language's formatter flagged before the run is the repo's own business, so only a file
it flags now and did not flag before is the run's doing.
"""

from arde.application.checks_runner import CheckContext
from arde.application.ports import LanguageAdapter
from arde.domain.base import printable_name
from arde.domain.findings import Finding

FORMATTING_UNCHANGED = "formatting_unchanged"
_NEXT_ACTION = (
    "This is a bug in ARDE: its edits broke the layout of a file the repo kept formatted. Report "
    "it with the file named."
)


class FormattingUnchanged:
    name = FORMATTING_UNCHANGED

    def __init__(self, adapter: LanguageAdapter) -> None:
        self._adapter = adapter

    def run(self, ctx: CheckContext) -> list[Finding]:
        before = self._adapter.format_issues(ctx.input_repo)
        after = self._adapter.format_issues(ctx.output_repo)
        return [self._finding(file) for file in sorted(after - before)]

    def _finding(self, file: str) -> Finding:
        shown = printable_name(file)
        return Finding(
            check=self.name,
            owner="code",
            subject=shown,
            message=f"{shown} is no longer laid out as the language's formatter lays it out.",
            next_action=_NEXT_ACTION,
        )

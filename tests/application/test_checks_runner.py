from typing import cast

from arde.application.checks_runner import CheckContext, run_checks
from arde.domain.findings import Finding

# run_checks hands the context to each check and reads nothing in it.
UNREAD_CONTEXT = cast(CheckContext, object())


class _Finds:
    def __init__(self, name: str, findings: list[Finding]) -> None:
        self.name = name
        self._findings = findings

    def run(self, ctx: CheckContext) -> list[Finding]:
        return self._findings


def _finding(check: str) -> Finding:
    return Finding(check=check, owner="code", subject="x", message="m", next_action="n")


def test_every_check_runs_even_after_one_finds_something() -> None:
    first = _Finds("first", [_finding("first")])
    second = _Finds("second", [_finding("second")])

    assert [f.check for f in run_checks([first, second], UNREAD_CONTEXT)] == ["first", "second"]


def test_no_findings_when_every_check_passes() -> None:
    assert run_checks([_Finds("quiet", [])], UNREAD_CONTEXT) == []

"""The single record of a run. It feeds the printed summary, --json and report.json."""

from enum import IntEnum
from typing import Literal

from arde.domain.base import Frozen
from arde.domain.findings import Finding
from arde.domain.usage import RoleTotals


class ExitCode(IntEnum):
    OK = 0
    CHECKS_FAILED = 1
    BAD_USAGE = 2
    DISCOVERY_NEEDS_INPUT = 3
    BUDGET_EXCEEDED = 4


class RepairRound(Frozen):
    """One pass of sending the checks' findings back to the agents that own them."""

    number: int
    # Everything the checks found before the round, each finding sent to the agent that owns it.
    findings: tuple[Finding, ...]


class RunReport(Frozen):
    status: Literal["success", "no_changes", "failed"]
    exit_code: ExitCode
    message: str
    next_action: str
    findings: tuple[Finding, ...]
    warnings: tuple[str, ...]
    exemptions: tuple[str, ...]
    change_labels: tuple[str, ...]
    cost_usd: float
    duration_s: float
    per_role: dict[str, RoleTotals]
    changed_paths: tuple[str, ...]
    # Empty when the run stopped before it had a folder, as on bad input.
    run_dir: str
    # Where the repo came from, and the commit the run started from.
    source: str = ""
    revision: str = ""
    # Where to read the result: the workspace clone for a URL, the output copy for a local path.
    result_dir: str = ""
    # The branch and commit that hold the result in the workspace clone, for a URL source.
    branch: str | None = None
    commit: str | None = None
    # In order. A round that failed part way is here too, because the findings it started from
    # say what the first attempt got wrong.
    repair_rounds: tuple[RepairRound, ...] = ()

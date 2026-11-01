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
    run_dir: str

"""The printed summary of a run."""

import io

from arde.adapters.console import Console
from arde.domain.findings import Finding
from arde.domain.report import ExitCode, RepairRound, RunReport


def a_report(**changes: object) -> RunReport:
    fields: dict[str, object] = {
        "status": "success",
        "exit_code": ExitCode.OK,
        "message": "Documented the service.",
        "next_action": "Read report.json.",
        "findings": (),
        "warnings": (),
        "exemptions": (),
        "change_labels": (),
        "cost_usd": 0.5,
        "duration_s": 12.0,
        "per_role": {},
        "changed_paths": (),
        "run_dir": "/runs/1",
    }
    return RunReport.model_validate({**fields, **changes})


def a_finding(owner: str, subject: str) -> Finding:
    return Finding.model_validate(
        {
            "check": "a_check",
            "owner": owner,
            "subject": subject,
            "message": "is wrong",
            "next_action": "Fix it.",
        }
    )


def test_the_summary_names_each_repair_round_and_who_it_went_back_to() -> None:
    out = io.StringIO()
    repaired = RepairRound(
        number=1,
        findings=(a_finding("explore", "GET /items"), a_finding("docs", "docs/api.md: x")),
    )

    Console(out=out, err=io.StringIO(), as_json=False).show(a_report(repair_rounds=(repaired,)))

    assert "  repair round 1: 2 findings went back to explore and docs" in out.getvalue()


def test_a_run_with_no_repair_prints_no_round() -> None:
    out = io.StringIO()

    Console(out=out, err=io.StringIO(), as_json=False).show(a_report())

    assert "repair round" not in out.getvalue()

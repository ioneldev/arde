"""The repair loop sends each finding back to the agent that owns it, for a bounded few rounds.

The step is a fake that answers each check from a script and records each repair, so a test sees
every model call the loop could cause: the loop reaches an agent only through VerifyStep.repair.
"""

import json
from collections.abc import Sequence
from dataclasses import dataclass, field

import pytest
from recording_tracer import RecordingTracer

from arde.application.repair import (
    REPAIR_HEADING,
    AgentOwner,
    findings_for_prompt,
    findings_key,
    run_with_repairs,
)
from arde.domain.findings import Finding, Owner


def a_finding(owner: Owner, subject: str = "GET /items", *, message: str = "is wrong") -> Finding:
    return Finding(
        check=f"{owner}_check",
        owner=owner,
        subject=subject,
        message=message,
        next_action="Fix it, then run again.",
    )


EXPLORE = a_finding("explore", "GET /items reply 200")
DOCS = a_finding("docs", "docs/errors.md: http://unknown.example")


@dataclass
class ScriptedStep:
    """Answers each check with the next list of findings, and records each repair asked of it."""

    checks: list[list[Finding]]
    repairs: list[tuple[AgentOwner, list[Finding]]] = field(default_factory=list)
    checked: int = 0

    def check(self) -> list[Finding]:
        findings = self.checks[self.checked]
        self.checked += 1
        return findings

    def repair(self, *, owner: AgentOwner, findings: Sequence[Finding]) -> None:
        self.repairs.append((owner, list(findings)))


def repaired(*checks: list[Finding], max_rounds: int = 2) -> tuple[ScriptedStep, list[Finding]]:
    step = ScriptedStep(checks=list(checks))
    left = run_with_repairs(step, max_rounds=max_rounds, tracer=RecordingTracer())
    return step, left


# Routing


def test_green_first_time_uses_no_repairs() -> None:
    step, left = repaired([])

    assert left == []
    assert step.repairs == []
    assert step.checked == 1


def test_explore_findings_go_to_explore_then_docs() -> None:
    step, left = repaired([EXPLORE], [])

    assert step.repairs == [("explore", [EXPLORE]), ("docs", [EXPLORE])]
    assert left == []


def test_docs_findings_go_to_docs_only() -> None:
    step, left = repaired([DOCS], [])

    assert step.repairs == [("docs", [DOCS])]
    assert left == []


def test_docs_runs_once_a_round_with_the_explore_findings_and_its_own() -> None:
    step, _ = repaired([DOCS, EXPLORE], [])

    assert step.repairs == [("explore", [EXPLORE]), ("docs", [DOCS, EXPLORE])]


@pytest.mark.parametrize("owner", ["code", "discovery"])
def test_code_findings_stop_immediately(owner: Owner) -> None:
    unrepairable = a_finding(owner)

    step, left = repaired([EXPLORE, unrepairable, DOCS])

    assert step.repairs == []
    assert left == [EXPLORE, unrepairable, DOCS]
    assert step.checked == 1


def test_a_code_finding_after_a_round_stops_the_next_one() -> None:
    broken = a_finding("code", "build")

    step, left = repaired([EXPLORE], [broken], max_rounds=2)

    assert [owner for owner, _ in step.repairs] == ["explore", "docs"]
    assert left == [broken]


# Stopping


def test_two_repair_rounds_then_stop() -> None:
    first, second, third = (a_finding("docs", f"docs/overview.md: {n}") for n in "abc")

    step, left = repaired([first], [second], [third], max_rounds=2)

    assert step.repairs == [("docs", [first]), ("docs", [second])]
    assert left == [third]
    assert step.checked == 3


def test_one_round_by_default() -> None:
    step = ScriptedStep(checks=[[DOCS], [EXPLORE]])

    left = run_with_repairs(step, tracer=RecordingTracer())

    assert step.repairs == [("docs", [DOCS])]
    assert left == [EXPLORE]


def test_no_rounds_only_checks() -> None:
    step, left = repaired([DOCS], max_rounds=0)

    assert step.repairs == []
    assert left == [DOCS]


def test_same_findings_twice_stops_early() -> None:
    step, left = repaired([DOCS], [DOCS], [], max_rounds=3)

    assert step.repairs == [("docs", [DOCS])]
    assert left == [DOCS]
    assert step.checked == 2


def test_findings_are_the_same_when_each_check_flags_the_same_subjects() -> None:
    reworded = a_finding("docs", DOCS.subject, message="names a host no probe holds")
    elsewhere = a_finding("docs", "docs/overview.md: http://unknown.example")

    assert findings_key([DOCS, EXPLORE]) == findings_key([EXPLORE, reworded, DOCS])
    assert findings_key([DOCS]) != findings_key([elsewhere])
    assert findings_key([DOCS]) != findings_key([DOCS.model_copy(update={"check": "other"})])


# What the agent is shown


def test_findings_for_prompt_start_with_the_heading_and_keep_every_field_as_ascii() -> None:
    odd = a_finding("docs", f"docs/overview.md: caf{chr(0xE9)}_{chr(0xDCE9)}")

    text = findings_for_prompt([odd, EXPLORE])

    assert text.startswith(REPAIR_HEADING)
    assert text.isascii()
    shown = json.loads(text.split("\n\n", 1)[1])
    assert shown == [odd.model_dump(mode="json"), EXPLORE.model_dump(mode="json")]


# The trace


def test_each_round_traced_as_repair_event() -> None:
    step = ScriptedStep(checks=[[EXPLORE, DOCS], [DOCS], []])
    tracer = RecordingTracer()

    run_with_repairs(step, max_rounds=2, tracer=tracer)

    assert tracer.events_of("repair") == [
        {"round": 1, "owners": ["explore", "docs"], "findings": 2},
        {"round": 2, "owners": ["docs"], "findings": 1},
    ]


def test_a_loop_that_repairs_nothing_traces_no_round() -> None:
    tracer = RecordingTracer()

    run_with_repairs(ScriptedStep(checks=[[a_finding("code")]]), max_rounds=2, tracer=tracer)

    assert tracer.events_of("repair") == []

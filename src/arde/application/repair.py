"""The bounded repair loop: each finding goes back to the agent that owns it, and the checks rerun.

Explore findings go to explore, and then to docs, because the docs are written from the facts.
Docs findings go to docs alone. A finding owned by code or by discovery stops the loop before any
model call, because no agent in this step can repair it. The loop decides from each finding's
owner, check and subject, never from its message.
"""

import json
from collections.abc import Sequence
from typing import Literal, Protocol

from arde.application.ports import Tracer
from arde.domain.findings import Finding

AgentOwner = Literal["explore", "docs"]
# The explore and docs prompts name this heading, so a change here goes there too.
REPAIR_HEADING = "Findings to repair"
# One round, the scope set for the first delivery: each round can rerun explore and every docs
# section, and nothing has yet measured how often a second round fixes what the first did not.
REPAIR_ROUNDS = 1


class VerifyStep(Protocol):
    """What the pipeline gives the loop: its checks, and a way to run an agent again."""

    def check(self) -> list[Finding]: ...

    def repair(self, *, owner: AgentOwner, findings: Sequence[Finding]) -> None: ...


def findings_key(findings: Sequence[Finding]) -> frozenset[tuple[str, str]]:
    """Which check flagged which subject, in any order.

    A message can name the request that showed the problem, which changes when explore probes
    again, so comparing messages would almost never see the same findings come back.
    """
    return frozenset((finding.check, finding.subject) for finding in findings)


# Escaped to ASCII, because a finding quotes a page or a reply, which can hold a lone surrogate
# that the API cannot encode.
def findings_for_prompt(findings: Sequence[Finding]) -> str:
    """The part of an agent's first message that lists what the last attempt's checks found."""
    heading = f"{REPAIR_HEADING}, raised by the checks of the last attempt, as JSON:"
    shown = [finding.model_dump(mode="json") for finding in findings]
    return f"{heading}\n\n{json.dumps(shown, indent=1, ensure_ascii=True)}"


def run_with_repairs(
    step: VerifyStep, *, max_rounds: int = REPAIR_ROUNDS, tracer: Tracer
) -> list[Finding]:
    """Checks, then repairs and checks again up to max_rounds times, and returns what is left.

    The loop stops early when the checks pass, when an agent cannot repair a finding, or when a
    round brings back the findings it started from.
    """
    findings = step.check()
    for round_number in range(1, max_rounds + 1):
        if not findings or not _agents_can_repair(findings):
            return findings
        _repair_round(step, findings=findings, round_number=round_number, tracer=tracer)
        before = findings_key(findings)
        findings = step.check()
        if findings_key(findings) == before:
            return findings
    return findings


def _agents_can_repair(findings: Sequence[Finding]) -> bool:
    return all(finding.owner in ("explore", "docs") for finding in findings)


# Every finding here is explore's or docs', and docs sees them all: its own to rewrite, and
# explore's to describe the facts as they were corrected.
def _repair_round(
    step: VerifyStep, *, findings: Sequence[Finding], round_number: int, tracer: Tracer
) -> None:
    for_explore = [finding for finding in findings if finding.owner == "explore"]
    owners: list[AgentOwner] = ["explore", "docs"] if for_explore else ["docs"]
    tracer.event("repair", round=round_number, owners=owners, findings=len(findings))
    if for_explore:
        step.repair(owner="explore", findings=for_explore)
    step.repair(owner="docs", findings=findings)

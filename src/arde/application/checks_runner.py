"""The checks that run after the agents are done, and what each one reads."""

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from arde.application.ports import RenderResult, ServiceHandle
from arde.domain.facts import Wiki
from arde.domain.findings import Finding
from arde.domain.inventory import Inventory
from arde.domain.profile import RepoProfile


@dataclass(frozen=True)
class ServicePair:
    """The service built from the input copy and from the output copy, both running."""

    before: ServiceHandle
    after: ServiceHandle


# A dataclass, because it holds handles to running services, which are not data to validate.
@dataclass(frozen=True)
class CheckContext:
    # The copy the run started from, never written to.
    input_repo: Path
    # The copy the run wrote its annotations, tags, spec and docs into.
    output_repo: Path
    profile: RepoProfile
    # Read from the input copy, before anything was written.
    inventory: Inventory
    wiki: Wiki
    render: RenderResult
    spec_path: Path
    services: ServicePair


class Check(Protocol):
    name: str

    def run(self, ctx: CheckContext) -> list[Finding]: ...


def run_checks(checks: Sequence[Check], ctx: CheckContext) -> list[Finding]:
    """Runs every check, so one failing check never hides what the others found."""
    return [finding for check in checks for finding in check.run(ctx)]

"""The discovery agent: it reads a repo and submits how to build, run and read it.

It runs on a first run, and again when a saved profile stops fitting. Code checks each profile it
submits by reading the repo with it, so the profile it returns fits the repo as code reads it.
"""

import hashlib
from collections.abc import Mapping
from importlib.resources import files
from pathlib import Path

from pydantic import Field

from arde.application.agent_loop import (
    Accepted,
    AgentSpec,
    AgentStopped,
    Rejected,
    SubmitTool,
    run_agent,
)
from arde.application.pipeline_deps import PipelineDeps
from arde.application.ports import LanguageAdapter, ProfileProblem, ToolDef
from arde.application.profile_store import load_override, merge_override
from arde.application.profile_validation import read_inventory
from arde.application.tool_schema import strict_tool_schema
from arde.domain.base import Frozen
from arde.domain.errors import DiscoveryFailed
from arde.domain.inventory import FileText, Inventory
from arde.domain.profile import DiscoveredProfile, RepoProfile

SUBMIT_TOOL = "submit_profile"
_USER_PROMPT = (
    "Work out how to build, run and read this repository, then submit its profile with "
    "submit_profile."
)
# The most lines of each kind one refusal lists. The rest are counted.
_LISTED_AT_MOST = 50
_LISTING_HEADER = (
    "Not accepted yet. Reading the repository with this profile left what is listed below, which "
    "ARDE lists once. If it shows a mistake in the profile, correct it and submit again. If it is "
    "as the repository is, submit the same profile again and it is accepted."
)
_REQUIRED_FIELDS = tuple(
    name for name, field in DiscoveredProfile.model_fields.items() if field.is_required()
)


class SuspectedInjection(Frozen):
    """A place in the repo whose text tried to direct the agent. Code warns about each one."""

    file: FileText
    line: int = Field(ge=1)


class DiscoverySubmission(DiscoveredProfile):
    """The submit schema: the profile, and the places that tried to direct the agent."""

    suspected_injections: tuple[SuspectedInjection, ...] = ()

    def discovered(self) -> DiscoveredProfile:
        return DiscoveredProfile.model_validate(self.model_dump(exclude={"suspected_injections"}))


class DiscoveryOutcome(Frozen):
    # What profile.yaml holds, for save_profile.
    discovered: DiscoveredProfile
    # With the override applied: the profile the run uses.
    profile: RepoProfile
    suspected_injections: tuple[SuspectedInjection, ...]


_SUBMIT_DEFINITION = ToolDef(
    name=SUBMIT_TOOL,
    description=(
        "Submit the repository's profile. Code checks it against the repository at once: a "
        "refusal comes back as this tool's error and says what to correct. Only an accepted "
        "profile ends your work."
    ),
    # Every key listed as required, so the model says each time whether it saw an injection.
    input_schema=strict_tool_schema(DiscoverySubmission),
    # The API refuses strict mode for a schema with an open mapping, and args is one.
    strict=False,
)


def discover_profile(repo: Path, deps: PipelineDeps) -> DiscoveryOutcome:
    """Runs the agent on the repo until it submits a profile that fits, and returns that profile.

    The override is read first, so a bad one stops the run before any model is paid for. Raises
    DiscoveryFailed when the agent stops without such a profile.
    """
    override = load_override(deps.store)
    spec = AgentSpec(
        role="discovery",
        config=deps.models["discovery"],
        system=_system_prompt(deps.adapter),
        user=_USER_PROMPT,
        tools=deps.toolbox.repo_tools(repo),
        submit=SubmitTool(
            definition=_SUBMIT_DEFINITION,
            validate=_ProfileCheck(repo=repo, override=override, adapter=deps.adapter),
        ),
    )
    deps.agents.tracer.event("prompt", role="discovery", sha256=_sha256(spec.system))
    try:
        submission = run_agent(spec, deps.agents)
    except AgentStopped as stopped:
        raise _discovery_failed(stopped, override_path=deps.store.override_path) from stopped
    discovered = submission.discovered()
    return DiscoveryOutcome(
        discovered=discovered,
        profile=merge_override(discovered, override),
        suspected_injections=submission.suspected_injections,
    )


# The same text on every run and for every repo, so the trace can compare runs by its hash.
def _system_prompt(adapter: LanguageAdapter) -> str:
    instructions = files("arde.application").joinpath("prompts", "discovery.md")
    text = instructions.read_text(encoding="utf-8")
    return f"{text.rstrip()}\n\n{adapter.discovery_guidance().strip()}\n"


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class _ProfileCheck:
    """Reads the repo with each profile the agent submits, and remembers what it has listed."""

    def __init__(
        self, *, repo: Path, override: Mapping[str, object], adapter: LanguageAdapter
    ) -> None:
        self._repo = repo
        self._override = override
        self._adapter = adapter
        self._listed: frozenset[str] = frozenset()

    # A profile the schema refuses raises here, and the loop sends that back as the tool's error.
    # The repo is read with the override applied, because that is the profile the run would use.
    def __call__(self, tool_input: dict[str, object]) -> Accepted[DiscoverySubmission] | Rejected:
        submission = DiscoverySubmission.model_validate(tool_input)
        profile = merge_override(submission.discovered(), self._override)
        try:
            inventory = read_inventory(self._repo, profile, adapter=self._adapter)
        except ProfileProblem as problem:
            return Rejected(f"{problem} {problem.next_action}")
        leftovers = _leftovers_of(inventory)
        if not leftovers <= self._listed:
            self._listed |= leftovers
            return Rejected(_listing(inventory))
        return Accepted(submission)


def _leftovers_of(inventory: Inventory) -> frozenset[str]:
    return frozenset(
        [
            *(f"dropped {line}" for line in inventory.dropped),
            *(f"unmatched {line}" for line in inventory.unmatched_patterns),
        ]
    )


def _listing(inventory: Inventory) -> str:
    return "\n".join(
        [
            _LISTING_HEADER,
            *_section("Found but gave nothing", inventory.dropped),
            *_section("Patterns that matched nothing", inventory.unmatched_patterns),
        ]
    )


def _section(title: str, lines: tuple[str, ...]) -> list[str]:
    if not lines:
        return []
    rest = len(lines) - _LISTED_AT_MOST
    return [
        f"{title} ({len(lines)}):",
        *(f"- {line}" for line in lines[:_LISTED_AT_MOST]),
        *([f"- and {rest} more"] if rest > 0 else []),
    ]


def _discovery_failed(stopped: AgentStopped, *, override_path: Path) -> DiscoveryFailed:
    missing = _missing(stopped.last_rejection)
    return DiscoveryFailed(
        f"Discovery stopped after {stopped.turns} turns ({stopped.stop}) without a profile that "
        f"fits this repo. What it could not settle: {'; '.join(missing)}",
        missing=missing,
        next_action=(
            f"Set what is missing by hand in {override_path}, which Discovery's check applies "
            f"too, then run again. Otherwise: {stopped.next_action}"
        ),
    )


# The last refusal says what the last profile lacked, a reason a line. With no refusal, the agent
# never submitted a profile, so it lacks every value it had to find.
def _missing(last_rejection: str | None) -> tuple[str, ...]:
    if last_rejection is None:
        return _REQUIRED_FIELDS
    return tuple(line for line in last_rejection.splitlines() if line.strip())

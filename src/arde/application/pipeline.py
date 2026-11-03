"""A run: one fixed sequence of steps, from a resolved source to a commit in the workspace clone.

Each step is a small function, so _document reads as the flow. Agents work only inside the steps
that need them. A red baseline stops the run before any paid call. A finding goes back to the
agent that owns it for a round of repair, and a finding left after the last round stops the run
before the commit. The profile, wiki, manifest, sections and snapshot are saved only once every
check has passed, so a failed run leaves the last good run's state as it was.
"""

import hashlib
import re
import shutil
import time
from collections.abc import Iterable, Iterator, Sequence
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from arde.application.agents import discovery, docs_writer
from arde.application.agents import explore as explore_agent
from arde.application.agents.discovery import discover_profile
from arde.application.agents.docs_writer import (
    DocsRequest,
    DocsResult,
    ReadmeLink,
    add_readme_link,
    write_docs,
)
from arde.application.agents.explore import ExploreRequest, ExploreResult, explore
from arde.application.changes import (
    NO_CHANGES,
    RunChanges,
    SnapshotComparison,
    changed_fact_ids,
    changes_since_snapshot,
    paths_that_differ,
    restore_last_pages,
)
from arde.application.checks_runner import Check, CheckContext, ServicePair, run_checks
from arde.application.manifest_store import load_manifest, save_manifest
from arde.application.models_config import Role
from arde.application.pipeline_deps import PipelineDeps
from arde.application.ports import (
    CheckResult,
    LanguageAdapter,
    RenderManifest,
    RenderOutcome,
    RenderRequest,
    ResolvedSource,
    ResultCommit,
    RouteHashes,
    RunStore,
    ServiceConfig,
    ServiceHandle,
    SourceControl,
    Tracer,
)
from arde.application.profile_store import (
    load_override,
    load_profile,
    merge_override,
    save_profile,
)
from arde.application.profile_validation import (
    ProfileCandidate,
    read_inventory,
    refuse_an_oversized,
    validate_profile,
)
from arde.application.repair import AgentOwner, run_with_repairs
from arde.application.run_warnings import baseline_warnings, inventory_warnings
from arde.application.section_store import save_sections
from arde.application.wiki_store import load_wiki, save_wiki
from arde.checks.behaviour import Behaviour
from arde.checks.docs_grounding import DocsGrounding
from arde.checks.formatting import FormattingUnchanged
from arde.checks.live_contract import LiveContract
from arde.checks.routes import RoutesUnchanged
from arde.checks.spec_inventory import SpecMatchesInventory
from arde.checks.spec_valid import SpecValid
from arde.domain.base import Frozen, one_line, printable_name, visible_text
from arde.domain.errors import ArdeError
from arde.domain.facts import Wiki
from arde.domain.findings import Finding
from arde.domain.inventory import Inventory
from arde.domain.profile import DiscoveredProfile, RepoProfile
from arde.domain.report import ExitCode, RepairRound, RunReport

# The branch a URL run commits its result on, followed by the run's id.
BRANCH_PREFIX = "arde/run-"
AGENT_ROLES: tuple[Role, ...] = ("discovery", "explore", "docs")
# How much of a message or a warning the report keeps. The trace and debug.log keep the rest.
MESSAGE_LIMIT = 8_000
WARNING_LIMIT = 2_000
# How much of what the repo's own checks printed a red baseline's message shows.
CHECKS_OUTPUT_SHOWN = 2_000


@dataclass(frozen=True)
class RunDeps:
    """What a whole run needs: what every step shares, and the copy and commit of the source."""

    shared: PipelineDeps
    source_control: SourceControl


class RunRequest(Frozen):
    source: ResolvedSource
    # A new, empty folder from the store, for the copies, the service logs and report.json.
    run_dir: Path


# The errors a run raises itself. Each names what to do next.


class NotAProjectTheAdapterReads(ArdeError):
    def __init__(self, origin: str) -> None:
        super().__init__(
            f"{printable_name(origin)} holds no project that ARDE's language adapter reads: it "
            "finds no module file at the repo's top folder.",
            next_action=(
                "Point --repo at the top folder of the service's module, the folder that holds "
                "its module file, then run again."
            ),
            exit_code=ExitCode.BAD_USAGE,
        )


class BaselineRed(ArdeError):
    def __init__(self, result: CheckResult) -> None:
        failed = [
            what
            for what, passed in (
                ("it does not build", result.build_ok),
                ("vet fails", result.vet_ok),
                ("its own tests fail", result.test_ok),
            )
            if not passed
        ]
        shown = visible_text(result.output.strip())[-CHECKS_OUTPUT_SHOWN:]
        super().__init__(
            f"The repo fails its own checks before ARDE changes anything: {', '.join(failed)}. "
            f"The end of what they printed:\n{shown}",
            next_action=(
                "Make the repo's build, vet and tests pass, then run again. ARDE documents a repo "
                "whose own checks pass, so that they can show its edits changed nothing."
            ),
            exit_code=ExitCode.CHECKS_FAILED,
        )


class NoRoutesFound(ArdeError):
    def __init__(self, store: RunStore) -> None:
        super().__init__(
            "The profile reads no route in the repo, so there is nothing to document.",
            next_action=(
                f"Set a route pattern that matches how the repo registers its routes in "
                f"{store.override_path}, then run again."
            ),
            exit_code=ExitCode.DISCOVERY_NEEDS_INPUT,
        )


class ChecksFailed(ArdeError):
    def __init__(self, findings: Sequence[Finding]) -> None:
        super().__init__(
            f"The output failed ARDE's checks with {len(findings)} findings, so nothing was "
            "committed and the last good run's state is kept.",
            next_action=(
                "Read each finding and its next action in report.json, fix what it names, then "
                "run again."
            ),
            exit_code=ExitCode.CHECKS_FAILED,
        )
        self.findings = tuple(findings)


class CopyFailed(ArdeError):
    def __init__(self, what: str, error: OSError) -> None:
        super().__init__(
            f"Cannot copy {what}: {printable_name(str(error))}",
            next_action="Check that you can write under ARDE_HOME, then run again.",
            exit_code=ExitCode.BAD_USAGE,
        )


# What one run carries from step to step.


@dataclass
class _Run:
    request: RunRequest
    deps: RunDeps
    started_s: float = field(default_factory=time.monotonic)
    warnings: list[str] = field(default_factory=list)
    repair_rounds: list[RepairRound] = field(default_factory=list)

    @property
    def shared(self) -> PipelineDeps:
        return self.deps.shared

    @property
    def tracer(self) -> Tracer:
        return self.deps.shared.agents.tracer

    @property
    def store(self) -> RunStore:
        return self.deps.shared.store

    @property
    def adapter(self) -> LanguageAdapter:
        return self.deps.shared.adapter

    @property
    def run_dir(self) -> Path:
        return self.request.run_dir

    def warn(self, lines: Iterable[str]) -> None:
        """Keeps each line for the report and traces it."""
        for shown in self._keep_new(lines):
            self.tracer.event("warning", role="pipeline", summary=shown)

    def keep(self, lines: Iterable[str]) -> None:
        """Keeps each line for the report. The agent that found it has traced it already."""
        self._keep_new(lines)

    # A repair runs a step again, which notes again much of what it noted the first time.
    def _keep_new(self, lines: Iterable[str]) -> list[str]:
        """Keeps each line the report does not hold yet, and returns those."""
        kept: list[str] = []
        for line in lines:
            shown = _shown(line, WARNING_LIMIT)
            if shown not in self.warnings:
                self.warnings.append(shown)
                kept.append(shown)
        return kept

    @contextmanager
    def step(self, name: str) -> Iterator[None]:
        self.tracer.event("step_start", step=name)
        started = time.monotonic()
        yield
        self.tracer.event("step_end", step=name, duration_s=round(time.monotonic() - started, 3))


@dataclass(frozen=True)
class _ChosenProfile:
    profile: RepoProfile
    # What Discovery found in this run, to save once the run succeeds. None when the saved one fits.
    discovered: DiscoveredProfile | None


@dataclass(frozen=True)
class _Reading:
    inventory: Inventory
    hashes: RouteHashes


@dataclass(frozen=True)
class _LastRun:
    wiki: Wiki
    manifest: RenderManifest


@dataclass(frozen=True)
class _Docs:
    result: DocsResult
    readme: ReadmeLink


@dataclass(frozen=True)
class _Basis:
    """What the run read before any agent wrote, which no repair changes."""

    input_repo: Path
    profile: RepoProfile
    reading: _Reading
    last: _LastRun


@dataclass(frozen=True)
class _Output:
    """An output copy written from one wiki, and what the run wrote into it."""

    repo: Path
    wiki: Wiki
    rendered: RenderOutcome
    spec_path: Path
    docs: _Docs
    changed_paths: tuple[str, ...]


@dataclass(frozen=True)
class _Commit:
    branch: str
    commit: str


@dataclass(frozen=True)
class _Documented:
    output_repo: Path
    changed_paths: tuple[str, ...]
    commit: _Commit | None


def run_pipeline(request: RunRequest, deps: RunDeps) -> RunReport:
    """Documents the source, and reports how it went. Only an interrupt goes on up."""
    run = _Run(request=request, deps=deps)
    try:
        _trace_run_start(run)
        documented = _document(run)
    except ArdeError as error:
        return _finished(run, _failure_report(error, run))
    except KeyboardInterrupt:
        _trace_run_end(run, status="interrupted", exit_code=None)
        raise
    return _finished(run, _success_report(documented, run))


def _document(run: _Run) -> _Documented:
    input_repo = _copy_input(run)
    _require_a_green_baseline(input_repo, run)
    chosen = _choose_profile(input_repo, run)
    profile = chosen.profile
    reading = _read_inventory(input_repo, profile=profile, run=run)
    basis = _Basis(input_repo=input_repo, profile=profile, reading=reading, last=_last_run(run))
    changes = _changes_since_last_run(basis, run)
    explored = _explore(basis, wiki=basis.last.wiki, changes=changes, run=run)
    written = _write_output(basis, wiki=explored.wiki, run=run)
    output = _verify(basis, output=written, run=run)
    commit = _commit(output.repo, changed_paths=output.changed_paths, run=run)
    _save(chosen, output=output, run=run)
    return _Documented(output_repo=output.repo, changed_paths=output.changed_paths, commit=commit)


# The steps


def _copy_input(run: _Run) -> Path:
    with run.step("copy_input"):
        input_repo = run.store.input_copy(run.run_dir)
        run.deps.source_control.copy_source(run.request.source, input_repo)
        return input_repo


def _require_a_green_baseline(input_repo: Path, run: _Run) -> None:
    with run.step("baseline"):
        if not run.adapter.detects(input_repo):
            raise NotAProjectTheAdapterReads(run.request.source.origin)
        result = run.adapter.run_checks(input_repo, _baseline_profile(run.store))
        run.tracer.debug(f"The repo's own checks, before any edit:\n{result.output}")
        if not (result.build_ok and result.vet_ok and result.test_ok):
            raise BaselineRed(result)
        run.warn(baseline_warnings(result))


# The repo's own checks read only what the owner may add to a profile, its extra test commands,
# so the baseline needs no discovered value and runs before any paid call. The override goes on a
# stand-in for the rest.
_STAND_IN = DiscoveredProfile(main_package=".", main_file="main", port_env="PORT", patterns=())


def _baseline_profile(store: RunStore) -> RepoProfile:
    return merge_override(_STAND_IN, load_override(store))


def _choose_profile(input_repo: Path, run: _Run) -> _ChosenProfile:
    with run.step("profile"):
        saved = load_profile(run.store)
        if saved is not None:
            problems = validate_profile(
                ProfileCandidate(
                    repo=input_repo, profile=saved, service_log=run.run_dir / "service-profile.log"
                ),
                run.shared,
            )
            if not problems:
                return _ChosenProfile(profile=saved, discovered=None)
            run.warn(
                [
                    "The saved profile no longer fits the repo, so Discovery ran again: "
                    + " ".join(problem.message for problem in problems)
                ]
            )
        outcome = discover_profile(input_repo, run.shared)
        run.warn(
            f"Discovery suspects {printable_name(injection.file)}:{injection.line} of holding "
            "instructions for agents. It reported them, and code followed none."
            for injection in outcome.suspected_injections
        )
        return _ChosenProfile(profile=outcome.profile, discovered=outcome.discovered)


def _read_inventory(input_repo: Path, *, profile: RepoProfile, run: _Run) -> _Reading:
    with run.step("inventory"):
        inventory = read_inventory(input_repo, profile, adapter=run.adapter)
        refuse_an_oversized(inventory)
        if not inventory.routes:
            raise NoRoutesFound(run.store)
        hashes = run.adapter.route_fingerprints(
            input_repo, inventory.routes, main_package=profile.main_package
        )
        run.warn(inventory_warnings(inventory, hashes))
        return _Reading(inventory=inventory, hashes=hashes)


def _last_run(run: _Run) -> _LastRun:
    return _LastRun(wiki=load_wiki(run.store), manifest=load_manifest(run.store))


def _changes_since_last_run(basis: _Basis, run: _Run) -> RunChanges:
    with run.step("changes"):
        if not (run.store.has_snapshot() and basis.last.wiki.facts):
            return NO_CHANGES
        comparison = SnapshotComparison(
            snapshot=run.store.snapshot_dir,
            repo=basis.input_repo,
            profile=basis.profile,
            inventory=basis.reading.inventory,
            hashes=basis.reading.hashes,
            wiki=basis.last.wiki,
            manifest=basis.last.manifest,
        )
        changes = changes_since_snapshot(comparison, run.adapter)
        assert changes.report is not None
        run.tracer.event(
            "changes",
            stale_ids=list(changes.stale_ids),
            changed_files=list(changes.report.changed_files),
            changed_declarations=list(changes.report.changed_declarations),
            changed_routes=list(changes.report.changed_routes),
        )
        return changes


def _explore(
    basis: _Basis,
    *,
    wiki: Wiki,
    changes: RunChanges,
    run: _Run,
    repair_findings: Sequence[Finding] = (),
    attempt: int = 0,
) -> ExploreResult:
    with run.step("explore"):
        log = _service_log(run, "explore", attempt=attempt)
        with _running_service(basis.input_repo, profile=basis.profile, log=log, run=run) as service:
            result = explore(
                ExploreRequest(
                    repo=basis.input_repo,
                    profile=basis.profile,
                    inventory=basis.reading.inventory,
                    wiki=wiki,
                    service=service,
                    stale_ids=changes.stale_ids,
                    change_report=changes.report,
                    repair_findings=tuple(repair_findings),
                ),
                run.shared,
            )
    run.keep(result.warnings)
    run.keep(f"suspected prompt injection at {note}" for note in result.suspected_injections)
    run.warn(
        f"Explore suspects the inventory misses {printable_name(gap)}"
        for gap in result.suspected_gaps
    )
    return result


def _write_output(
    basis: _Basis,
    *,
    wiki: Wiki,
    run: _Run,
    repair_findings: Sequence[Finding] = (),
    repaired_fact_ids: frozenset[str] = frozenset(),
) -> _Output:
    """A new output copy of the input, with the annotations, spec and docs written from the wiki."""
    output_repo = _copy_output(basis.input_repo, run)
    rendered = _render(output_repo, basis=basis, wiki=wiki, run=run)
    spec_path = _generate_spec(output_repo, profile=basis.profile, run=run)
    docs = _write_docs(
        DocsRequest(
            repo=output_repo,
            wiki=wiki,
            inventory=basis.reading.inventory,
            changed_fact_ids=changed_fact_ids(basis.last.wiki, wiki) | repaired_fact_ids,
            repair_findings=tuple(repair_findings),
        ),
        run,
    )
    return _Output(
        repo=output_repo,
        wiki=wiki,
        rendered=rendered,
        spec_path=spec_path,
        docs=docs,
        # Measured before the checks start a service in the copy, which may write files there.
        changed_paths=paths_that_differ(basis.input_repo, output_repo),
    )


def _copy_output(input_repo: Path, run: _Run) -> Path:
    with run.step("copy_output"):
        output_repo = run.store.output_copy(run.run_dir)
        _remove_the_last_attempt(output_repo)
        _copy_tree(input_repo, output_repo, what="the input copy to the output copy")
        return output_repo


# A repair writes the output again on a new copy of the input. The render puts each annotation at
# a line the inventory read from the input, which an annotated copy has moved, and a file the last
# checks' service or tests wrote into the copy must not be committed.
def _remove_the_last_attempt(output_repo: Path) -> None:
    if not output_repo.exists():
        return
    try:
        shutil.rmtree(output_repo)
    except OSError as error:
        raise CopyFailed("the input copy over the last attempt's output copy", error) from error


def _render(output_repo: Path, *, basis: _Basis, wiki: Wiki, run: _Run) -> RenderOutcome:
    with run.step("render"):
        request = RenderRequest(
            repo=output_repo,
            wiki=wiki,
            inventory=basis.reading.inventory,
            manifest=basis.last.manifest,
            main_file=basis.profile.main_file,
            title=api_title(run.request.source),
        )
        return run.adapter.render_annotations(request)


def _generate_spec(output_repo: Path, *, profile: RepoProfile, run: _Run) -> Path:
    with run.step("spec"):
        return run.adapter.generate_spec(output_repo, profile)


def _write_docs(request: DocsRequest, run: _Run) -> _Docs:
    with run.step("docs"):
        if run.store.has_snapshot():
            _restore_last_pages(request.repo, run)
        docs = _Docs(result=write_docs(request, run.shared), readme=add_readme_link(request.repo))
    run.keep(docs.result.warnings)
    run.warn(docs.readme.warnings)
    return docs


def _restore_last_pages(output_repo: Path, run: _Run) -> None:
    try:
        restore_last_pages(run.store.snapshot_dir, output_repo)
    except OSError as error:
        raise CopyFailed("the last run's pages into the output copy", error) from error


def _verify(basis: _Basis, *, output: _Output, run: _Run) -> _Output:
    """The output once every check passes on it, after the rounds of repair the loop allows."""
    verification = _Verification(basis=basis, run=run, wiki=output.wiki, output=output)
    left = run_with_repairs(verification, tracer=run.tracer)
    if left:
        raise ChecksFailed(left)
    return verification.output


@dataclass
class _Verification:
    """The checks, and each agent run again on what they found. It is the loop's VerifyStep.

    Round n follows the n-th check: its repairs and the check after it are attempt n.
    """

    basis: _Basis
    run: _Run
    # The latest wiki, which an explore repair replaces before the docs repair writes from it.
    wiki: Wiki
    output: _Output
    # The ids of the facts explore changed in a repair, whose sections docs writes again.
    repaired_fact_ids: frozenset[str] = frozenset()
    # What the last check found, which the round after it repairs.
    found: list[Finding] = field(default_factory=list)
    checks_run: int = 0

    def check(self) -> list[Finding]:
        self.found = _check(self.basis, output=self.output, attempt=self.checks_run, run=self.run)
        self.checks_run += 1
        return self.found

    def repair(self, *, owner: AgentOwner, findings: Sequence[Finding]) -> None:
        self._note_the_round()
        if owner == "explore":
            self._explore_again(findings)
        else:
            self._write_again(findings)

    # A round starts with its first repair, and repairs everything the check before it found.
    def _note_the_round(self) -> None:
        if len(self.run.repair_rounds) < self.checks_run:
            self.run.repair_rounds.append(
                RepairRound(number=self.checks_run, findings=tuple(self.found))
            )

    # The wiki is this run's, checked against the code as it is now, so none of its facts is stale.
    def _explore_again(self, findings: Sequence[Finding]) -> None:
        explored = _explore(
            self.basis,
            wiki=self.wiki,
            changes=NO_CHANGES,
            run=self.run,
            repair_findings=findings,
            attempt=self.checks_run,
        )
        self.repaired_fact_ids |= changed_fact_ids(self.wiki, explored.wiki)
        self.wiki = explored.wiki

    # The docs repair ends every round, after explore's when there is one, so it writes the whole
    # output again: the annotations and spec from the latest wiki, and the pages with the findings.
    def _write_again(self, findings: Sequence[Finding]) -> None:
        self.output = _write_output(
            self.basis,
            wiki=self.wiki,
            run=self.run,
            repair_findings=findings,
            repaired_fact_ids=self.repaired_fact_ids,
        )


# Both services start afresh for each attempt and stop when its checks end, so the two that the
# behaviour check compares have answered the same requests, and none runs during a repair.
def _check(basis: _Basis, *, output: _Output, attempt: int, run: _Run) -> list[Finding]:
    with run.step("checks"), ExitStack() as services:
        before = services.enter_context(
            _running_service(
                basis.input_repo,
                profile=basis.profile,
                log=_service_log(run, "before", attempt=attempt),
                run=run,
            )
        )
        after = services.enter_context(
            _running_service(
                output.repo,
                profile=basis.profile,
                log=_service_log(run, "after", attempt=attempt),
                run=run,
            )
        )
        context = CheckContext(
            input_repo=basis.input_repo,
            output_repo=output.repo,
            profile=basis.profile,
            inventory=basis.reading.inventory,
            wiki=output.wiki,
            render=output.rendered.result,
            spec_path=output.spec_path,
            services=ServicePair(before=before, after=after),
        )
        return run_checks(_the_checks(basis.last.manifest, run), context)


def _the_checks(manifest: RenderManifest, run: _Run) -> list[Check]:
    adapter, client = run.adapter, run.shared.service_client
    checks: list[Check] = [
        SpecValid(),
        Behaviour(adapter, client, last_run_manifest=manifest),
        RoutesUnchanged(adapter),
        FormattingUnchanged(adapter),
        SpecMatchesInventory(),
        LiveContract(client),
        DocsGrounding(),
    ]
    return [_Traced(name=check.name, check=check, tracer=run.tracer) for check in checks]


# Not frozen, because a check's name is a settable attribute of the protocol.
@dataclass
class _Traced:
    """A check whose result goes to the trace."""

    name: str
    check: Check
    tracer: Tracer

    def run(self, ctx: CheckContext) -> list[Finding]:
        findings = self.check.run(ctx)
        self.tracer.event("check_result", check=self.name, findings=len(findings))
        return findings


def _commit(output_repo: Path, *, changed_paths: tuple[str, ...], run: _Run) -> _Commit | None:
    with run.step("commit"):
        source = run.request.source
        if source.kind != "url":
            return None
        run_id = run.run_dir.name
        branch = f"{BRANCH_PREFIX}{run_id}"
        commit = run.deps.source_control.commit_result(
            ResultCommit(
                clone=source.root,
                output=output_repo,
                paths=changed_paths,
                branch=branch,
                message=f"docs: annotate the handlers and document the service (ARDE run {run_id})",
            )
        )
        return _Commit(branch=branch, commit=commit)


def _save(chosen: _ChosenProfile, *, output: _Output, run: _Run) -> None:
    with run.step("save"):
        store = run.store
        if chosen.discovered is not None:
            save_profile(store, chosen.discovered)
        save_wiki(store, output.wiki)
        save_manifest(store, output.rendered.manifest)
        save_sections(store, output.docs.result.sections)
        # A copy, because the output copy is where the user reads a local run's result.
        snapshot = run.run_dir / "snapshot"
        _copy_tree(output.repo, snapshot, what="the output copy to the snapshot")
        store.save_snapshot(snapshot)


# Services


@contextmanager
def _running_service(
    repo: Path, *, profile: RepoProfile, log: Path, run: _Run
) -> Iterator[ServiceHandle]:
    with run.adapter.built_service(repo, profile) as binary:
        config = ServiceConfig(binary=binary, cwd=repo, port_env=profile.port_env, log=log)
        with run.shared.service_runner.running(config) as service:
            yield service


# A runner replaces the log it is given, so each attempt's services log to files of their own.
def _service_log(run: _Run, service: str, *, attempt: int) -> Path:
    suffix = f"-repair-{attempt}" if attempt else ""
    return run.run_dir / f"service-{service}{suffix}.log"


def _copy_tree(source: Path, dest: Path, *, what: str) -> None:
    # The links gate ran on the input copy, so a link here leads inside the copy, and stays a link.
    try:
        shutil.copytree(source, dest, symlinks=True)
    except OSError as error:
        raise CopyFailed(what, error) from error


# The title of the API in the spec: the last part of the repo's URL or folder.


def api_title(source: ResolvedSource) -> str:
    """A plain one-line name, such as "shop API" for https://example.com/team/shop.git."""
    name = re.split(r"[/\\:]", source.origin.rstrip("/\\"))[-1].removesuffix(".git")
    return f"{one_line(name, 80) or 'Service'} API"


# The trace


def _trace_run_start(run: _Run) -> None:
    source, models = run.request.source, run.shared.models
    run.tracer.event(
        "run_start",
        source=source.origin,
        source_kind=source.kind,
        revision=source.revision,
        run_dir=str(run.run_dir),
        tool_versions=run.adapter.tool_versions(),
        models={role: models[role].model for role in AGENT_ROLES if role in models},
        prompt_sha256=_prompt_hashes(run.adapter),
    )


def _prompt_hashes(adapter: LanguageAdapter) -> dict[str, str]:
    prompts = {
        "discovery": discovery.system_prompt(adapter),
        "explore": explore_agent.SYSTEM_PROMPT,
        "docs": docs_writer.system_prompt(),
    }
    return {
        role: hashlib.sha256(text.encode("utf-8")).hexdigest() for role, text in prompts.items()
    }


def _finished(run: _Run, report: RunReport) -> RunReport:
    _trace_run_end(run, status=report.status, exit_code=int(report.exit_code))
    return report


def _trace_run_end(run: _Run, *, status: str, exit_code: int | None) -> None:
    totals = run.shared.agents.budget.totals()
    run.tracer.event(
        "run_end",
        status=status,
        exit_code=exit_code,
        cost_usd=sum(role.cost_usd for role in totals.values()),
        duration_s=round(time.monotonic() - run.started_s, 3),
        per_role=totals,
    )


# The report


def _success_report(documented: _Documented, run: _Run) -> RunReport:
    source = run.request.source
    revision = source.revision[:12]
    if documented.commit is None:
        output = documented.output_repo
        return _report(
            run,
            status="success",
            exit_code=ExitCode.OK,
            message=(
                f"Documented {source.origin} at {revision}. The result is the output copy in "
                f"{output}."
            ),
            next_action=(
                f"Read report.json, then the output copy. Every change the run made: "
                f"diff -ru {run.store.input_copy(run.run_dir)} {output}"
            ),
            changed_paths=documented.changed_paths,
            result_dir=str(output),
        )
    # The real folder, because the clone link moves to the next run's clone.
    commit, clone = documented.commit, source.root.resolve()
    return _report(
        run,
        status="success",
        exit_code=ExitCode.OK,
        message=(
            f"Documented {source.origin} at {revision}, and committed {commit.commit[:12]} on "
            f"the branch {commit.branch} of the workspace clone in {clone}. Nothing was pushed."
        ),
        next_action=f"Review the commit with: git -C {clone} show --stat {commit.branch}",
        changed_paths=documented.changed_paths,
        result_dir=str(clone),
        branch=commit.branch,
        commit=commit.commit,
    )


def _failure_report(error: ArdeError, run: _Run) -> RunReport:
    findings = error.findings if isinstance(error, ChecksFailed) else ()
    return _report(
        run,
        status="failed",
        exit_code=error.exit_code,
        message=_with_notes(error),
        next_action=error.next_action,
        findings=findings,
    )


# A note is how a service that would not stop reports itself on the error that was on its way out.
def _with_notes(error: BaseException) -> str:
    return "\n".join([str(error), *getattr(error, "__notes__", ())])


def _report(
    run: _Run,
    *,
    status: str,
    exit_code: ExitCode,
    message: str,
    next_action: str,
    findings: Sequence[Finding] = (),
    changed_paths: tuple[str, ...] = (),
    result_dir: str = "",
    branch: str | None = None,
    commit: str | None = None,
) -> RunReport:
    totals = run.shared.agents.budget.totals()
    source = run.request.source
    return RunReport.model_validate(
        {
            "status": status,
            "exit_code": exit_code,
            "message": _shown(message, MESSAGE_LIMIT),
            "next_action": _shown(next_action, MESSAGE_LIMIT),
            "findings": tuple(_shown_finding(finding) for finding in findings),
            "warnings": tuple(run.warnings),
            "exemptions": (),
            "change_labels": (),
            "cost_usd": sum(role.cost_usd for role in totals.values()),
            "duration_s": round(time.monotonic() - run.started_s, 3),
            "per_role": totals,
            "changed_paths": changed_paths,
            "run_dir": str(run.run_dir),
            "source": source.origin,
            "revision": source.revision,
            "result_dir": result_dir,
            "branch": branch,
            "commit": commit,
            "repair_rounds": tuple(_shown_round(repair) for repair in run.repair_rounds),
        }
    )


def _shown_round(repair: RepairRound) -> RepairRound:
    return repair.model_copy(
        update={"findings": tuple(_shown_finding(finding) for finding in repair.findings)}
    )


def _shown_finding(finding: Finding) -> Finding:
    return finding.model_copy(
        update={
            "subject": _shown(finding.subject, WARNING_LIMIT),
            "message": _shown(finding.message, MESSAGE_LIMIT),
            "next_action": _shown(finding.next_action, MESSAGE_LIMIT),
        }
    )


# Every text of a report can hold what a repo or a model wrote, so each control character is
# written out, and a long text keeps its start, which names what went wrong.
def _shown(text: str, limit: int) -> str:
    shown = visible_text(text)
    return shown if len(shown) <= limit else shown[:limit] + " [cut]"

"""The checks' findings go back to the agents that own them, and the checks run again.

The stand-ins are those of the first-run tests, and the checks are the real ones, so each finding
here is one a check really raised on the stand-ins' output. An endpoint fact that quotes code and
cites no probe leaves the live contract nothing to start from, which is explore's to repair, and a
page that names an address the repo does not hold is docs'.
"""

import json
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from end_to_end_support import writing_a_section
from pipeline_support import (
    THINGS_FACT,
    FakeAdapter,
    FakeRunner,
    Rig,
    a_change_guide,
    an_overview,
    exploring,
    submitting_the_profile,
    summarising,
)

from arde.application.pipeline import run_pipeline
from arde.application.ports import (
    LLMRequest,
    LLMResponse,
    ProviderError,
    ServiceConfig,
    ServiceHandle,
)
from arde.application.repair import REPAIR_HEADING
from arde.application.wiki_store import load_wiki
from arde.domain.facts import CodeQuote, Fact, Links, RuleFact
from arde.domain.findings import Finding
from arde.domain.report import ExitCode, RunReport

THINGS_CODE = CodeQuote(kind="code", file="main.go", quote="func things() {}")
# Nothing it cites was probed, so the live contract has no request to start from. It lists no
# response, because a code quote backs no status.
UNPROBED_THINGS = THINGS_FACT.model_copy(update={"evidence": (THINGS_CODE,), "responses": ()})
UNKNOWN_ADDRESS = "https://unknown.example/rules"
CODE_FINDING = Finding(
    check="agent_edits", owner="code", subject="main.go", message="m", next_action="n"
)


ORDER_RULE = RuleFact(
    id="rule.order",
    topic="business-rules",
    kind="rule",
    evidence=(THINGS_CODE,),
    links=Links(declarations=("main.go::func things",)),
    statement="Things come back in the order they were added.",
)
PORT_RULE = RuleFact(
    id="rule.port",
    topic="configuration",
    kind="rule",
    evidence=(THINGS_CODE,),
    links=Links(declarations=("main.go::func main",)),
    statement="The port comes from THINGS_PORT.",
)


def run(rig: Rig) -> RunReport:
    return run_pipeline(rig.request(), rig.deps())


def tool_names(request: LLMRequest) -> set[str]:
    return {tool.name for tool in request.tools}


def first_message(request: LLMRequest) -> str:
    content = request.messages[0]["content"]
    assert isinstance(content, str)
    return content


def agent_of(request: LLMRequest) -> str:
    names = tool_names(request)
    return (
        "discovery" if "submit_profile" in names else "explore" if "add_facts" in names else "docs"
    )


# Every agent run starts with one message, the run's context.
def first_turns_of(rig: Rig, agent: str) -> list[LLMRequest]:
    return [r for r in rig.llm.requests if agent_of(r) == agent and len(r.messages) == 1]


def assignment_of(request: LLMRequest) -> str:
    return first_message(request).split("\n\n", 1)[0]


def steps_and_rounds(rig: Rig) -> list[str]:
    return [
        str(fields["step"]) if kind == "step_start" else f"repair {fields['round']}"
        for kind, fields in rig.tracer.events
        if kind in ("step_start", "repair")
    ]


# An explore finding goes to explore, then to docs, and the checks run again


# Each attempt writes the overview and the change guide, which rest on every fact, so an attempt
# whose only fact is the endpoint has those two sections to write.
def an_attempt(fact: Fact, *, injections: tuple[str, ...] = ()) -> list[LLMResponse]:
    return [*exploring(fact, injections=injections), *summarising(fact.id)]


def an_explore_repair(tmp_path: Path, **changes: object) -> Rig:
    script = [submitting_the_profile(), *an_attempt(UNPROBED_THINGS), *an_attempt(THINGS_FACT)]
    return Rig(tmp_path=tmp_path, script=script, **changes)  # type: ignore[arg-type]


def test_an_explore_finding_is_repaired_by_explore_and_the_run_commits(tmp_path: Path) -> None:
    rig = an_explore_repair(tmp_path, kind="url")

    report = run(rig)

    assert (report.status, report.exit_code, report.findings) == ("success", ExitCode.OK, ())
    [commit] = rig.source_control.commits
    assert commit.paths == report.changed_paths
    [repaired] = report.repair_rounds
    assert repaired.number == 1
    assert [(f.check, f.owner, f.subject) for f in repaired.findings] == [
        ("live_contract", "explore", "GET /things")
    ]


def test_the_explore_repair_starts_from_this_runs_wiki_and_sees_the_findings(
    tmp_path: Path,
) -> None:
    rig = an_explore_repair(tmp_path)

    run(rig)

    [first, repair] = first_turns_of(rig, "explore")
    assert REPAIR_HEADING not in first_message(first)
    _, context, heading, findings = first_message(repair).split("\n\n")
    shown = json.loads(context)
    assert [fact["evidence"] for fact in shown["wiki"]] == [[THINGS_CODE.model_dump(mode="json")]]
    assert (shown["stale_fact_ids"], shown["change_report"]) == ([], None)
    assert heading.startswith(REPAIR_HEADING)
    assert [finding["subject"] for finding in json.loads(findings)] == ["GET /things"]


def test_the_repaired_wiki_is_the_one_saved(tmp_path: Path) -> None:
    rig = an_explore_repair(tmp_path)

    run(rig)

    saved = load_wiki(rig.store)
    assert [(probe.method, probe.path_and_query) for probe in saved.probes] == [("GET", "/things")]


def test_a_repair_round_runs_explore_then_writes_and_checks_the_output_again(
    tmp_path: Path,
) -> None:
    rig = an_explore_repair(tmp_path)

    run(rig)

    assert steps_and_rounds(rig)[5:] == [
        "explore",
        "copy_output",
        "render",
        "spec",
        "docs",
        "checks",
        "repair 1",
        "explore",
        "copy_output",
        "render",
        "spec",
        "docs",
        "checks",
        "commit",
        "save",
    ]
    assert rig.tracer.events_of("repair") == [
        {"round": 1, "owners": ["explore", "docs"], "findings": 1}
    ]


def test_each_attempt_starts_its_own_services_with_logs_of_their_own(tmp_path: Path) -> None:
    rig = an_explore_repair(tmp_path)

    report = run(rig)

    run_dir = Path(report.run_dir)
    started = [(config.cwd, config.log.name) for config in rig.runner.configs]
    assert started == [
        (run_dir / "input", "service-explore.log"),
        (run_dir / "input", "service-before.log"),
        (run_dir / "output", "service-after.log"),
        (run_dir / "input", "service-explore-repair-1.log"),
        (run_dir / "input", "service-before-repair-1.log"),
        (run_dir / "output", "service-after-repair-1.log"),
    ]


@dataclass
class WatchingRunner(FakeRunner):
    """Notes which services run at once. Each one, like a service that keeps its data beside it,
    writes a file of its own into the folder it runs in."""

    running_now: list[Path] = field(default_factory=list)
    most_at_once: int = 0

    @contextmanager
    def running(self, config: ServiceConfig) -> Iterator[ServiceHandle]:
        (config.cwd / f"{config.log.stem}.data").write_text("written by the service\n")
        with super().running(config) as service:
            self.running_now.append(config.cwd)
            self.most_at_once = max(self.most_at_once, len(self.running_now))
            try:
                yield service
            finally:
                self.running_now.remove(config.cwd)


def test_services_run_two_at_a_time_and_all_are_stopped(tmp_path: Path) -> None:
    rig = an_explore_repair(tmp_path, runner=WatchingRunner())

    run(rig)

    assert isinstance(rig.runner, WatchingRunner)
    assert rig.runner.most_at_once == 2
    assert rig.runner.running_now == []


def test_a_file_the_last_attempts_service_wrote_is_not_committed(tmp_path: Path) -> None:
    rig = an_explore_repair(tmp_path, runner=WatchingRunner(), kind="url")

    report = run(rig)

    assert report.status == "success"
    assert report.changed_paths == (
        "docs/api.md",
        "docs/change-guide.md",
        "docs/openapi.json",
        "docs/overview.md",
        "main.go",
    )
    [commit] = rig.source_control.commits
    assert commit.paths == report.changed_paths


class FailingAfter:
    """Gives its replies in order, then fails as the provider does once they run out."""

    def __init__(self, replies: list[LLMResponse]) -> None:
        self._replies = list(replies)
        self.requests: list[LLMRequest] = []

    def complete(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        if not self._replies:
            raise ProviderError("Anthropic is overloaded", next_action="Run again later.")
        return self._replies.pop(0)


def test_a_repair_that_fails_stops_every_service_and_reports_the_round(tmp_path: Path) -> None:
    rig = an_explore_repair(tmp_path, runner=WatchingRunner())
    rig.llm = FailingAfter([submitting_the_profile(), *an_attempt(UNPROBED_THINGS)])  # type: ignore[assignment]

    report = run(rig)

    assert (report.status, report.message) == ("failed", "Anthropic is overloaded")
    assert agent_of(rig.llm.requests[-1]) == "explore"
    assert isinstance(rig.runner, WatchingRunner)
    assert rig.runner.running_now == []
    assert [r.number for r in report.repair_rounds] == [1]
    assert not rig.store.wiki_dir.exists()


def test_a_warning_both_attempts_give_is_reported_once(tmp_path: Path) -> None:
    script = [
        submitting_the_profile(),
        *an_attempt(UNPROBED_THINGS, injections=("main.go:2",)),
        *an_attempt(THINGS_FACT, injections=("main.go:2",)),
    ]
    rig = Rig(tmp_path=tmp_path, script=script)

    report = run(rig)

    assert report.warnings.count("suspected prompt injection at main.go:2") == 1


# A docs finding goes to docs alone


# On a first run the docs repair writes every section again, each with the findings in view.
def writing_the_sections(business_rules: str) -> list[LLMResponse]:
    return [
        an_overview(ORDER_RULE.id),
        writing_a_section(business_rules, ORDER_RULE.id),
        a_change_guide(ORDER_RULE.id),
    ]


def a_docs_repair(tmp_path: Path, *, repaired_section: str, **changes: object) -> Rig:
    script = [
        submitting_the_profile(),
        *exploring(THINGS_FACT, ORDER_RULE),
        *writing_the_sections(f"Read {UNKNOWN_ADDRESS} before you add things."),
        *writing_the_sections(repaired_section),
    ]
    return Rig(tmp_path=tmp_path, script=script, **changes)  # type: ignore[arg-type]


BUSINESS_RULES = 'Write the section "business-rules"'


def test_a_docs_finding_is_repaired_by_docs_alone(tmp_path: Path) -> None:
    rig = a_docs_repair(tmp_path, repaired_section="Things come back in the order they were added.")

    report = run(rig)

    assert (report.status, report.findings) == ("success", ())
    assert [agent_of(r) for r in rig.llm.requests] == [
        "discovery",
        *["explore"] * 3,
        *["docs"] * 3,
        *["docs"] * 3,
    ]
    sections = first_turns_of(rig, "docs")
    first, repair = sections[:3], sections[3:]
    assert not any(REPAIR_HEADING in first_message(r) for r in first)
    assert all(REPAIR_HEADING in first_message(r) for r in repair)
    [business_rules] = [r for r in repair if assignment_of(r).startswith(BUSINESS_RULES)]
    assert f"docs/business-rules.md: {UNKNOWN_ADDRESS}" in first_message(business_rules)
    page = Path(report.run_dir) / "output" / "docs" / "business-rules.md"
    assert "in the order they were added" in page.read_text()
    assert UNKNOWN_ADDRESS not in page.read_text()
    [repaired] = report.repair_rounds
    assert [(f.check, f.owner) for f in repaired.findings] == [("docs_grounding", "docs")]
    assert rig.tracer.events_of("repair") == [{"round": 1, "owners": ["docs"], "findings": 1}]


# A finding no agent can repair stops the loop before any model call


def test_a_code_finding_stops_the_run_with_no_model_call_even_beside_an_explore_one(
    tmp_path: Path,
) -> None:
    script = [submitting_the_profile(), *an_attempt(UNPROBED_THINGS)]
    adapter = FakeAdapter(edit_findings=[CODE_FINDING])
    rig = Rig(tmp_path=tmp_path, script=script, adapter=adapter, kind="url")

    report = run(rig)

    assert (report.status, report.exit_code) == ("failed", ExitCode.CHECKS_FAILED)
    assert {f.owner for f in report.findings} == {"code", "explore"}
    assert len(rig.llm.requests) == len(script)
    assert report.repair_rounds == ()
    assert rig.tracer.events_of("repair") == []
    assert rig.source_control.commits == []


# Findings left after the last round fail the run


def test_findings_left_after_the_last_round_exit_1_and_commit_nothing(tmp_path: Path) -> None:
    script = [
        submitting_the_profile(),
        *an_attempt(UNPROBED_THINGS),
        *an_attempt(UNPROBED_THINGS),
    ]
    rig = Rig(tmp_path=tmp_path, script=script, kind="url")

    report = run(rig)

    assert (report.status, report.exit_code) == ("failed", ExitCode.CHECKS_FAILED)
    assert [(f.check, f.subject) for f in report.findings] == [("live_contract", "GET /things")]
    assert [r.number for r in report.repair_rounds] == [1]
    assert len(rig.llm.requests) == len(script)
    assert rig.source_control.commits == []
    assert not rig.store.wiki_dir.exists()
    assert not rig.store.has_snapshot()


def test_a_docs_finding_left_after_the_repair_names_the_page(tmp_path: Path) -> None:
    rig = a_docs_repair(tmp_path, repaired_section=f"See {UNKNOWN_ADDRESS}.")

    report = run(rig)

    assert report.exit_code == ExitCode.CHECKS_FAILED
    assert [f.subject for f in report.findings] == [f"docs/business-rules.md: {UNKNOWN_ADDRESS}"]


# On a later run, the sections that rest on facts explore changed in a repair are written again


def test_a_section_whose_facts_a_repair_changed_is_written_again(tmp_path: Path) -> None:
    first = Rig(
        tmp_path=tmp_path,
        script=[
            submitting_the_profile(),
            *exploring(THINGS_FACT, ORDER_RULE, PORT_RULE),
            an_overview(ORDER_RULE.id),
            writing_a_section("Things come back in the order they were added.", ORDER_RULE.id),
            writing_a_section("Set THINGS_PORT to choose the port.", PORT_RULE.id),
            a_change_guide(ORDER_RULE.id),
        ],
    )
    assert run(first).status == "success"
    # The first attempt rewords the order rule and drops the probe. The repair brings back both,
    # so the order rule is again what the last run saved, and only the repair changed it. The
    # overview and the change guide rest on every fact, so each attempt writes them too.
    reworded = ORDER_RULE.model_copy(update={"statement": "Things come back oldest first."})
    second = Rig(
        tmp_path=tmp_path,
        script=[
            *exploring(UNPROBED_THINGS, reworded),
            an_overview(ORDER_RULE.id),
            writing_a_section("Oldest first.", ORDER_RULE.id),
            a_change_guide(ORDER_RULE.id),
            *exploring(THINGS_FACT, ORDER_RULE),
            an_overview(ORDER_RULE.id),
            writing_a_section("In the order they were added, again.", ORDER_RULE.id),
            a_change_guide(ORDER_RULE.id),
        ],
        adapter=FakeAdapter(changed=["main.go::func things"]),
    )

    report = run(second)

    assert (report.status, report.findings) == ("success", ())
    [first_attempt, written_again] = [
        r for r in first_turns_of(second, "docs") if assignment_of(r).startswith(BUSINESS_RULES)
    ]
    assert assignment_of(first_attempt).startswith(BUSINESS_RULES)
    assert assignment_of(written_again).startswith(BUSINESS_RULES)
    assert REPAIR_HEADING in first_message(written_again)
    docs = Path(report.run_dir) / "output" / "docs"
    assert "again" in (docs / "business-rules.md").read_text()
    assert "Set THINGS_PORT" in (docs / "configuration.md").read_text()

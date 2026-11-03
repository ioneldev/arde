"""The first run, step by step, with stand-ins for the language, the service and the model.

The checks are the real ones, so a run that succeeds here passed every check on the stand-ins'
output. Integration tests in tests/test_end_to_end.py run the same flow on a real service.
"""

import json
from pathlib import Path

import pytest
from pipeline_support import (
    ANNOTATION,
    PROFILE,
    THINGS_FACT,
    THINGS_ROUTE,
    FakeAdapter,
    Rig,
    a_change_guide,
    a_check_result,
    a_first_run_script,
    an_inventory,
    an_overview,
    exploring,
    submitting_the_profile,
    summarising,
)
from scripted_model import USAGE, calling, tool_call

from arde.application.agents.discovery import system_prompt as discovery_prompt
from arde.application.agents.docs_writer import system_prompt as docs_prompt
from arde.application.agents.explore import SYSTEM_PROMPT as EXPLORE_PROMPT
from arde.application.pipeline import run_pipeline
from arde.application.ports import LLMResponse, ProviderError, RouteHashes
from arde.application.profile_store import save_profile
from arde.domain.facts import RuleFact
from arde.domain.findings import Finding
from arde.domain.profile import DiscoveredProfile
from arde.domain.report import ExitCode, RunReport


def ending_the_turn() -> LLMResponse:
    return LLMResponse(
        stop_reason="end_turn",
        tool_calls=(),
        usage=USAGE,
        latency_s=0.1,
        assistant_content=({"type": "text", "text": "Nothing to submit."},),
    )


class FailingMidRun(Exception):
    pass


def run(rig: Rig) -> RunReport:
    return run_pipeline(rig.request(), rig.deps())


def a_rig(tmp_path: Path, script: list[LLMResponse] | None = None, **changes: object) -> Rig:
    return Rig(
        tmp_path=tmp_path, script=a_first_run_script() if script is None else script, **changes
    )  # type: ignore[arg-type]


def warnings_of(report: RunReport) -> str:
    return "\n".join(report.warnings)


# A whole run


def test_a_first_run_documents_the_repo_and_passes_every_check(tmp_path: Path) -> None:
    rig = a_rig(tmp_path)

    report = run(rig)

    assert report.findings == ()
    assert (report.status, report.exit_code) == ("success", ExitCode.OK)
    output = Path(report.run_dir) / "output"
    assert ANNOTATION in (output / "main.go").read_text()
    assert (output / "docs" / "api.md").is_file()
    assert report.changed_paths == (
        "docs/api.md",
        "docs/change-guide.md",
        "docs/openapi.json",
        "docs/overview.md",
        "main.go",
    )


def test_the_steps_run_in_order(tmp_path: Path) -> None:
    rig = a_rig(tmp_path)

    run(rig)

    steps = [event["step"] for event in rig.tracer.events_of("step_start")]
    assert steps == [
        "copy_input",
        "baseline",
        "profile",
        "inventory",
        "changes",
        "explore",
        "copy_output",
        "render",
        "spec",
        "docs",
        "checks",
        "commit",
        "save",
    ]


def test_the_input_copy_is_never_written(tmp_path: Path) -> None:
    rig = a_rig(tmp_path)

    report = run(rig)

    assert ANNOTATION not in (Path(report.run_dir) / "input" / "main.go").read_text()


def test_explore_probes_the_service_built_from_the_input_copy(tmp_path: Path) -> None:
    rig = a_rig(tmp_path)

    report = run(rig)

    explore_service = rig.runner.configs[0]
    assert explore_service.cwd == Path(report.run_dir) / "input"
    assert explore_service.port_env == PROFILE["port_env"]


def test_the_checks_run_on_both_copies_with_both_services(tmp_path: Path) -> None:
    rig = a_rig(tmp_path)

    report = run(rig)

    run_dir = Path(report.run_dir)
    assert [config.cwd for config in rig.runner.configs[1:]] == [
        run_dir / "input",
        run_dir / "output",
    ]
    assert rig.adapter.checked_repos == [run_dir / "input", run_dir / "output"]
    checked = {event["check"] for event in rig.tracer.events_of("check_result")}
    assert checked == {
        "spec_valid",
        "behaviour",
        "routes_unchanged",
        "formatting_unchanged",
        "spec_matches_inventory",
        "live_contract",
        "docs_grounding",
    }


def test_a_successful_run_saves_the_profile_wiki_manifest_sections_and_snapshot(
    tmp_path: Path,
) -> None:
    rig = a_rig(tmp_path)

    run(rig)

    assert rig.store.profile_path.is_file()
    assert (rig.store.wiki_dir / "api.yaml").is_file()
    assert ANNOTATION in rig.store.manifest_path.read_text()
    assert rig.store.sections_path.is_file()
    assert ANNOTATION in (rig.store.snapshot_dir / "main.go").read_text()


def test_a_local_run_commits_nothing_and_says_where_the_output_copy_is(tmp_path: Path) -> None:
    rig = a_rig(tmp_path)

    report = run(rig)

    assert rig.source_control.commits == []
    assert report.result_dir == str(Path(report.run_dir) / "output")
    assert report.branch is None
    assert report.result_dir in report.message


def test_url_run_commits_on_new_branch_in_workspace_only(tmp_path: Path) -> None:
    rig = a_rig(tmp_path, kind="url")

    report = run(rig)

    [commit] = rig.source_control.commits
    run_id = Path(report.run_dir).name
    assert commit.branch == f"arde/run-{run_id}"
    assert commit.clone == rig.repo
    assert commit.output == Path(report.run_dir) / "output"
    assert commit.paths == report.changed_paths
    assert (report.branch, report.commit) == (commit.branch, "c0ffee" * 6 + "abcd")
    assert report.result_dir == str(rig.repo.resolve())
    assert commit.branch in report.message


# Bad input and a red baseline stop before any paid call


def test_no_go_mod_exits_2_with_next_action(tmp_path: Path) -> None:
    rig = a_rig(tmp_path, adapter=FakeAdapter(present=False))

    report = run(rig)

    assert (report.status, report.exit_code) == ("failed", ExitCode.BAD_USAGE)
    assert "module" in report.next_action
    assert rig.llm.requests == []


def test_baseline_red_stops_with_clear_message(tmp_path: Path) -> None:
    red = a_check_result(test_ok=False, output="== test (exit 1) ==\nFAIL TestThings\n")
    rig = a_rig(tmp_path, adapter=FakeAdapter(baseline=red))

    report = run(rig)

    assert (report.status, report.exit_code) == ("failed", ExitCode.CHECKS_FAILED)
    assert "its own tests fail" in report.message
    assert "FAIL TestThings" in report.message
    assert rig.llm.requests == []
    assert not rig.store.profile_path.exists()


def test_a_baseline_that_does_not_build_names_the_build(tmp_path: Path) -> None:
    red = a_check_result(build_ok=False, vet_ok=False, test_ok=False)

    report = run(a_rig(tmp_path, adapter=FakeAdapter(baseline=red)))

    assert "does not build" in report.message


def test_the_checks_output_reaches_the_message_with_control_characters_written_out(
    tmp_path: Path,
) -> None:
    red = a_check_result(test_ok=False, output="FAIL \x1b[2J cleared")

    report = run(a_rig(tmp_path, adapter=FakeAdapter(baseline=red)))

    assert "\x1b" not in report.message
    assert "\\x1b[2J" in report.message


def test_zero_routes_never_passes(tmp_path: Path) -> None:
    rig = a_rig(tmp_path, adapter=FakeAdapter(inventory=an_inventory(routes=())))

    report = run(rig)

    assert report.status == "failed"
    assert report.exit_code == ExitCode.DISCOVERY_NEEDS_INPUT
    assert "no route" in report.message


# Warnings


def test_zero_tests_run_gives_warning(tmp_path: Path) -> None:
    quiet = a_check_result(tests_run=0, tests_listed=0)

    report = run(a_rig(tmp_path, adapter=FakeAdapter(baseline=quiet)))

    assert report.status == "success"
    assert "ran no test" in warnings_of(report)


def test_skipped_tests_give_one_warning_with_a_count_and_the_first_50_names(
    tmp_path: Path,
) -> None:
    skipped = tuple(f"things.TestSkipped{number}" for number in range(60))
    baseline = a_check_result(tests_skipped=skipped)

    report = run(a_rig(tmp_path, adapter=FakeAdapter(baseline=baseline)))

    [warning] = [line for line in report.warnings if "skipped" in line]
    assert warning.startswith("60 tests skipped")
    assert "things.TestSkipped49" in warning
    assert "things.TestSkipped50" not in warning
    assert "and 10 more" in warning


def test_tests_run_below_tests_listed_gives_warning(tmp_path: Path) -> None:
    baseline = a_check_result(tests_run=1, tests_listed=3)

    report = run(a_rig(tmp_path, adapter=FakeAdapter(baseline=baseline)))

    assert "1 of the 3 tests" in warnings_of(report)


def test_no_warning_when_the_listed_tests_are_unknown(tmp_path: Path) -> None:
    baseline = a_check_result(tests_run=1, tests_listed=None)

    report = run(a_rig(tmp_path, adapter=FakeAdapter(baseline=baseline)))

    assert "tests the toolchain lists" not in warnings_of(report)


def test_hidden_test_files_give_warning(tmp_path: Path) -> None:
    baseline = a_check_result(ignored_files=("web/smoke_\x1b[0m_test.go",))

    report = run(a_rig(tmp_path, adapter=FakeAdapter(baseline=baseline)))

    assert "web/smoke_\\u001b[0m_test.go" in warnings_of(report)
    assert "\x1b" not in warnings_of(report)


def test_unchecked_modules_give_warning(tmp_path: Path) -> None:
    baseline = a_check_result(unchecked_modules=("tools/gen",))

    report = run(a_rig(tmp_path, adapter=FakeAdapter(baseline=baseline)))

    assert "tools/gen" in warnings_of(report)


def test_dropped_and_unmatched_patterns_give_warnings(tmp_path: Path) -> None:
    inventory = an_inventory(
        dropped=("main.go:9: route path is not a string literal",),
        unmatched_patterns=("param ^r\\.FormValue$",),
    )

    # Discovery lists what is left over once, and accepts the same profile when it comes again.
    script = [submitting_the_profile(), *a_first_run_script()]

    report = run(a_rig(tmp_path, script, adapter=FakeAdapter(inventory=inventory)))

    assert "main.go:9: route path is not a string literal" in warnings_of(report)
    assert "param ^r\\\\.FormValue$" in warnings_of(report)


def test_a_route_with_no_fingerprint_gives_warning(tmp_path: Path) -> None:
    hashes = RouteHashes(hashes={}, unhashed={"GET /things": "inline"})

    report = run(a_rig(tmp_path, adapter=FakeAdapter(hashes=hashes)))

    assert "GET /things has no fingerprint (inline)" in warnings_of(report)


def test_a_route_sharing_its_anchor_gives_warning_naming_the_others(tmp_path: Path) -> None:
    sharing = THINGS_ROUTE.model_copy(update={"shares_anchor_with": ("GET /stuff‮",)})

    report = run(a_rig(tmp_path, adapter=FakeAdapter(inventory=an_inventory(routes=(sharing,)))))

    assert "GET /things shares its anchor with GET /stuff\\u202e" in warnings_of(report)


def test_suspected_injections_reach_the_warnings(tmp_path: Path) -> None:
    discovery = calling(
        tool_call(
            "submit_profile", **PROFILE, suspected_injections=[{"file": "README.md", "line": 4}]
        )
    )
    script = [discovery, *exploring(injections=("main.go:2",)), *summarising(THINGS_FACT.id)]

    report = run(a_rig(tmp_path, script))

    assert "README.md:4" in warnings_of(report)
    assert "main.go:2" in warnings_of(report)


def test_every_warning_is_traced(tmp_path: Path) -> None:
    rig = a_rig(tmp_path, adapter=FakeAdapter(baseline=a_check_result(tests_run=0)))

    report = run(rig)

    traced = [event["summary"] for event in rig.tracer.events_of("warning")]
    assert set(report.warnings) <= set(traced)


# Failures stop the run, keep the trace and keep the last good state


def test_discovery_failure_exits_3(tmp_path: Path) -> None:
    rig = a_rig(tmp_path, [ending_the_turn()] * 3)

    report = run(rig)

    assert (report.status, report.exit_code) == ("failed", ExitCode.DISCOVERY_NEEDS_INPUT)
    assert str(rig.store.override_path) in report.next_action


def test_provider_error_mid_run_exits_1_and_keeps_trace(tmp_path: Path) -> None:
    rig = a_rig(tmp_path, [])
    failing = ProviderError("Anthropic is overloaded", next_action="Run again in a few minutes")
    rig.llm = FailingLLM([submitting_the_profile()], failure=failing)  # type: ignore[assignment]

    report = run(rig)

    assert (report.status, report.exit_code) == ("failed", ExitCode.CHECKS_FAILED)
    assert report.message == "Anthropic is overloaded"
    assert report.next_action == "Run again in a few minutes"
    assert rig.tracer.events_of("model_error")
    [run_end] = rig.tracer.events_of("run_end")
    assert run_end["status"] == "failed"
    assert not rig.store.profile_path.exists()


def test_budget_exceeded_exits_4(tmp_path: Path) -> None:
    rig = a_rig(tmp_path, max_usd=0.000001)

    report = run(rig)

    assert (report.status, report.exit_code) == ("failed", ExitCode.BUDGET_EXCEEDED)
    assert "--max-usd" in report.next_action
    assert report.cost_usd > 0


def test_a_finding_fails_the_run_with_the_findings_and_commits_nothing(tmp_path: Path) -> None:
    finding = Finding(
        check="agent_edits", owner="code", subject="main.go", message="m\x07", next_action="n"
    )
    rig = a_rig(tmp_path, adapter=FakeAdapter(edit_findings=[finding]), kind="url")

    report = run(rig)

    assert (report.status, report.exit_code) == ("failed", ExitCode.CHECKS_FAILED)
    assert [f.subject for f in report.findings] == ["main.go"]
    assert report.findings[0].message == "m\\x07"
    assert rig.source_control.commits == []


def test_failed_run_keeps_old_snapshot(tmp_path: Path) -> None:
    first = a_rig(tmp_path)
    run(first)
    snapshot = (first.store.snapshot_dir / "main.go").read_text()
    finding = Finding(check="agent_edits", owner="code", subject="s", message="m", next_action="n")
    second = Rig(
        tmp_path=tmp_path, script=exploring(), adapter=FakeAdapter(edit_findings=[finding])
    )
    (second.repo / "main.go").write_text(
        "package main\n\nfunc main() {}\n\nfunc things() {}\n// x\n"
    )

    report = run(second)

    assert report.status == "failed"
    assert (second.store.snapshot_dir / "main.go").read_text() == snapshot


def test_a_failed_run_saves_no_wiki(tmp_path: Path) -> None:
    finding = Finding(check="agent_edits", owner="code", subject="s", message="m", next_action="n")
    rig = a_rig(tmp_path, adapter=FakeAdapter(edit_findings=[finding]))

    run(rig)

    assert not rig.store.wiki_dir.exists()
    assert not rig.store.manifest_path.exists()


def test_an_error_note_reaches_the_message(tmp_path: Path) -> None:
    rig = a_rig(tmp_path, [])
    failing = ProviderError("Anthropic failed", next_action="Run again")
    failing.add_note("The service on port 5001 would not stop\x1b[0m.")
    rig.llm = FailingLLM([submitting_the_profile()], failure=failing)  # type: ignore[assignment]

    report = run(rig)

    assert "The service on port 5001 would not stop\\x1b[0m." in report.message


def test_an_interrupt_ends_the_trace_and_goes_on_up(tmp_path: Path) -> None:
    rig = a_rig(tmp_path, [])
    rig.llm = FailingLLM([submitting_the_profile()], failure=KeyboardInterrupt())  # type: ignore[assignment]

    with pytest.raises(KeyboardInterrupt):
        run(rig)

    [run_end] = rig.tracer.events_of("run_end")
    assert run_end["status"] == "interrupted"
    assert not rig.store.has_snapshot()


# The trace


def test_run_start_records_commit_tool_versions_models_and_prompt_hashes(tmp_path: Path) -> None:
    rig = a_rig(tmp_path)

    run(rig)

    [start] = rig.tracer.events_of("run_start")
    assert start["revision"] == "1234567890abcdef1234567890abcdef12345678"
    assert start["tool_versions"] == {"compiler": "1.2.3", "helper": "sha256:abc"}
    assert start["models"] == {
        "discovery": "claude-opus-5-5",
        "explore": "claude-opus-5-5",
        "docs": "claude-opus-5-5",
    }
    hashes = start["prompt_sha256"]
    assert isinstance(hashes, dict)
    assert set(hashes) == {"discovery", "explore", "docs"}
    import hashlib

    assert hashes["discovery"] == hashlib.sha256(discovery_prompt(rig.adapter).encode()).hexdigest()
    assert hashes["explore"] == hashlib.sha256(EXPLORE_PROMPT.encode()).hexdigest()
    assert hashes["docs"] == hashlib.sha256(docs_prompt().encode()).hexdigest()


def test_run_end_has_per_role_totals(tmp_path: Path) -> None:
    rig = a_rig(tmp_path)

    report = run(rig)

    [end] = rig.tracer.events_of("run_end")
    per_role = end["per_role"]
    assert isinstance(per_role, dict)
    assert per_role["discovery"].calls == 1
    assert per_role["explore"].calls == 3
    assert end["cost_usd"] == pytest.approx(report.cost_usd)
    assert report.per_role["explore"].calls == 3


def test_each_step_is_traced_with_its_duration(tmp_path: Path) -> None:
    rig = a_rig(tmp_path)

    run(rig)

    ends = rig.tracer.events_of("step_end")
    assert all(isinstance(event["duration_s"], float) for event in ends)
    assert [event["step"] for event in ends][-1] == "save"


# A run after a successful one


def test_a_second_run_reuses_the_saved_profile_and_calls_no_discovery(tmp_path: Path) -> None:
    run(a_rig(tmp_path))
    second = Rig(tmp_path=tmp_path, script=exploring())

    report = run(second)

    assert report.status == "success"
    assert all(request.config == second.llm.requests[0].config for request in second.llm.requests)
    assert "submit_profile" not in {
        tool.name for request in second.llm.requests for tool in request.tools
    }


def test_a_saved_profile_that_no_longer_fits_runs_discovery_again(tmp_path: Path) -> None:
    rig = a_rig(tmp_path)
    save_profile(
        rig.store,
        DiscoveredProfile.model_validate({**PROFILE, "main_file": "elsewhere.go"}),
    )

    report = run(rig)

    assert report.status == "success"
    assert "saved profile no longer fits" in warnings_of(report)


def a_rule(fact_id: str, topic: str, *, statement: str, declaration: str) -> RuleFact:
    return RuleFact.model_validate(
        {
            "id": fact_id,
            "topic": topic,
            "kind": "rule",
            "evidence": [evidence.model_dump() for evidence in THINGS_FACT.evidence],
            "links": {"declarations": [declaration]},
            "statement": statement,
        }
    )


ORDER_RULE = a_rule(
    "rule.order",
    "business-rules",
    statement="Things come back in the order they were added.",
    declaration="main.go::func things",
)
PORT_RULE = a_rule(
    "rule.port",
    "configuration",
    statement="The port comes from THINGS_PORT.",
    declaration="main.go::func main",
)


def writing_a_section(markdown: str, *fact_ids: str) -> LLMResponse:
    return calling(
        tool_call(
            "submit_section",
            markdown=markdown,
            used_fact_ids=list(fact_ids),
            suspected_injections=[],
        )
    )


def a_documented_repo(tmp_path: Path) -> Rig:
    """A first run whose wiki holds the endpoint and one rule for each of two sections."""
    script = [
        submitting_the_profile(),
        *exploring(THINGS_FACT, ORDER_RULE, PORT_RULE),
        an_overview("rule.order"),
        writing_a_section("Things come back in the order they were added.", "rule.order"),
        writing_a_section("Set THINGS_PORT to choose the port.", "rule.port"),
        a_change_guide("rule.order"),
    ]
    rig = a_rig(tmp_path, script)
    assert run(rig).status == "success"
    return rig


def explore_context(rig: Rig) -> dict[str, object]:
    first_message = rig.llm.requests[0].messages[0]["content"]
    assert isinstance(first_message, str)
    context: dict[str, object] = json.loads(first_message.split("\n\n", 1)[1])
    return context


def test_a_second_run_marks_only_the_facts_linked_to_a_changed_declaration_stale(
    tmp_path: Path,
) -> None:
    a_documented_repo(tmp_path)
    adapter = FakeAdapter(changed=["main.go::func things"])
    second = Rig(tmp_path=tmp_path, script=exploring(ORDER_RULE), adapter=adapter)

    report = run(second)

    assert report.status == "success"
    context = explore_context(second)
    assert context["stale_fact_ids"] == ["rule.order"]
    change_report = context["change_report"]
    assert isinstance(change_report, dict)
    assert change_report["changed_declarations"] == ["main.go::func things"]
    assert change_report["changed_files"] == []


def test_a_fact_submitted_again_unchanged_rewrites_no_section(tmp_path: Path) -> None:
    first = a_documented_repo(tmp_path)
    page = first.store.snapshot_dir / "docs" / "business-rules.md"
    second = Rig(
        tmp_path=tmp_path,
        script=exploring(ORDER_RULE),
        adapter=FakeAdapter(changed=["main.go::func things"]),
    )

    report = run(second)

    assert report.status == "success"
    output_page = Path(report.run_dir) / "output" / "docs" / "business-rules.md"
    assert output_page.read_text() == page.read_text()


def test_a_changed_fact_rewrites_its_own_section_and_no_other(tmp_path: Path) -> None:
    a_documented_repo(tmp_path)
    reworded = ORDER_RULE.model_copy(update={"statement": "Things come back oldest first."})
    script = [*exploring(reworded), writing_a_section("Oldest first.", "rule.order")]
    second = Rig(
        tmp_path=tmp_path, script=script, adapter=FakeAdapter(changed=["main.go::func things"])
    )

    report = run(second)

    assert report.status == "success"
    docs_requests = [r for r in second.llm.requests if r.config == second.llm.requests[-1].config]
    sections = [r for r in docs_requests if "submit_section" in {t.name for t in r.tools}]
    assert len(sections) == 1
    assert "business-rules" in str(sections[0].messages[0]["content"])
    output = Path(report.run_dir) / "output" / "docs"
    assert "Oldest first." in (output / "business-rules.md").read_text()
    assert "Set THINGS_PORT" in (output / "configuration.md").read_text()


class FailingLLM:
    """Gives its replies in order, then raises the failure on the first request past them."""

    def __init__(self, replies: list[LLMResponse], *, failure: BaseException) -> None:
        self._replies = list(replies)
        self._failure = failure
        self.requests: list[object] = []

    def complete(self, request: object) -> LLMResponse:
        self.requests.append(request)
        if not self._replies:
            raise self._failure
        return self._replies.pop(0)

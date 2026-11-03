"""The blind judge as the last gate of a run, with the stand-ins of the first-run tests.

The judge runs only once the code checks pass, on the same attempt's output copy and "after"
service. A mismatch a fresh re-test confirms is a finding that explore owns, so the repair loop
sends it to explore, then to docs, and the checks and the judge run again.
"""

import json
from pathlib import Path

from pipeline_support import (
    THINGS_FACT,
    FakeAdapter,
    Rig,
    a_change_guide,
    a_first_run_script,
    a_prediction,
    a_section,
    an_inventory,
    an_overview,
    exploring,
    judging,
    judging_clean,
    submitting_the_profile,
    summarising,
)
from scripted_model import calling, tool_call

from arde.application.agents.judge import SYSTEM_PROMPT as JUDGE_PROMPT
from arde.application.pipeline import JUDGE_SKIPPED, run_pipeline
from arde.application.ports import HttpRequest, LLMRequest, LLMResponse
from arde.application.repair import REPAIR_HEADING
from arde.domain.facts import CodeQuote, Links, UnreachableFact
from arde.domain.inventory import ErrorCode
from arde.domain.report import ExitCode, RunReport

THINGS_CODE = CodeQuote(kind="code", file="main.go", quote="func things() {}")


def run(rig: Rig, *, skip_judge: bool = False) -> RunReport:
    request = rig.request().model_copy(update={"skip_judge": skip_judge})
    return run_pipeline(request, rig.deps())


def agent_of(request: LLMRequest) -> str:
    names = {tool.name for tool in request.tools}
    if "submit_profile" in names:
        return "discovery"
    if "add_facts" in names:
        return "explore"
    if "predict_and_call" in names:
        return "judge"
    if "predict" in names:
        return "retest"
    return "docs"


def agents_in_order(rig: Rig) -> list[str]:
    return [agent_of(request) for request in rig.llm.requests]


def first_message(request: LLMRequest) -> str:
    content = request.messages[0]["content"]
    assert isinstance(content, str)
    return content


def first_turns_of(rig: Rig, agent: str) -> list[LLMRequest]:
    return [r for r in rig.llm.requests if agent_of(r) == agent and len(r.messages) == 1]


def steps(rig: Rig) -> list[str]:
    return [str(fields["step"]) for fields in rig.tracer.events_of("step_start")]


# The stand-in service answers {"things":[]}, which holds no count.
def mispredicting() -> list[LLMResponse]:
    return judging(a_prediction("/things", count=0))


def a_fresh_prediction(status: int = 200, **fields: object) -> LLMResponse:
    return calling(tool_call("predict", status=status, fields=fields))


def a_first_run_up_to_the_judge() -> list[LLMResponse]:
    return [submitting_the_profile(), *exploring(), *summarising(THINGS_FACT.id)]


# A judge that predicts every reply right


def test_a_judge_that_predicts_everything_right_lets_the_run_commit(tmp_path: Path) -> None:
    rig = Rig(tmp_path=tmp_path, script=a_first_run_script(), kind="url")

    report = run(rig)

    assert (report.status, report.exit_code, report.findings) == ("success", ExitCode.OK, ())
    assert len(rig.source_control.commits) == 1
    assert agents_in_order(rig)[-2:] == ["judge", "judge"]
    assert steps(rig)[-4:] == ["checks", "judge", "commit", "save"]
    assert {"check": "judge", "findings": 0} in rig.tracer.events_of("check_result")
    assert len(rig.llm.requests) == len(rig.script)


def test_the_judge_reads_the_output_copys_pages_and_spec(tmp_path: Path) -> None:
    rig = Rig(tmp_path=tmp_path, script=a_first_run_script())

    report = run(rig)

    [judge] = first_turns_of(rig, "judge")
    assert judge.system == JUDGE_PROMPT
    message = first_message(judge)
    docs = Path(report.run_dir) / "output" / "docs"
    for page in ("overview.md", "api.md", "change-guide.md"):
        assert (docs / page).read_text() in message
    assert "The OpenAPI spec, docs/openapi.json:" in message
    assert "- route GET /things\n" in message


# The runner hands out ports in order: explore's service, then the attempt's before and after.
def test_the_judge_calls_the_after_service_of_the_attempt_it_judges(tmp_path: Path) -> None:
    rig = Rig(tmp_path=tmp_path, script=a_first_run_script())

    report = run(rig)

    after = rig.runner.configs[2]
    assert after.cwd == Path(report.run_dir) / "output"
    assert rig.service.requested[-1] == HttpRequest(method="GET", path_and_query="/things")
    assert rig.service.handles[-1].port == 5003


def test_the_judge_is_charged_to_its_role_and_its_prompt_is_hashed(tmp_path: Path) -> None:
    rig = Rig(tmp_path=tmp_path, script=a_first_run_script())

    report = run(rig)

    assert report.per_role["judge"].calls == 2
    [start] = rig.tracer.events_of("run_start")
    assert start["models"] == {
        "discovery": "claude-opus-5-5",
        "explore": "claude-opus-5-5",
        "docs": "claude-opus-5-5",
        "judge": "claude-opus-5-5",
    }
    hashes = start["prompt_sha256"]
    assert isinstance(hashes, dict)
    assert set(hashes) == {"discovery", "explore", "docs", "judge"}


# A mismatch


def test_a_mismatch_the_retest_clears_does_not_block(tmp_path: Path) -> None:
    script = [*a_first_run_up_to_the_judge(), *mispredicting(), a_fresh_prediction()]
    rig = Rig(tmp_path=tmp_path, script=script, kind="url")

    report = run(rig)

    assert (report.status, report.findings, report.repair_rounds) == ("success", (), ())
    assert len(rig.source_control.commits) == 1
    [noise] = rig.tracer.events_of("judge_noise")
    assert noise["request"] == "GET /things"
    assert agents_in_order(rig)[-3:] == ["judge", "judge", "retest"]


def a_confirmed_mismatch_then_a_repair() -> list[LLMResponse]:
    return [
        *a_first_run_up_to_the_judge(),
        *mispredicting(),
        a_fresh_prediction(count=0),
        *exploring(),
        *summarising(THINGS_FACT.id),
        *judging_clean(),
    ]


def test_confirmed_mismatch_routes_to_explore_then_docs(tmp_path: Path) -> None:
    rig = Rig(tmp_path=tmp_path, script=a_confirmed_mismatch_then_a_repair(), kind="url")

    report = run(rig)

    assert (report.status, report.findings) == ("success", ())
    [repaired] = report.repair_rounds
    assert [(f.check, f.owner, f.subject) for f in repaired.findings] == [
        ("judge", "explore", "GET /things")
    ]
    assert agents_in_order(rig) == [
        "discovery",
        *["explore"] * 3,
        *["docs"] * 2,
        *["judge"] * 2,
        "retest",
        *["explore"] * 3,
        *["docs"] * 2,
        *["judge"] * 2,
    ]
    assert rig.tracer.events_of("repair") == [
        {"round": 1, "owners": ["explore", "docs"], "findings": 1}
    ]
    _, explore_repair = first_turns_of(rig, "explore")
    [finding] = json.loads(first_message(explore_repair).rsplit("\n\n", 1)[1])
    assert (finding["check"], finding["subject"]) == ("judge", "GET /things")
    assert "count = 0" in finding["message"]
    assert all(REPAIR_HEADING in first_message(r) for r in first_turns_of(rig, "docs")[2:])
    assert len(rig.source_control.commits) == 1


def test_the_next_attempts_judge_must_send_the_request_again(tmp_path: Path) -> None:
    rig = Rig(tmp_path=tmp_path, script=a_confirmed_mismatch_then_a_repair())

    run(rig)

    first, second = first_turns_of(rig, "judge")
    assert "- the request GET /things\n" not in first_message(first)
    assert "- the request GET /things\n" in first_message(second)


def test_a_mismatch_confirmed_again_after_the_last_round_fails_the_run(tmp_path: Path) -> None:
    script = [
        *a_first_run_up_to_the_judge(),
        *mispredicting(),
        a_fresh_prediction(count=0),
        *exploring(),
        *summarising(THINGS_FACT.id),
        *mispredicting(),
        a_fresh_prediction(count=0),
    ]
    rig = Rig(tmp_path=tmp_path, script=script, kind="url")

    report = run(rig)

    assert (report.status, report.exit_code) == ("failed", ExitCode.CHECKS_FAILED)
    assert [(f.check, f.subject) for f in report.findings] == [("judge", "GET /things")]
    assert rig.source_control.commits == []
    assert not rig.store.wiki_dir.exists()
    assert len(rig.llm.requests) == len(script)


# Where the judge does not run


def test_the_judge_does_not_run_while_a_code_check_finds_something(tmp_path: Path) -> None:
    # Nothing it cites was probed, so the live contract finds it on every attempt.
    unprobed = THINGS_FACT.model_copy(update={"evidence": (THINGS_CODE,), "responses": ()})
    attempt = [*exploring(unprobed), *summarising(unprobed.id)]
    rig = Rig(tmp_path=tmp_path, script=[submitting_the_profile(), *attempt, *attempt])

    report = run(rig)

    assert report.status == "failed"
    assert "judge" not in agents_in_order(rig)
    assert "judge" not in steps(rig)


def test_skip_judge_warns_and_commits(tmp_path: Path) -> None:
    rig = Rig(tmp_path=tmp_path, script=a_first_run_up_to_the_judge(), kind="url")

    report = run(rig, skip_judge=True)

    assert (report.status, report.exit_code) == ("success", ExitCode.OK)
    assert JUDGE_SKIPPED in report.warnings
    assert JUDGE_SKIPPED.startswith("The blind judge did not run")
    assert "judge" not in steps(rig)
    assert len(rig.source_control.commits) == 1


def test_a_rerun_that_changed_no_fact_a_request_can_test_runs_no_judge(tmp_path: Path) -> None:
    assert run(Rig(tmp_path=tmp_path, script=a_first_run_script())).status == "success"
    second = Rig(tmp_path=tmp_path, script=exploring())

    report = run(second)

    assert report.status == "success"
    assert "judge" not in agents_in_order(second)
    assert second.tracer.events_of("judge_skipped") != []


# Exemptions

UNREACHABLE = UnreachableFact(
    id="unreachable.internal",
    topic="errors",
    kind="unreachable",
    evidence=(THINGS_CODE,),
    links=Links(declarations=("main.go::func things",)),
    error_code="internal",
    reason="Nothing in the service fails.",
)


def test_exemptions_listed_in_run_report(tmp_path: Path) -> None:
    script = [
        submitting_the_profile(),
        *exploring(THINGS_FACT, UNREACHABLE),
        an_overview(THINGS_FACT.id),
        a_section("Nothing in the service fails.", UNREACHABLE.id),
        a_change_guide(THINGS_FACT.id),
        *judging_clean(),
    ]
    inventory = an_inventory(error_codes=(ErrorCode(code="internal", status="500"),))
    rig = Rig(tmp_path=tmp_path, script=script, adapter=FakeAdapter(inventory=inventory))

    report = run(rig)

    assert (report.status, report.findings) == ("success", ())
    assert report.exemptions == (
        "error code internal: no request reaches it, as fact unreachable.internal says: Nothing "
        "in the service fails.",
    )
    assert "- error code internal\n" not in first_message(first_turns_of(rig, "judge")[0])
    assert len(rig.llm.requests) == len(script)

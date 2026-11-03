"""The command line, run in this process with stand-ins for the language, the service and the model.

Resolving the source, copying it and committing in the workspace clone are the real ones, on git
repos made here, so a URL run's commit is a real commit.
"""

import io
import json
import os
from dataclasses import dataclass, field, replace
from pathlib import Path

import pytest
from pipeline_support import (
    THINGS_FACT,
    FakeAdapter,
    FakeRunner,
    FakeService,
    FakeToolbox,
    a_first_run_script,
    a_git_repo,
    exploring,
    git,
    submitting_the_profile,
    summarising,
)

from arde.adapters.llm.fake import FakeLLM
from arde.application.ports import LLMClient, LLMRequest, LLMResponse, ProviderError, Tracer
from arde.cli import LOCAL_WIRING, Wiring, parse_arguments, run
from arde.domain.report import ExitCode, RunReport

KEY = "sk-test-not-a-real-key-0123456789"


@dataclass
class Outputs:
    out: io.StringIO = field(default_factory=io.StringIO)
    err: io.StringIO = field(default_factory=io.StringIO)


@dataclass
class Stand_ins:
    script: list[LLMResponse] = field(default_factory=a_first_run_script)
    adapter: FakeAdapter = field(default_factory=FakeAdapter)
    service: FakeService = field(default_factory=FakeService)
    clients: list[LLMClient] = field(default_factory=list)
    adapters_built: int = 0

    def wiring(self) -> Wiring:
        return replace(
            LOCAL_WIRING,
            language_adapter=self._language_adapter,
            model_client=self._model_client,
            service_runner=FakeRunner,
            service_client=lambda: self.service,
            toolbox=lambda: FakeToolbox(self.service),
        )

    def _language_adapter(self, bin_dir: Path, *, cache_dir: Path) -> FakeAdapter:
        self.adapters_built += 1
        return self.adapter

    def _model_client(self, key: object, tracer: Tracer) -> LLMClient:
        client = FakeLLM(self.script)
        self.clients.append(client)
        return client


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "arde-home"
    monkeypatch.setenv("ARDE_HOME", str(home))
    monkeypatch.setenv("ANTHROPIC_API_KEY", KEY)
    return home


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    return a_git_repo(tmp_path / "things")


def run_arde(*argv: str, stand_ins: Stand_ins | None = None) -> tuple[int, Outputs]:
    outputs = Outputs()
    code = run(list(argv), (stand_ins or Stand_ins()).wiring(), out=outputs.out, err=outputs.err)
    return code, outputs


def everything_under(folder: Path) -> str:
    texts = [
        path.read_bytes().decode("utf-8", errors="replace")
        for path in folder.rglob("*")
        if path.is_file() and not path.is_symlink()
    ]
    return "\n".join(texts)


# Bad input stops before any work


def test_missing_api_key_exits_2_before_any_work(
    home: Path, repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    stand_ins = Stand_ins()

    code, outputs = run_arde("--repo", str(repo), stand_ins=stand_ins)

    assert code == ExitCode.BAD_USAGE
    assert "ANTHROPIC_API_KEY" in outputs.err.getvalue()
    assert "uv run --env-file .env arde" in outputs.err.getvalue()
    assert (stand_ins.adapters_built, stand_ins.clients) == (0, [])
    assert not home.exists()


def test_a_blank_api_key_counts_as_missing(
    home: Path, repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "  \n")

    code, _ = run_arde("--repo", str(repo))

    assert code == ExitCode.BAD_USAGE


def test_unknown_flag_exits_2(home: Path) -> None:
    code, outputs = run_arde("--repo", "x", "--frobnicate")

    assert code == ExitCode.BAD_USAGE
    assert "--frobnicate" in outputs.err.getvalue()
    assert outputs.out.getvalue() == ""


def test_no_repo_exits_2(home: Path) -> None:
    code, outputs = run_arde()

    assert code == ExitCode.BAD_USAGE
    assert "--repo" in outputs.err.getvalue()


def test_help_exits_0_on_stdout(home: Path) -> None:
    code, outputs = run_arde("--help")

    assert code == ExitCode.OK
    assert "--max-usd" in outputs.out.getvalue()


def test_the_cost_cap_defaults_to_10_dollars() -> None:
    assert parse_arguments(["--repo", "x"]).max_usd == 10


def test_the_judge_runs_unless_skip_judge_is_given() -> None:
    assert parse_arguments(["--repo", "x"]).skip_judge is False
    assert parse_arguments(["--repo", "x", "--skip-judge"]).skip_judge is True


@pytest.mark.parametrize("cap", ["0", "-1", "nan", "inf"])
def test_a_cost_cap_that_is_not_a_positive_number_exits_2(home: Path, repo: Path, cap: str) -> None:
    stand_ins = Stand_ins()

    code, outputs = run_arde("--repo", str(repo), "--max-usd", cap, stand_ins=stand_ins)

    assert code == ExitCode.BAD_USAGE
    assert "--max-usd" in outputs.err.getvalue()
    assert stand_ins.clients == []


def test_a_folder_that_is_no_git_repo_exits_2_with_no_model_call(
    home: Path, tmp_path: Path
) -> None:
    folder = tmp_path / "plain"
    folder.mkdir()
    stand_ins = Stand_ins()

    code, outputs = run_arde("--repo", str(folder), stand_ins=stand_ins)

    assert code == ExitCode.BAD_USAGE
    assert "not a git repo" in outputs.err.getvalue()
    assert stand_ins.clients == []


def test_branch_with_a_local_path_exits_2(home: Path, repo: Path) -> None:
    code, outputs = run_arde("--repo", str(repo), "--branch", "main")

    assert code == ExitCode.BAD_USAGE
    assert "--branch" in outputs.err.getvalue()


def test_a_repo_with_a_link_out_stops_with_no_target_content_in_any_output(
    home: Path, tmp_path: Path
) -> None:
    outside = tmp_path / "outside.txt"
    outside.write_text("TARGET-ONLY-TEXT module example.com/secret\n", encoding="utf-8")
    repo = a_git_repo(tmp_path / "linked")
    (repo / "go.mod").symlink_to(outside)
    git(repo, "add", "--all")
    git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "--quiet", "-m", "link")

    code, outputs = run_arde("--repo", str(repo), "--json")

    assert code == ExitCode.BAD_USAGE
    printed = outputs.out.getvalue() + outputs.err.getvalue()
    assert "go.mod" in printed
    assert "TARGET-ONLY-TEXT" not in printed
    assert str(outside) not in printed
    assert not home.exists() or "TARGET-ONLY-TEXT" not in everything_under(home)


def test_with_json_an_early_failure_prints_a_report_on_stdout(home: Path, tmp_path: Path) -> None:
    code, outputs = run_arde("--repo", str(tmp_path / "missing"), "--json")

    report = RunReport.model_validate_json(outputs.out.getvalue())
    assert (code, report.exit_code, report.status) == (2, ExitCode.BAD_USAGE, "failed")
    assert report.run_dir == ""


# A whole run


def test_a_local_run_succeeds_and_says_where_the_output_is(home: Path, repo: Path) -> None:
    code, outputs = run_arde("--repo", str(repo))

    assert code == ExitCode.OK, outputs.err.getvalue()
    assert "output" in outputs.out.getvalue()


def test_a_run_with_skip_judge_calls_no_judge_and_says_so(home: Path, repo: Path) -> None:
    script = [submitting_the_profile(), *exploring(), *summarising(THINGS_FACT.id)]
    stand_ins = Stand_ins(script=script)

    code, outputs = run_arde("--repo", str(repo), "--json", "--skip-judge", stand_ins=stand_ins)

    report = RunReport.model_validate_json(outputs.out.getvalue())
    assert (code, report.status) == (ExitCode.OK, "success"), report.message
    assert any(warning.startswith("The blind judge did not run") for warning in report.warnings)


def test_json_flag_prints_only_runreport_on_stdout(home: Path, repo: Path) -> None:
    code, outputs = run_arde("--repo", str(repo), "--json")

    report = RunReport.model_validate_json(outputs.out.getvalue())
    assert (code, report.status) == (ExitCode.OK, "success")


def test_progress_goes_to_stderr(home: Path, repo: Path) -> None:
    _, outputs = run_arde("--repo", str(repo))

    err = outputs.err.getvalue()
    assert "arde: explore" in err
    assert "arde: run folder" in err
    assert "arde: explore" not in outputs.out.getvalue()


def test_report_json_saved_in_run_dir(home: Path, repo: Path) -> None:
    _, outputs = run_arde("--repo", str(repo), "--json")

    printed = RunReport.model_validate_json(outputs.out.getvalue())
    saved = RunReport.model_validate_json((Path(printed.run_dir) / "report.json").read_text())
    assert saved == printed


def test_the_trace_is_in_the_run_folder(home: Path, repo: Path) -> None:
    _, outputs = run_arde("--repo", str(repo), "--json")

    run_dir = Path(RunReport.model_validate_json(outputs.out.getvalue()).run_dir)
    kinds = [json.loads(line)["kind"] for line in (run_dir / "trace" / "trace.jsonl").open()]
    assert (kinds[0], kinds[-1]) == ("run_start", "run_end")


def test_url_run_commits_on_new_branch_in_workspace_only(home: Path, repo: Path) -> None:
    before = git(repo, "rev-parse", "HEAD").strip()
    branches_before = git(repo, "branch", "--list")

    code, outputs = run_arde("--repo", repo.as_uri(), "--json")

    report = RunReport.model_validate_json(outputs.out.getvalue())
    assert code == ExitCode.OK, report.message
    clone = Path(report.result_dir)
    assert report.branch is not None
    log = git(clone, "log", "--format=%H %an %s", f"{before}..{report.branch}").splitlines()
    assert len(log) == 1
    commit, author, *_ = log[0].split()
    assert (commit, author) == (report.commit, "ARDE")
    committed = git(clone, "show", "--name-only", "--format=", report.branch).split()
    assert sorted(committed) == list(report.changed_paths)
    assert (git(repo, "rev-parse", "HEAD").strip(), git(repo, "branch", "--list")) == (
        before,
        branches_before,
    )
    assert not (repo / "docs").exists()


# The key and the terminal


class _FailingWithText:
    def __init__(self, message: str) -> None:
        self._message = message

    def complete(self, request: LLMRequest) -> LLMResponse:
        raise ProviderError(self._message, next_action=f"Retry. {self._message}")

    def parse(self, request: LLMRequest, schema: object) -> object:
        raise NotImplementedError


def failing_with(message: str) -> Stand_ins:
    stand_ins = Stand_ins()
    stand_ins._model_client = lambda key, tracer: _FailingWithText(message)  # type: ignore[method-assign, assignment, return-value]
    return stand_ins


def test_the_key_reaches_no_report_trace_or_output(home: Path, repo: Path) -> None:
    code, outputs = run_arde("--repo", str(repo), stand_ins=failing_with(f"bad key {KEY}"))

    assert code == ExitCode.CHECKS_FAILED
    printed = outputs.out.getvalue() + outputs.err.getvalue()
    assert "bad key [REDACTED]" in printed
    assert KEY not in printed
    assert KEY not in everything_under(home)


def test_printed_text_shows_no_control_character(home: Path, repo: Path) -> None:
    code, outputs = run_arde("--repo", str(repo), stand_ins=failing_with("cleared \x1b[2J here"))

    printed = outputs.out.getvalue() + outputs.err.getvalue()
    assert code == ExitCode.CHECKS_FAILED
    assert "\x1b" not in printed
    assert "cleared \\x1b[2J here" in printed


def test_an_interrupt_exits_130_and_keeps_the_trace(home: Path, repo: Path) -> None:
    stand_ins = Stand_ins()

    class Interrupting:
        def complete(self, request: LLMRequest) -> LLMResponse:
            error = KeyboardInterrupt()
            error.add_note("The service on port 5001 would not stop.")
            raise error

    stand_ins._model_client = lambda key, tracer: Interrupting()  # type: ignore[method-assign, assignment, return-value]

    code, outputs = run_arde("--repo", str(repo), stand_ins=stand_ins)

    assert code == 130
    assert "interrupted" in outputs.err.getvalue()
    assert "The service on port 5001 would not stop." in outputs.err.getvalue()
    [trace] = list(home.rglob("trace.jsonl"))
    assert json.loads(trace.read_text().splitlines()[-1])["status"] == "interrupted"


def test_the_signals_become_interrupts_only_while_arde_runs() -> None:
    import signal

    from arde.cli import signals_as_interrupts

    before = signal.getsignal(signal.SIGTERM)
    with signals_as_interrupts():
        assert signal.getsignal(signal.SIGTERM) is signal.default_int_handler
        assert signal.getsignal(signal.SIGHUP) is signal.default_int_handler
    assert signal.getsignal(signal.SIGTERM) == before


def test_the_entry_point_is_arde_cli_main() -> None:
    import tomllib

    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    scripts = tomllib.loads(pyproject.read_text())["project"]["scripts"]
    assert scripts == {"arde": "arde.cli:main"}
    assert os.access(pyproject, os.R_OK)

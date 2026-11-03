"""Each repo's Go commands keep their caches in that repo's own workspace.

One cache shared by every repo, as a single volume holds them in the container, would let one repo's
build or tests write entries that another repo's commands then read. The cli runs here as a person
runs it, with the real Go adapter; a stand-in plays go and records the caches each command got, and
stand-ins play the prebuilt tools. Every run stops at the baseline, before any model call.
"""

import io
import stat
from dataclasses import replace
from pathlib import Path

import pytest
from pipeline_support import git

from arde.adapters.language.go import go_environment
from arde.adapters.language.go.prebuilt import GOINSPECT_VARIABLE, SWAG_VARIABLE
from arde.adapters.llm.fake import FakeLLM
from arde.application.ports import LLMClient, Tracer
from arde.cli import LOCAL_WIRING, run
from arde.domain.report import ExitCode

# Answers the questions ARDE asks the toolchain itself, and records and fails every command it would
# run on a repo, which ends the run at the baseline.
STAND_IN_GO = """#!/bin/sh
case "$1 $2" in
  "env -json") echo '{"GOPRIVATE": "", "GONOPROXY": "", "GONOSUMDB": ""}'; exit 0 ;;
  "env GOVERSION") echo go1.27.1; exit 0 ;;
  "version -m") exit 0 ;;
esac
printf '%s %s|%s\\n' "$1" "$GOCACHE" "$GOPATH" >> "$(dirname "$0")/commands.log"
echo "stand-in go refuses $1" >&2
exit 1
"""


def an_executable(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


def a_go_repo(folder: Path) -> Path:
    folder.mkdir(parents=True)
    (folder / "go.mod").write_text(f"module example.com/{folder.name}\n\ngo 1.22\n")
    (folder / "main.go").write_text("package main\n\nfunc main() {}\n")
    git(folder, "init", "--quiet")
    git(folder, "add", "--all")
    git(folder, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "--quiet", "-m", "first")
    return folder


@pytest.fixture
def stand_in_go(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    tools = tmp_path / "tools"
    an_executable(tools / "go", STAND_IN_GO)
    monkeypatch.setenv(GOINSPECT_VARIABLE, str(an_executable(tools / "goinspect", "#!/bin/sh\n")))
    monkeypatch.setenv(SWAG_VARIABLE, str(an_executable(tools / "swag", "#!/bin/sh\n")))
    monkeypatch.setenv("PATH", f"{tools}:/usr/bin:/bin")
    monkeypatch.setenv("ARDE_HOME", str(tmp_path / "arde-home"))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-not-a-real-key-0123456789")
    # What the image exports, which the repo's caches must win over.
    monkeypatch.setenv("GOCACHE", str(tmp_path / "shared" / "gocache"))
    monkeypatch.setenv("GOPATH", str(tmp_path / "shared" / "gopath"))
    go_environment._user_settings.cache_clear()
    yield tools / "commands.log"
    go_environment._user_settings.cache_clear()


def scripted(key: object, tracer: Tracer) -> LLMClient:
    return FakeLLM([])


def caches_of_a_run_on(repo: Path, commands: Path) -> set[str]:
    commands.unlink(missing_ok=True)
    out, err = io.StringIO(), io.StringIO()

    code = run(
        ["--repo", str(repo), "--json"],
        replace(LOCAL_WIRING, model_client=scripted),
        out=out,
        err=err,
    )

    assert code == ExitCode.CHECKS_FAILED, out.getvalue() + err.getvalue()
    return {line.split(" ", 1)[1] for line in commands.read_text().splitlines()}


def test_two_repos_runs_use_two_caches_each_inside_its_own_workspace(
    stand_in_go: Path, tmp_path: Path
) -> None:
    first = caches_of_a_run_on(a_go_repo(tmp_path / "first"), stand_in_go)
    second = caches_of_a_run_on(a_go_repo(tmp_path / "second"), stand_in_go)

    workspaces = tmp_path / "arde-home" / "workspaces"
    for caches, name in ((first, "first"), (second, "second")):
        [only] = caches
        build, path = only.split("|")
        [workspace] = workspaces.glob(f"{name}-*")
        assert Path(build) == workspace / "cache" / "gocache"
        assert Path(path) == workspace / "cache" / "gopath"
    assert first != second


def test_a_second_run_on_one_repo_uses_the_same_caches(stand_in_go: Path, tmp_path: Path) -> None:
    repo = a_go_repo(tmp_path / "only")

    assert caches_of_a_run_on(repo, stand_in_go) == caches_of_a_run_on(repo, stand_in_go)

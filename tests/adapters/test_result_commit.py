"""The commit of a run's result in the workspace clone: exactly the paths given, on a new branch,
as ARDE, whatever the user's own git settings say."""

import shutil
import subprocess
from pathlib import Path

import pytest

from arde.adapters.source import commit_result
from arde.application.ports import ResultCommit
from arde.domain.errors import SourceError


def git(repo: Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", *arguments], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()


@pytest.fixture
def clone(tmp_path: Path) -> Path:
    clone = tmp_path / "clone"
    clone.mkdir()
    (clone / "main.go").write_text("package main\n")
    (clone / "old.md").write_text("old\n")
    git(clone, "init", "--quiet")
    git(clone, "add", "--all")
    git(clone, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "--quiet", "-m", "first")
    return clone


@pytest.fixture
def output(tmp_path: Path, clone: Path) -> Path:
    output = tmp_path / "output"
    shutil.copytree(clone, output, ignore=shutil.ignore_patterns(".git"))
    (output / "main.go").write_text("// main starts the service.\npackage main\n")
    (output / "docs").mkdir()
    (output / "docs" / "api.md").write_text("# API\n")
    (output / "old.md").unlink()
    (output / "untouched.txt").write_text("written by the service, not by ARDE\n")
    return output


def a_commit(clone: Path, output: Path, *paths: str) -> ResultCommit:
    return ResultCommit(
        clone=clone, output=output, paths=paths, branch="arde/run-1", message="docs: the result"
    )


def test_the_paths_are_committed_on_a_new_branch_at_the_cloned_commit(
    clone: Path, output: Path
) -> None:
    first = git(clone, "rev-parse", "HEAD")

    commit = commit_result(a_commit(clone, output, "docs/api.md", "main.go", "old.md"))

    assert git(clone, "rev-parse", "HEAD") == commit
    assert git(clone, "rev-parse", "arde/run-1~1") == first
    last = git(clone, "log", "-1", "--format=%an <%ae> %s")
    assert last == "ARDE <arde@localhost> docs: the result"
    changed = git(clone, "show", "--name-status", "--format=", "arde/run-1").splitlines()
    assert sorted(changed) == ["A\tdocs/api.md", "D\told.md", "M\tmain.go"]
    assert git(clone, "status", "--porcelain") == ""


def test_a_file_that_is_not_one_of_the_paths_stays_out(clone: Path, output: Path) -> None:
    commit_result(a_commit(clone, output, "main.go"))

    assert not (clone / "untouched.txt").exists()
    assert (clone / "old.md").exists()


def test_a_path_behind_a_link_in_the_clone_is_refused(clone: Path, output: Path) -> None:
    (clone / "web").mkdir()
    (clone / "docs").symlink_to("web")

    with pytest.raises(SourceError, match="behind a link"):
        commit_result(a_commit(clone, output, "docs/api.md"))

    assert not (clone / "web" / "api.md").exists()


def test_a_path_that_leaves_the_clone_is_refused(clone: Path, output: Path) -> None:
    with pytest.raises(SourceError):
        commit_result(a_commit(clone, output, "../escaped.md"))


def test_the_users_hooks_and_signing_settings_do_not_apply(
    clone: Path, output: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    hooks = home / "hooks"
    hooks.mkdir(parents=True)
    marker = tmp_path / "hook-ran"
    (hooks / "pre-commit").write_text(f"#!/bin/sh\ntouch {marker}\n")
    (hooks / "pre-commit").chmod(0o755)
    (home / ".gitconfig").write_text(
        f"[core]\n\thooksPath = {hooks}\n[commit]\n\tgpgsign = true\n[gpg]\n\tprogram = false\n"
        "[user]\n\tname = Someone Else\n\temail = someone@example.com\n"
    )
    monkeypatch.setenv("HOME", str(home))

    commit_result(a_commit(clone, output, "main.go"))

    assert not marker.exists()
    assert git(clone, "log", "-1", "--format=%an") == "ARDE"


def test_no_path_still_makes_one_commit(clone: Path, output: Path) -> None:
    first = git(clone, "rev-parse", "HEAD")

    commit_result(a_commit(clone, output))

    assert git(clone, "rev-list", "--count", f"{first}..arde/run-1") == "1"

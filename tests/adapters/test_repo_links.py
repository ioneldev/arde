"""The gate that keeps a repo's links from reaching files outside it.

Go commands quote a broken go.mod in their errors, and ARDE reads a Makefile, so a link in a repo
can carry any file the user can read into ARDE's output, with no repo code running.
"""

import json
import os
from pathlib import Path

import pytest
from tree_support import a_tree_too_deep_for_one_path

from arde.adapters.language.go.adapter import GoAdapter
from arde.adapters.repo_links import (
    LinkLeavesRepoError,
    RepoNotReadableError,
    links_leaving,
    require_no_link_leaves,
)
from arde.domain.errors import ArdeError
from arde.domain.profile import RepoProfile
from arde.domain.report import ExitCode

FAKE_SECRET = "FAKE_SECRET_VALUE_FOR_THE_TEST"


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    folder = tmp_path / "repo"
    folder.mkdir()
    return folder


@pytest.fixture
def secret(tmp_path: Path) -> Path:
    file = tmp_path / "private" / "secret_file"
    file.parent.mkdir()
    file.write_text(f"KEY={FAKE_SECRET}\n", encoding="utf-8")
    return file


def test_a_repo_with_no_links_has_none_leaving(repo: Path) -> None:
    (repo / "main.go").write_text("package main\n", encoding="utf-8")

    assert links_leaving(repo) == []


def test_a_link_to_a_file_outside_is_listed(repo: Path, secret: Path) -> None:
    (repo / "go.mod").symlink_to(secret)

    assert links_leaving(repo) == ["go.mod"]


def test_a_relative_link_that_climbs_out_is_listed(repo: Path, secret: Path) -> None:
    (repo / "pkg").mkdir()
    (repo / "pkg" / "leak.go").symlink_to(Path("..") / ".." / "private" / secret.name)

    assert links_leaving(repo) == ["pkg/leak.go"]


def test_a_link_to_a_folder_outside_is_listed_and_not_walked(repo: Path, secret: Path) -> None:
    (repo / "pkg").symlink_to(secret.parent)

    assert links_leaving(repo) == ["pkg"]


def test_a_broken_link_that_points_outside_is_listed(repo: Path, tmp_path: Path) -> None:
    (repo / "go.work").symlink_to(tmp_path / "not-there-yet")

    assert links_leaving(repo) == ["go.work"]


def test_a_link_through_another_link_is_judged_by_where_it_ends(repo: Path, secret: Path) -> None:
    (repo / "first").symlink_to("second")
    (repo / "second").symlink_to(secret)

    assert links_leaving(repo) == ["first", "second"]


def test_a_folder_beside_the_repo_whose_name_starts_the_same_is_outside(
    repo: Path, tmp_path: Path
) -> None:
    neighbour = tmp_path / "repo-secrets"
    neighbour.mkdir()
    (neighbour / "key").write_text(FAKE_SECRET, encoding="utf-8")
    (repo / "leak.go").symlink_to(neighbour / "key")

    assert links_leaving(repo) == ["leak.go"]


def test_a_link_to_a_file_inside_is_kept(repo: Path) -> None:
    (repo / "src_real").mkdir()
    (repo / "src_real" / "extra.txt").write_text("package main\n", encoding="utf-8")
    (repo / "extra.go").symlink_to(Path("src_real") / "extra.txt")

    assert links_leaving(repo) == []


def test_a_link_to_the_root_itself_stays_inside(repo: Path) -> None:
    (repo / "same").symlink_to(".")

    assert links_leaving(repo) == []


def test_a_broken_link_that_stays_inside_is_kept(repo: Path) -> None:
    (repo / "gone.go").symlink_to("not-there")

    assert links_leaving(repo) == []


def test_a_link_that_loops_stays_inside(repo: Path) -> None:
    (repo / "a").symlink_to("b")
    (repo / "b").symlink_to("a")

    assert links_leaving(repo) == []


def test_a_repo_reached_through_a_link_is_judged_from_its_real_folder(
    repo: Path, tmp_path: Path, secret: Path
) -> None:
    (repo / "inside").write_text("x", encoding="utf-8")
    (repo / "fine").symlink_to("inside")
    (repo / "leak").symlink_to(secret)
    entrance = tmp_path / "entrance"
    entrance.symlink_to(repo)

    assert links_leaving(entrance) == ["leak"]


# A go.work can name a module inside .git, and Go quotes that module's go.mod if it cannot parse it.
def test_a_link_inside_the_git_folder_that_leaves_is_listed(repo: Path, secret: Path) -> None:
    (repo / ".git" / "hooks").mkdir(parents=True)
    (repo / ".git" / "hooks" / "pre-commit").symlink_to(secret)

    assert links_leaving(repo) == [".git/hooks/pre-commit"]


def test_a_link_inside_a_nested_git_folder_that_leaves_is_listed(repo: Path, secret: Path) -> None:
    (repo / "sub" / ".git" / "dep").mkdir(parents=True)
    (repo / "sub" / ".git" / "dep" / "go.mod").symlink_to(secret)

    assert links_leaving(repo) == ["sub/.git/dep/go.mod"]


def test_a_link_inside_the_git_folder_that_stays_inside_is_kept(repo: Path) -> None:
    (repo / "scripts").mkdir()
    (repo / "scripts" / "pre-commit").write_text("#!/bin/sh\n", encoding="utf-8")
    (repo / ".git" / "hooks").mkdir(parents=True)
    (repo / ".git" / "hooks" / "pre-commit").symlink_to(
        Path("..") / ".." / "scripts" / "pre-commit"
    )

    assert links_leaving(repo) == []


def test_a_link_named_git_is_still_a_link(repo: Path, secret: Path) -> None:
    (repo / ".git").symlink_to(secret.parent)

    assert links_leaving(repo) == [".git"]


def test_a_link_in_a_folder_the_go_tool_passes_over_is_still_listed(
    repo: Path, secret: Path
) -> None:
    (repo / "vendor" / "lib").mkdir(parents=True)
    (repo / "vendor" / "lib" / "go.mod").symlink_to(secret)

    assert links_leaving(repo) == ["vendor/lib/go.mod"]


def test_the_list_is_sorted(repo: Path, secret: Path) -> None:
    for name in ("z.go", "a.go", "m.go"):
        (repo / name).symlink_to(secret)

    assert links_leaving(repo) == ["a.go", "m.go", "z.go"]


def test_the_gate_lets_a_clean_repo_through(repo: Path) -> None:
    (repo / "a.go").write_text("package a\n", encoding="utf-8")

    require_no_link_leaves(repo)


def test_the_gate_names_the_link_and_the_next_action_and_exits_two(
    repo: Path, secret: Path
) -> None:
    (repo / "go.mod").symlink_to(secret)

    with pytest.raises(LinkLeavesRepoError) as stopped:
        require_no_link_leaves(repo)

    assert isinstance(stopped.value, ArdeError)
    assert stopped.value.exit_code == ExitCode.BAD_USAGE == 2
    assert "go.mod" in str(stopped.value)
    assert "link" in stopped.value.next_action


def test_the_message_never_quotes_where_a_link_points(repo: Path, secret: Path) -> None:
    (repo / "go.mod").symlink_to(secret)
    (repo / "relative").symlink_to(Path("..") / "private" / secret.name)

    with pytest.raises(LinkLeavesRepoError) as stopped:
        require_no_link_leaves(repo)

    message = f"{stopped.value} {stopped.value.next_action}"
    assert secret.name not in message
    assert "private" not in message
    assert FAKE_SECRET not in message


def test_a_link_name_with_control_characters_is_printed_safely(repo: Path, secret: Path) -> None:
    (repo / "e\x1b[31mvil\n.go").symlink_to(secret)

    with pytest.raises(LinkLeavesRepoError) as stopped:
        require_no_link_leaves(repo)

    assert "\x1b" not in str(stopped.value)
    assert "\n" not in str(stopped.value)
    assert json.dumps("e\x1b[31mvil\n.go")[1:-1] in str(stopped.value)


def test_a_repo_with_thousands_of_links_gets_a_short_message_with_the_full_count(
    repo: Path, secret: Path
) -> None:
    for number in range(3000):
        (repo / f"link{number:04d}.go").symlink_to(secret)

    with pytest.raises(LinkLeavesRepoError) as stopped:
        require_no_link_leaves(repo)

    assert "3000" in str(stopped.value)
    assert "link0000.go" in str(stopped.value)
    assert "link2999.go" not in str(stopped.value)
    assert len(str(stopped.value)) < 1000


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads every folder")
def test_a_folder_that_cannot_be_read_stops_the_gate_and_is_not_passed_over(repo: Path) -> None:
    locked = repo / "locked"
    locked.mkdir()
    locked.chmod(0o000)
    try:
        with pytest.raises(RepoNotReadableError) as stopped:
            require_no_link_leaves(repo)
    finally:
        locked.chmod(0o755)

    assert isinstance(stopped.value, ArdeError)
    assert stopped.value.exit_code == ExitCode.BAD_USAGE
    assert stopped.value.next_action


def test_a_tree_too_deep_for_one_path_stops_the_gate_because_it_could_hide_a_link(
    repo: Path,
) -> None:
    a_tree_too_deep_for_one_path(repo)

    with pytest.raises(RepoNotReadableError) as stopped:
        require_no_link_leaves(repo)

    assert stopped.value.next_action


def test_a_repo_that_is_not_there_stops_the_gate(tmp_path: Path) -> None:
    with pytest.raises(RepoNotReadableError):
        require_no_link_leaves(tmp_path / "not-there")


def a_profile() -> RepoProfile:
    return RepoProfile(main_package=".", main_file="main.go", port_env="PORT", patterns=())


REPO_METHODS = (
    "detects",
    "changed_declarations old",
    "changed_declarations new",
    "extract_inventory",
    "route_fingerprints",
    "run_checks",
    "built_service",
)


# The helper's path is never used: the gate stops each method before anything runs.
def every_repo_method(repo: Path) -> dict[str, object]:
    adapter = GoAdapter(Path("unused"))
    return {
        "detects": lambda: adapter.detects(repo),
        "changed_declarations old": lambda: adapter.changed_declarations(repo, repo.parent),
        "changed_declarations new": lambda: adapter.changed_declarations(repo.parent, repo),
        "extract_inventory": lambda: adapter.extract_inventory(repo, a_profile()),
        "route_fingerprints": lambda: adapter.route_fingerprints(repo, (), main_package="."),
        "run_checks": lambda: adapter.run_checks(repo, a_profile()),
        "built_service": lambda: adapter.built_service(repo, a_profile()),
    }


@pytest.mark.parametrize("method", REPO_METHODS)
def test_every_adapter_method_that_takes_a_repo_stops_at_a_link_that_leaves_it(
    repo: Path, secret: Path, method: str
) -> None:
    (repo / "go.mod").symlink_to(secret)

    with pytest.raises(LinkLeavesRepoError) as stopped:
        every_repo_method(repo)[method]()  # type: ignore[operator]

    assert FAKE_SECRET not in str(stopped.value)


@pytest.mark.parametrize("method", REPO_METHODS)
def test_every_adapter_method_stops_at_a_link_inside_the_git_folder_that_leaves_the_repo(
    repo: Path, secret: Path, method: str
) -> None:
    (repo / ".git" / "dep").mkdir(parents=True)
    (repo / ".git" / "dep" / "go.mod").symlink_to(secret)

    with pytest.raises(LinkLeavesRepoError) as stopped:
        every_repo_method(repo)[method]()  # type: ignore[operator]

    assert ".git/dep/go.mod" in str(stopped.value)
    assert FAKE_SECRET not in str(stopped.value)

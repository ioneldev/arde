"""The folder ARDE keeps for one target repo. The snapshot in it decides what the next run does."""

import os
from pathlib import Path

import pytest

from arde.adapters import workspace as workspace_module
from arde.adapters.workspace import Workspace, WorkspaceError, arde_home
from arde.application.ports import RunStore
from arde.domain.errors import ArdeError
from arde.domain.report import ExitCode


@pytest.fixture
def workspace(tmp_path: Path) -> Workspace:
    return Workspace(tmp_path / "home", "shop-0123456789ab")


def a_tree(folder: Path, files: dict[str, str]) -> Path:
    for name, text in files.items():
        (folder / name).parent.mkdir(parents=True, exist_ok=True)
        (folder / name).write_text(text, encoding="utf-8")
    return folder


def files_in(folder: Path) -> dict[str, str]:
    return {
        path.relative_to(folder).as_posix(): path.read_text(encoding="utf-8")
        for path in sorted(folder.rglob("*"))
        if path.is_file()
    }


def test_a_workspace_is_a_run_store() -> None:
    def needs_a_store(store: RunStore) -> RunStore:
        return store

    assert needs_a_store(Workspace(Path("home"), "id")) is not None


def test_the_layout_is_the_one_the_design_names(tmp_path: Path) -> None:
    workspace = Workspace(tmp_path, "shop-0123456789ab")
    root = tmp_path / "workspaces" / "shop-0123456789ab"

    assert workspace.root == root
    assert workspace.profile_path == root / "profile.yaml"
    assert workspace.override_path == root / "profile.override.yaml"
    assert workspace.wiki_dir == root / "wiki"
    assert workspace.sections_path == root / "sections.yaml"
    assert workspace.manifest_path == root / "render_manifest.yaml"
    assert workspace.snapshot_dir == root / "snapshot"
    assert workspace.clone_dir == root / "clone"


def test_naming_a_workspace_creates_nothing(tmp_path: Path) -> None:
    Workspace(tmp_path / "home", "shop-0123456789ab")

    assert not (tmp_path / "home").exists()


def test_each_run_gets_a_new_folder_of_its_own(workspace: Workspace) -> None:
    first, second = workspace.new_run_dir(), workspace.new_run_dir()

    assert first != second
    assert first.is_dir() and second.is_dir()
    assert first.parent == second.parent == workspace.root / "runs"


def test_the_copies_of_a_run_live_in_its_folder_and_are_left_to_the_copy(
    workspace: Workspace,
) -> None:
    run_dir = workspace.new_run_dir()

    assert workspace.input_copy(run_dir) == run_dir / "input"
    assert workspace.output_copy(run_dir) == run_dir / "output"
    assert not workspace.input_copy(run_dir).exists()


def test_a_new_workspace_has_no_snapshot(workspace: Workspace) -> None:
    assert not workspace.has_snapshot()


def test_a_saved_tree_becomes_the_snapshot(workspace: Workspace) -> None:
    tree = a_tree(workspace.new_run_dir() / "output", {"main.go": "package main\n"})

    workspace.save_snapshot(tree)

    assert workspace.has_snapshot()
    assert files_in(workspace.snapshot_dir) == {"main.go": "package main\n"}


def test_snapshot_replaced_atomically(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    old = a_tree(workspace.new_run_dir() / "output", {"a.go": "old\n", "gone.go": "x\n"})
    workspace.save_snapshot(old)
    new = a_tree(workspace.new_run_dir() / "output", {"a.go": "new\n", "added.go": "y\n"})
    workspace.save_snapshot(new)

    assert files_in(workspace.snapshot_dir) == {"a.go": "new\n", "added.go": "y\n"}

    # The swap is one rename. When it fails, the snapshot is still the whole of the last one.
    def refuse(source: object, target: object) -> None:
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(workspace_module.os, "replace", refuse)
    third = a_tree(workspace.new_run_dir() / "output", {"third.go": "z\n"})

    with pytest.raises(WorkspaceError):
        workspace.save_snapshot(third)

    assert files_in(workspace.snapshot_dir) == {"a.go": "new\n", "added.go": "y\n"}


def test_only_the_snapshot_and_the_one_before_it_are_kept_on_disk(workspace: Workspace) -> None:
    for number in range(4):
        tree = a_tree(workspace.new_run_dir() / "output", {"n.go": f"{number}\n"})
        workspace.save_snapshot(tree)

    kept = sorted((workspace.root / "snapshots").iterdir())
    assert len(kept) == 2
    assert files_in(workspace.snapshot_dir) == {"n.go": "3\n"}


def test_a_fresh_clone_replaces_the_last_one_only_once_it_is_done(workspace: Workspace) -> None:
    with workspace.fresh_clone() as folder:
        a_tree(folder, {"v1.txt": "one\n"})
    assert files_in(workspace.clone_dir) == {"v1.txt": "one\n"}

    with pytest.raises(RuntimeError), workspace.fresh_clone() as folder:
        a_tree(folder, {"v2.txt": "two\n"})
        raise RuntimeError("the clone failed")

    assert files_in(workspace.clone_dir) == {"v1.txt": "one\n"}


def test_a_workspace_that_cannot_be_written_names_arde_home(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.write_text("a file where a folder should be", encoding="utf-8")

    with pytest.raises(WorkspaceError) as stopped:
        Workspace(home, "shop-0123456789ab").new_run_dir()

    assert isinstance(stopped.value, ArdeError)
    assert stopped.value.exit_code == ExitCode.BAD_USAGE
    assert "ARDE_HOME" in stopped.value.next_action


def test_arde_home_defaults_to_a_folder_in_the_users_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("ARDE_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))

    assert arde_home() == tmp_path / ".arde"


def test_arde_home_is_the_folder_the_variable_names(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ARDE_HOME", str(tmp_path / "volume"))

    assert arde_home() == tmp_path / "volume"


def test_an_empty_arde_home_counts_as_unset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ARDE_HOME", "")
    monkeypatch.setenv("HOME", str(tmp_path))

    assert arde_home() == tmp_path / ".arde"


# Every child process runs in another folder, so a relative home would point somewhere else.
def test_a_relative_arde_home_is_made_absolute(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ARDE_HOME", "relative")

    assert arde_home() == tmp_path / "relative"
    assert os.path.isabs(arde_home())

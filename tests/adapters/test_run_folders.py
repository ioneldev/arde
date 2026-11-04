"""Finding the runs in ARDE_HOME, and exporting one as a tar of what a person needs from it.

The folders are laid out as Workspace makes them: workspaces/<repo-id>/runs/<run-id>/, holding
report.json, trace/, the service logs, and the input and output copies.
"""

import io
import shutil
import subprocess
import tarfile
from pathlib import Path

import pytest
from run_folder_support import CHANGES, RUN_ID, WORKSPACE, a_run

from arde.adapters.run_folders import (
    RunFolder,
    RunNotFound,
    UnsafeChangedPath,
    export_run,
    find_runs,
    pick_run,
    summarise,
)
from arde.domain.report import ExitCode


def exported(run: RunFolder) -> tuple[dict[str, bytes], str]:
    buffer = io.BytesIO()
    result = export_run(run, buffer)
    buffer.seek(0)
    with tarfile.open(fileobj=buffer, mode="r|") as tar:
        members = {}
        for member in tar:
            extracted = tar.extractfile(member)
            assert extracted is not None
            members[member.name] = extracted.read()
    return members, result.run_id


# Finding and picking a run


def test_runs_are_found_oldest_first_across_workspaces(tmp_path: Path) -> None:
    a_run(tmp_path, run_id="20261104T105708Z-a8063e", workspace="b-111111111111")
    a_run(tmp_path, run_id="20261104T000827Z-3eb1fe", workspace="a-222222222222")
    a_run(tmp_path, run_id="20261104T104315Z-827a78", workspace="b-111111111111")

    runs = find_runs(tmp_path)

    assert [(run.run_id, run.workspace) for run in runs] == [
        ("20261104T000827Z-3eb1fe", "a-222222222222"),
        ("20261104T104315Z-827a78", "b-111111111111"),
        ("20261104T105708Z-a8063e", "b-111111111111"),
    ]


def test_a_folder_that_is_not_a_run_is_left_out(tmp_path: Path) -> None:
    run_dir = a_run(tmp_path)
    (run_dir.parent / "notes").mkdir()
    (run_dir.parent / "20261104T105708Z-a8063e.txt").write_text("not a folder")

    assert [run.run_id for run in find_runs(tmp_path)] == [RUN_ID]


def test_a_home_with_no_workspace_has_no_runs(tmp_path: Path) -> None:
    assert find_runs(tmp_path / "missing") == []


def test_with_no_run_named_the_latest_is_picked(tmp_path: Path) -> None:
    a_run(tmp_path, run_id="20261104T105708Z-a8063e")
    a_run(tmp_path, run_id="20261104T000827Z-3eb1fe", workspace="other-0123456789ab")

    assert pick_run(tmp_path, None).run_id == "20261104T105708Z-a8063e"


def test_a_run_is_picked_by_the_start_of_its_id(tmp_path: Path) -> None:
    a_run(tmp_path, run_id="20261104T105708Z-a8063e")
    a_run(tmp_path, run_id="20261104T104315Z-827a78")

    assert pick_run(tmp_path, "20261104T1057").run_id == "20261104T105708Z-a8063e"


def test_a_start_two_runs_share_names_both(tmp_path: Path) -> None:
    a_run(tmp_path, run_id="20261104T105708Z-a8063e")
    a_run(tmp_path, run_id="20261104T104315Z-827a78")

    with pytest.raises(RunNotFound) as stopped:
        pick_run(tmp_path, "20261104T10")

    assert "20261104T104315Z-827a78" in str(stopped.value)
    assert "20261104T105708Z-a8063e" in str(stopped.value)
    assert stopped.value.exit_code == ExitCode.BAD_USAGE


def test_an_unknown_run_says_how_to_list_the_runs(tmp_path: Path) -> None:
    a_run(tmp_path)

    with pytest.raises(RunNotFound) as stopped:
        pick_run(tmp_path, "2025")

    assert "arde-runs list" in stopped.value.next_action


def test_with_no_run_at_all_it_says_how_to_start_one(tmp_path: Path) -> None:
    with pytest.raises(RunNotFound) as stopped:
        pick_run(tmp_path, None)

    assert "No run" in str(stopped.value)
    assert "--repo" in stopped.value.next_action


# Summaries


def test_a_local_run_is_summarised_from_its_report_and_trace(tmp_path: Path) -> None:
    a_run(tmp_path)

    summary = summarise(pick_run(tmp_path, None))

    assert (summary.run_id, summary.workspace, summary.source_kind) == (RUN_ID, WORKSPACE, "local")
    assert (summary.status, summary.cost_usd, summary.duration_s) == ("success", 0.7634, 93.2)
    assert summary.branch is None


def test_a_url_run_names_the_branch_it_committed(tmp_path: Path) -> None:
    report = {"status": "success", "exit_code": 0, "cost_usd": 2.47, "duration_s": 479.0}
    a_run(tmp_path, source_kind="url", report={**report, "branch": "arde/run-x"})

    summary = summarise(pick_run(tmp_path, None))

    assert (summary.source_kind, summary.branch) == ("url", "arde/run-x")


@pytest.mark.parametrize(
    ("report_text", "status"), [(None, "no report"), ("{", "unreadable report")]
)
def test_a_run_without_a_readable_report_says_so(
    tmp_path: Path, report_text: str | None, status: str
) -> None:
    report = a_run(tmp_path) / "report.json"
    if report_text is None:
        report.unlink()
    else:
        report.write_text(report_text)

    summary = summarise(pick_run(tmp_path, None))

    assert (summary.status, summary.cost_usd, summary.source_kind) == (status, None, "local")


# Exporting a run


def test_the_export_holds_the_report_trace_logs_patch_and_changed_files(tmp_path: Path) -> None:
    a_run(tmp_path)

    members, run_id = exported(pick_run(tmp_path, None))

    assert run_id == RUN_ID
    assert sorted(members) == [
        f"{RUN_ID}/changed/docs/api.md",
        f"{RUN_ID}/changed/main.go",
        f"{RUN_ID}/changes.patch",
        f"{RUN_ID}/report.json",
        f"{RUN_ID}/service-after.log",
        f"{RUN_ID}/trace/debug.log",
        f"{RUN_ID}/trace/trace.jsonl",
    ]


def test_the_changed_files_are_the_output_copys(tmp_path: Path) -> None:
    a_run(tmp_path)

    members, _ = exported(pick_run(tmp_path, None))

    assert members[f"{RUN_ID}/changed/main.go"] == CHANGES["main.go"][1]
    assert members[f"{RUN_ID}/changed/docs/api.md"] == b"# API\n"


def test_the_patch_turns_the_input_copy_into_the_output_copy(tmp_path: Path) -> None:
    run_dir = a_run(tmp_path / "home")
    tree = shutil.copytree(run_dir / "input", tmp_path / "repo")
    members, _ = exported(pick_run(tmp_path / "home", None))

    subprocess.run(
        ["git", "-c", "core.autocrlf=false", "apply", "-"],
        cwd=tree,
        input=members[f"{RUN_ID}/changes.patch"],
        capture_output=True,
        check=True,
    )

    for name in CHANGES:
        assert (tree / name).read_bytes() == (run_dir / "output" / name).read_bytes()


@pytest.mark.parametrize("path", ["../escape.txt", "/etc/passwd", "docs/../../escape.txt", ""])
def test_a_changed_path_outside_the_copies_stops_the_export(tmp_path: Path, path: str) -> None:
    report = {"status": "success", "changed_paths": ["main.go", path]}
    a_run(tmp_path, report=report)

    with pytest.raises(UnsafeChangedPath) as stopped:
        export_run(pick_run(tmp_path, None), io.BytesIO())

    assert stopped.value.exit_code == ExitCode.BAD_USAGE


def test_a_link_is_left_out_of_the_changed_files_and_the_patch(tmp_path: Path) -> None:
    run_dir = a_run(tmp_path)
    (run_dir / "output" / "docs" / "api.md").unlink()
    (run_dir / "output" / "docs" / "api.md").symlink_to("/etc/hosts")
    buffer = io.BytesIO()

    result = export_run(pick_run(tmp_path, None), buffer)

    assert (result.changed, result.left_out) == (("main.go",), ("docs/api.md",))
    buffer.seek(0)
    with tarfile.open(fileobj=buffer, mode="r|") as tar:
        names = [member.name for member in tar]
    assert f"{RUN_ID}/changed/docs/api.md" not in names


def test_a_run_with_no_report_exports_its_trace_and_logs(tmp_path: Path) -> None:
    (a_run(tmp_path) / "report.json").unlink()

    members, _ = exported(pick_run(tmp_path, None))

    assert sorted(members) == [
        f"{RUN_ID}/service-after.log",
        f"{RUN_ID}/trace/debug.log",
        f"{RUN_ID}/trace/trace.jsonl",
    ]

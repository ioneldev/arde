"""arde-runs: the runs in ARDE_HOME listed, and one exported as a tar on stdout."""

import io
import tarfile
from pathlib import Path

from run_folder_support import RUN_ID, a_run

from arde.cli_runs import run
from arde.domain.report import ExitCode


class Terminal(io.BytesIO):
    def isatty(self) -> bool:
        return True


def ran(home: Path, *argv: str, binary_out: io.BytesIO | None = None) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    code = run(list(argv), home=home, out=out, err=err, binary_out=binary_out or io.BytesIO())
    return code, out.getvalue(), err.getvalue()


def test_list_prints_one_line_per_run_under_a_header(tmp_path: Path) -> None:
    a_run(
        tmp_path,
        run_id="20261104T000827Z-3eb1fe",
        source_kind="url",
        report={
            "status": "success",
            "cost_usd": 2.4714,
            "duration_s": 479.3,
            "branch": "arde/run-x",
        },
    )
    a_run(tmp_path)

    code, out, _ = ran(tmp_path, "list")

    header, first, second = out.splitlines()
    assert code == ExitCode.OK
    assert header.split() == ["RUN", "WORKSPACE", "SOURCE", "STATUS", "COST", "TIME", "BRANCH"]
    assert first.split() == [
        "20261104T000827Z-3eb1fe",
        "things-0123456789ab",
        "url",
        "success",
        "$2.47",
        "479",
        "s",
        "arde/run-x",
    ]
    assert second.split() == [
        RUN_ID,
        "things-0123456789ab",
        "local",
        "success",
        "$0.76",
        "93",
        "s",
    ]


def test_list_with_no_run_says_where_it_looked(tmp_path: Path) -> None:
    code, out, _ = ran(tmp_path, "list")

    assert code == ExitCode.OK
    assert out == f"No run in {tmp_path} yet.\n"


def test_export_writes_the_tar_to_stdout_and_says_what_it_holds(tmp_path: Path) -> None:
    a_run(tmp_path)
    tar_out = io.BytesIO()

    code, out, err = ran(tmp_path, "export", binary_out=tar_out)

    assert (code, out) == (ExitCode.OK, "")
    tar_out.seek(0)
    with tarfile.open(fileobj=tar_out, mode="r|") as tar:
        assert f"{RUN_ID}/changes.patch" in [member.name for member in tar]
    assert err == (
        f"arde-runs: exported run {RUN_ID}: 2 changed files under changed/, changes.patch, the "
        "report, the trace and the service logs.\n"
    )


def test_export_of_a_run_with_no_report_says_it_has_none(tmp_path: Path) -> None:
    (a_run(tmp_path) / "report.json").unlink()

    _, _, err = ran(tmp_path, "export")

    assert (
        f"exported run {RUN_ID}: 0 changed files under changed/, no report, as the run never "
        "wrote one, the trace and the service logs." in err
    )


def test_export_names_a_run_by_the_start_of_its_id(tmp_path: Path) -> None:
    a_run(tmp_path)
    a_run(tmp_path, run_id="20261104T000827Z-3eb1fe")

    _, _, err = ran(tmp_path, "export", "20261104T0008")

    assert "exported run 20261104T000827Z-3eb1fe" in err


def test_export_names_what_it_left_out(tmp_path: Path) -> None:
    run_dir = a_run(tmp_path)
    (run_dir / "output" / "docs" / "api.md").unlink()
    (run_dir / "output" / "docs" / "api.md").symlink_to("/etc/hosts")

    _, _, err = ran(tmp_path, "export")

    assert "1 changed file under changed/," in err
    assert "Left out, as a link or not a file: docs/api.md." in err


def test_export_refuses_to_write_a_tar_to_a_terminal(tmp_path: Path) -> None:
    a_run(tmp_path)

    code, _, err = ran(tmp_path, "export", binary_out=Terminal())

    assert code == ExitCode.BAD_USAGE
    assert "next: Pipe it into tar" in err


def test_an_unknown_run_exits_2_with_the_next_action(tmp_path: Path) -> None:
    a_run(tmp_path)

    code, _, err = ran(tmp_path, "export", "1999")

    assert code == ExitCode.BAD_USAGE
    assert err.splitlines() == [
        'arde-runs: No run\'s id starts with "1999".',
        "  next: List the runs with arde-runs list, then name one of them.",
    ]


def test_no_command_is_bad_usage(tmp_path: Path) -> None:
    code, _, err = ran(tmp_path)

    assert code == ExitCode.BAD_USAGE
    assert "usage: arde-runs" in err

"""arde-runs: lists the runs in ARDE_HOME, and exports one as a tar on stdout.

    arde-runs list
    arde-runs export [RUN]    the latest run, or the one run whose id starts with RUN

The export holds the run's report, trace and service logs, the files it changed as its output copy
has them, and changes.patch, which git apply turns a clone of the repo into the output copy with.
In the container, the runs live in the arde-home volume, and this is how they reach the host.
"""

import argparse
import sys
from collections.abc import Sequence
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import BinaryIO, TextIO

from arde.adapters.run_folders import (
    CHANGED_FOLDER,
    PATCH_FILE,
    Exported,
    RunSummary,
    export_run,
    find_runs,
    pick_run,
    summarise,
)
from arde.adapters.workspace import arde_home
from arde.domain.base import printable_name, visible_text
from arde.domain.errors import ArdeError
from arde.domain.report import ExitCode

PROGRAM = "arde-runs"
PREFIX = f"{PROGRAM}:"
_HEADER = ("RUN", "WORKSPACE", "SOURCE", "STATUS", "COST", "TIME", "BRANCH")
_NOT_RECORDED = "-"


class TarToATerminal(ArdeError):
    def __init__(self) -> None:
        super().__init__(
            "The export is a tar archive, and stdout is a terminal.",
            next_action=f"Pipe it into tar, as in: {PROGRAM} export | tar -xf - -C runs",
            exit_code=ExitCode.BAD_USAGE,
        )


def main(argv: Sequence[str] | None = None) -> int:
    return run(argv, home=arde_home(), out=sys.stdout, err=sys.stderr, binary_out=sys.stdout.buffer)


def run(
    argv: Sequence[str] | None, *, home: Path, out: TextIO, err: TextIO, binary_out: BinaryIO
) -> int:
    try:
        with redirect_stdout(out), redirect_stderr(err):
            arguments = _parser().parse_args(argv)
    except SystemExit as stop:
        # argparse has already said why, on stderr, or printed the help on stdout.
        return stop.code if isinstance(stop.code, int) else ExitCode.BAD_USAGE
    try:
        if arguments.command == "list":
            _list(home, out=out)
        else:
            _export(home, arguments.run, err=err, binary_out=binary_out)
    except ArdeError as error:
        _write(err, f"{PREFIX} {error}\n  next: {error.next_action}")
        return int(error.exit_code)
    return int(ExitCode.OK)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=PROGRAM, description="Lists ARDE's runs, and exports one as a tar on stdout."
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("list", help="list every run, oldest first")
    export = commands.add_parser(
        "export",
        help=(
            f"write a run's report, trace, logs, changed files and {PATCH_FILE} to stdout, as a tar"
        ),
    )
    export.add_argument(
        "run", nargs="?", help="the run, or the start of its id (default: the latest run)"
    )
    return parser


def _list(home: Path, *, out: TextIO) -> None:
    runs = find_runs(home)
    if not runs:
        _write(out, f"No run in {printable_name(str(home))} yet.")
        return
    rows = [_HEADER, *(_row(summarise(run)) for run in runs)]
    widths = [max(len(row[column]) for row in rows) for column in range(len(_HEADER))]
    _write(out, "\n".join(_aligned(row, widths) for row in rows))


def _row(summary: RunSummary) -> tuple[str, ...]:
    cost = _NOT_RECORDED if summary.cost_usd is None else f"${summary.cost_usd:.2f}"
    time = _NOT_RECORDED if summary.duration_s is None else f"{summary.duration_s:.0f} s"
    return (
        summary.run_id,
        summary.workspace,
        summary.source_kind,
        summary.status,
        cost,
        time,
        summary.branch or "",
    )


def _aligned(row: Sequence[str], widths: Sequence[int]) -> str:
    return "  ".join(cell.ljust(width) for cell, width in zip(row, widths, strict=True)).rstrip()


def _export(home: Path, wanted: str | None, *, err: TextIO, binary_out: BinaryIO) -> None:
    if binary_out.isatty():
        raise TarToATerminal
    exported = export_run(pick_run(home, wanted), binary_out)
    binary_out.flush()
    _write(err, _what_was_exported(exported))


def _what_was_exported(exported: Exported) -> str:
    count = len(exported.changed)
    parts = [f"{count} changed {'file' if count == 1 else 'files'} under {CHANGED_FOLDER}/"]
    if exported.patched:
        parts.append(PATCH_FILE)
    parts.append("the report" if exported.has_report else "no report, as the run never wrote one")
    lines = [
        f"{PREFIX} exported run {exported.run_id}: {', '.join(parts)}, the trace and the service "
        "logs."
    ]
    if exported.left_out:
        lines.append(f"  Left out, as a link or not a file: {', '.join(exported.left_out)}.")
    return "\n".join(lines)


def _write(stream: TextIO, text: str) -> None:
    stream.write(visible_text(text) + "\n")
    stream.flush()

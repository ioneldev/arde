"""The runs in ARDE_HOME, and one run exported as a tar of what a person needs from it.

A run folder is laid out as Workspace makes it: workspaces/<repo-id>/runs/<run-id>/ holds
report.json, trace/, the service logs, and the input and output copies. The export leaves the two
copies out. It carries the files the run changed, as the output copy has them, and a patch that
turns the input copy into the output copy, so the changes can be read, or applied to a clone.
"""

import io
import json
import re
import tarfile
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import BinaryIO

from arde.adapters.unified_patch import FileChange, unified_patch
from arde.domain.base import printable_name
from arde.domain.errors import ArdeError
from arde.domain.report import ExitCode

# As Workspace names a run: sorted by time, then a random suffix.
_RUN_ID = re.compile(r"\d{8}T\d{6}Z-[0-9a-f]{6}")
_REPORT_FILE = "report.json"
_TRACE_FOLDER = "trace"
_TRACE_FILE = "trace.jsonl"
_SERVICE_LOGS = "service-*.log"
_INPUT_COPY = "input"
_OUTPUT_COPY = "output"
CHANGED_FOLDER = "changed"
PATCH_FILE = "changes.patch"
LIST_COMMAND = "arde-runs list"
NO_REPORT = "no report"
UNREADABLE_REPORT = "unreadable report"
UNKNOWN_SOURCE = "?"
_GENERATED_FILE_MODE = 0o644
_PERMISSION_BITS = 0o777


@dataclass(frozen=True)
class RunFolder:
    run_id: str
    # The repo's workspace, which every run of that repo shares.
    workspace: str
    path: Path


@dataclass(frozen=True)
class RunSummary:
    run_id: str
    workspace: str
    # local or url, as the trace's first event records it.
    source_kind: str
    status: str
    cost_usd: float | None
    duration_s: float | None
    # The branch a URL run committed on. A local run commits nothing.
    branch: str | None


@dataclass(frozen=True)
class Exported:
    run_id: str
    # The files under changed/, from the repo root.
    changed: tuple[str, ...]
    # Changed paths that are a link or not a file in either copy, which neither part holds.
    left_out: tuple[str, ...]
    patched: bool
    # False for a run that ended before it wrote one, such as an interrupted run.
    has_report: bool


class RunNotFound(ArdeError):
    def __init__(self, message: str, *, next_action: str) -> None:
        super().__init__(message, next_action=next_action, exit_code=ExitCode.BAD_USAGE)


class UnsafeChangedPath(ArdeError):
    def __init__(self, path: str) -> None:
        super().__init__(
            "The run's report.json names a changed path outside its copies: "
            f'"{printable_name(path)}".',
            next_action=(
                "Export another run. ARDE writes only paths inside the repo, so something other "
                "than ARDE changed this report."
            ),
            exit_code=ExitCode.BAD_USAGE,
        )


def find_runs(home: Path) -> list[RunFolder]:
    """Every run of every workspace, oldest first."""
    runs = [
        RunFolder(run_id=folder.name, workspace=folder.parent.parent.name, path=folder)
        for folder in home.glob("workspaces/*/runs/*")
        if _RUN_ID.fullmatch(folder.name) and folder.is_dir() and not folder.is_symlink()
    ]
    return sorted(runs, key=lambda run: run.run_id)


def pick_run(home: Path, wanted: str | None) -> RunFolder:
    """The latest run when none is named, or the one run whose id starts with wanted."""
    runs = find_runs(home)
    if not runs:
        raise RunNotFound(
            f"No run in {printable_name(str(home))} yet.",
            next_action="Run ARDE on a repo first, with arde --repo <url or path>, then try again.",
        )
    if wanted is None:
        return runs[-1]
    matching = [run for run in runs if run.run_id.startswith(wanted)]
    if len(matching) == 1:
        return matching[0]
    if not matching:
        raise RunNotFound(
            f'No run\'s id starts with "{printable_name(wanted)}".',
            next_action=f"List the runs with {LIST_COMMAND}, then name one of them.",
        )
    raise RunNotFound(
        f'"{printable_name(wanted)}" starts the ids of {len(matching)} runs: '
        f"{', '.join(run.run_id for run in matching)}.",
        next_action="Give more of the id, so that it starts one run's id only.",
    )


def summarise(run: RunFolder) -> RunSummary:
    report = _read_report(run.path)
    return RunSummary(
        run_id=run.run_id,
        workspace=run.workspace,
        source_kind=_source_kind(run.path),
        status=report.status,
        cost_usd=_number(report.fields, "cost_usd"),
        duration_s=_number(report.fields, "duration_s"),
        branch=_text(report.fields, "branch"),
    )


def export_run(run: RunFolder, out: BinaryIO) -> Exported:
    """Writes the run's report, trace and service logs, its changed files and the patch to out,
    as a tar stream with every name under the run's id."""
    # Every changed path is checked before the first byte goes out, so a refusal leaves no half
    # archive behind.
    paths = [_inside_the_copies(path) for path in _changed_paths(_read_report(run.path))]
    sides = _Sides(input_copy=run.path / _INPUT_COPY, output_copy=run.path / _OUTPUT_COPY)
    changes, left_out = sides.changes(paths)
    patch = unified_patch(changes)
    records = _record_files(run.path)
    with tarfile.open(fileobj=out, mode="w|", format=tarfile.PAX_FORMAT) as tar:
        archive = _Archive(tar=tar, run_id=run.run_id)
        for name, path in records:
            archive.add_file(name, path)
        if patch:
            archive.add_bytes(PATCH_FILE, patch)
        for change in changes:
            if change.after is not None:
                archive.add_bytes(f"{CHANGED_FOLDER}/{change.path}", change.after)
    return Exported(
        run_id=run.run_id,
        changed=tuple(change.path for change in changes if change.after is not None),
        left_out=tuple(left_out),
        patched=bool(patch),
        has_report=any(name == _REPORT_FILE for name, _ in records),
    )


# The report and the trace


@dataclass(frozen=True)
class _Report:
    status: str
    fields: Mapping[str, object]


def _read_report(run_dir: Path) -> _Report:
    path = run_dir / _REPORT_FILE
    if path.is_symlink() or not path.is_file():
        return _Report(status=NO_REPORT, fields={})
    try:
        fields = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return _Report(status=UNREADABLE_REPORT, fields={})
    if not isinstance(fields, dict):
        return _Report(status=UNREADABLE_REPORT, fields={})
    return _Report(status=_text(fields, "status") or UNREADABLE_REPORT, fields=fields)


def _source_kind(run_dir: Path) -> str:
    trace = run_dir / _TRACE_FOLDER / _TRACE_FILE
    try:
        with trace.open(encoding="utf-8") as lines:
            first = json.loads(lines.readline())
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return UNKNOWN_SOURCE
    if not isinstance(first, dict) or first.get("kind") != "run_start":
        return UNKNOWN_SOURCE
    return _text(first, "source_kind") or UNKNOWN_SOURCE


def _changed_paths(report: _Report) -> list[str]:
    paths = report.fields.get("changed_paths")
    if not isinstance(paths, list):
        return []
    return [str(path) for path in paths]


def _number(fields: Mapping[str, object], key: str) -> float | None:
    value = fields.get(key)
    # bool is an int in Python, and a report never means true as a number.
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def _text(fields: Mapping[str, object], key: str) -> str | None:
    value = fields.get(key)
    return value if isinstance(value, str) else None


def _record_files(run_dir: Path) -> list[tuple[str, Path]]:
    """The report, the trace's files and the service logs, by their names in the archive."""
    candidates = [
        (_REPORT_FILE, run_dir / _REPORT_FILE),
        *(
            (f"{_TRACE_FOLDER}/{path.name}", path)
            for path in sorted((run_dir / _TRACE_FOLDER).glob("*"))
        ),
        *((path.name, path) for path in sorted(run_dir.glob(_SERVICE_LOGS))),
    ]
    return [(name, path) for name, path in candidates if _is_plain_file(path)]


# The changed files


def _inside_the_copies(text: str) -> PurePosixPath:
    path = PurePosixPath(text)
    if not text or path.is_absolute() or ".." in path.parts:
        raise UnsafeChangedPath(text)
    return path


class _NotAFile(Exception):
    """A link, a folder or another kind of file, which the export does not follow or read."""


@dataclass(frozen=True)
class _Sides:
    input_copy: Path
    output_copy: Path

    def changes(self, paths: Sequence[PurePosixPath]) -> tuple[list[FileChange], list[str]]:
        changes: list[FileChange] = []
        left_out: list[str] = []
        for path in paths:
            try:
                before = _contents(self.input_copy, path)
                after = _contents(self.output_copy, path)
            except _NotAFile:
                left_out.append(str(path))
                continue
            if before is None and after is None:
                left_out.append(str(path))
                continue
            changes.append(FileChange(str(path), before=before, after=after))
        return changes, left_out


def _contents(copy: Path, relative: PurePosixPath) -> bytes | None:
    """The file's bytes, or None when the copy does not have it."""
    path = copy.joinpath(*relative.parts)
    # A link in a folder above it could lead out of the copy.
    if any(
        copy.joinpath(*relative.parts[:end]).is_symlink() for end in range(1, len(relative.parts))
    ):
        raise _NotAFile
    if path.is_symlink():
        raise _NotAFile
    if not path.exists():
        return None
    if not path.is_file():
        raise _NotAFile
    return path.read_bytes()


def _is_plain_file(path: Path) -> bool:
    return path.is_file() and not path.is_symlink()


@dataclass(frozen=True)
class _Archive:
    tar: tarfile.TarFile
    run_id: str

    def add_file(self, name: str, path: Path) -> None:
        # Kept as the run left it: debug.log and the service logs hold the repo's source.
        mode = path.stat().st_mode & _PERMISSION_BITS
        self.add_bytes(name, path.read_bytes(), mode=mode)

    def add_bytes(self, name: str, content: bytes, *, mode: int = _GENERATED_FILE_MODE) -> None:
        member = tarfile.TarInfo(f"{self.run_id}/{name}")
        member.size = len(content)
        member.mode = mode
        member.mtime = int(time.time())
        self.tar.addfile(member, io.BytesIO(content))

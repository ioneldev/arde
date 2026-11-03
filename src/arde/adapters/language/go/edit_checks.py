"""Whether a run changed a Go repo only where ARDE writes: its comment lines and its tag pairs.

Two comparisons, because each sees what the other cannot. goinspect compares syntax trees, which
hold every change to code and no comment at all. The allow-list compares lines, so it sees a
directive such as //go:build: each line the output gained must be one the render reports writing,
or a line of the input with ARDE's tag pairs added or taken out, and each line it lost must be an
annotation, a line the last run's manifest lists, or an empty comment line, which gofmt sets
between a block and a directive and may take out once the block is gone. Lines are compared with
their spacing collapsed and counted rather than placed, as the render measured them, so a line that
only moved is not seen here: the syntax trees see code that moved.
"""

import os
import re
from collections import Counter
from collections.abc import Collection, Iterator, Sequence
from pathlib import Path, PurePosixPath
from typing import Literal, NamedTuple

from arde.adapters.child_process import describe_os_error
from arde.adapters.language.go.goinspect_client import CompareReport
from arde.adapters.language.go.renderer import NumberedLine, collapsed_line, unmatched_lines
from arde.adapters.language.go.struct_tags import persons_pairs
from arde.application.ports import EditCheckRequest
from arde.domain.base import one_line, printable_name
from arde.domain.errors import ArdeError
from arde.domain.findings import Finding
from arde.domain.report import ExitCode

AGENT_EDITS = "agent_edits"
# The files the go command reads to learn what a build depends on. ARDE never writes one.
MODULE_FILES = frozenset({"go.mod", "go.sum", "go.work", "go.work.sum"})
_ANNOTATION = "// @"
_EMPTY_COMMENT = "//"
_RAW_STRING = re.compile(r"`([^`]*)`")
_SHOWN_LINE_LENGTH = 200
_NEXT_ACTION = (
    "This is a bug in ARDE: the output copy holds a change ARDE does not make. Report it with the "
    "file named."
)


class CopyNotReadableError(ArdeError):
    """A copy of the repo that the run made could not be read back while its edits were checked."""

    def __init__(self, error: OSError) -> None:
        super().__init__(
            "Cannot read a copy of the repo while checking ARDE's edits: "
            f"{printable_name(describe_os_error(error))}.",
            next_action="Check that ARDE's workspace is readable and its disk is not full, then "
            "run again.",
            exit_code=ExitCode.CHECKS_FAILED,
        )


class UnexplainedLine(NamedTuple):
    change: Literal["gained", "lost"]
    # Counted from 1: in the output for a line it gained, in the input for one it lost.
    number: int
    # With its spacing collapsed.
    text: str


def edit_findings(request: EditCheckRequest, compared: CompareReport) -> list[Finding]:
    """Findings for every change in request.after that ARDE does not make.

    compared is goinspect's comparison of the two copies.
    """
    try:
        before, after = _source_files(request.before), _source_files(request.after)
        return [
            *_code_changes(compared),
            *_module_file_changes(before, after),
            *_unexplained_line_findings(request, before, after),
        ]
    except OSError as error:
        raise CopyNotReadableError(error) from error


def unexplained_lines(
    before: str, after: str, *, rendered: Sequence[str], removable: Collection[str]
) -> list[UnexplainedLine]:
    """The lines after gained, then the lines before lost, that the allow-list does not explain.

    rendered holds the lines the render reports writing, and removable the lines the last run's
    manifest lists, each with its spacing collapsed.
    """
    gained = unmatched_lines(after, other=before)
    lost = unmatched_lines(before, other=after)
    untagged_lost = _by_text_without_ardes_pairs(lost)
    still_rendered = Counter(rendered)
    retagged: set[int] = set()
    unexplained: list[UnexplainedLine] = []
    for line in gained:
        old_versions = untagged_lost.get(_without_ardes_pairs(line.text), [])
        if still_rendered[line.text] > 0:
            still_rendered[line.text] -= 1
        elif old_versions:
            retagged.add(old_versions.pop(0).number)
        else:
            unexplained.append(UnexplainedLine("gained", line.number, line.text))
    unexplained.extend(
        UnexplainedLine("lost", line.number, line.text)
        for line in lost
        if line.number not in retagged and not _may_be_lost(line.text, removable)
    )
    return unexplained


# Every Go file and module file below root, by its path from root. goinspect's comparison skips
# only .git, and so does this.
def _source_files(root: Path) -> dict[str, Path]:
    found: dict[str, Path] = {}
    for folder, subfolders, names in os.walk(root, onerror=_raise):
        subfolders[:] = [name for name in subfolders if name != ".git"]
        for name in names:
            path = Path(folder) / name
            if (name.endswith(".go") or name in MODULE_FILES) and path.is_file():
                found[path.relative_to(root).as_posix()] = path
    return found


def _raise(error: OSError) -> None:
    raise error


def _code_changes(compared: CompareReport) -> Iterator[Finding]:
    for file in compared.added:
        yield _finding(file, "is a Go file the input does not have.")
    for file in compared.removed:
        yield _finding(file, "is a Go file of the input that is gone.")
    for file in compared.changed:
        yield _finding(file, "differs from the input in its code, beyond comments and ARDE's tags.")


def _module_file_changes(before: dict[str, Path], after: dict[str, Path]) -> Iterator[Finding]:
    for file in sorted(before.keys() | after.keys()):
        if PurePosixPath(file).name not in MODULE_FILES:
            continue
        if file not in after:
            yield _finding(file, "is gone from the output, and ARDE never removes one.")
        elif file not in before:
            yield _finding(file, "is new in the output, and ARDE never writes one.")
        elif before[file].read_bytes() != after[file].read_bytes():
            yield _finding(file, "differs from the input, and ARDE never edits one.")


def _unexplained_line_findings(
    request: EditCheckRequest, before: dict[str, Path], after: dict[str, Path]
) -> Iterator[Finding]:
    for file in sorted(before.keys() & after.keys()):
        old, new = before[file].read_bytes(), after[file].read_bytes()
        if not file.endswith(".go") or old == new:
            continue
        try:
            old_text, new_text = old.decode("utf-8"), new.decode("utf-8")
        except UnicodeDecodeError:
            yield _finding(file, "changed, and it is not UTF-8, which the render never writes.")
            continue
        unexplained = unexplained_lines(
            old_text,
            new_text,
            rendered=request.render.added_lines.get(file, ()),
            removable={collapsed_line(line) for line in request.manifest.lines.get(file, ())},
        )
        yield from _findings_for(file, unexplained)


def _findings_for(file: str, unexplained: Sequence[UnexplainedLine]) -> Iterator[Finding]:
    gained = [line for line in unexplained if line.change == "gained"]
    lost = [line for line in unexplained if line.change == "lost"]
    if gained:
        yield _finding(
            file,
            f"gained {_count_of(gained)} that the render did not write and that are no line of "
            f"the input with ARDE's tags added or taken out. The first is line {gained[0].number}: "
            f"{_shown(gained[0].text)}",
        )
    if lost:
        yield _finding(
            file,
            f"lost {_count_of(lost)} that ARDE did not write. The first is line "
            f"{lost[0].number} of the input: {_shown(lost[0].text)}",
        )


# A field's line that a render retagged differs from its old line only in the pairs of its tag
# that ARDE adds, whichever way they went, so both read the same once those pairs are out.
def _by_text_without_ardes_pairs(lines: Sequence[NumberedLine]) -> dict[str, list[NumberedLine]]:
    found: dict[str, list[NumberedLine]] = {}
    for line in lines:
        untagged = _without_ardes_pairs(line.text)
        if untagged:
            found.setdefault(untagged, []).append(line)
    return found


# The renderer sets its pairs in the first raw string of a field's line, so that is the one read.
# A comment, a line with no raw string and a tag ARDE cannot read give the empty text, which pairs
# with nothing.
def _without_ardes_pairs(line: str) -> str:
    tag = _RAW_STRING.search(line)
    pairs = None if tag is None or line.startswith("//") else persons_pairs(tag[1])
    if tag is None or pairs is None:
        return ""
    return f"{line[: tag.start()]}`{' '.join(pairs)}`{line[tag.end() :]}"


def _may_be_lost(line: str, removable: Collection[str]) -> bool:
    return line.startswith(_ANNOTATION) or line == _EMPTY_COMMENT or line in removable


def _count_of(lines: Sequence[UnexplainedLine]) -> str:
    return "1 line" if len(lines) == 1 else f"{len(lines)} lines"


def _shown(line: str) -> str:
    return one_line(line, _SHOWN_LINE_LENGTH)


def _finding(file: str, what: str) -> Finding:
    name = printable_name(file)
    return Finding(
        check=AGENT_EDITS,
        owner="code",
        subject=name,
        message=f"{name} {what}",
        next_action=_NEXT_ACTION,
    )

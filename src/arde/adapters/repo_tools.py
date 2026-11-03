"""The read-only tools an agent has on a repo: list_files, read_file and grep.

A model names every path, so each one is resolved with its links followed and refused unless it
stays inside the repo. The repo supplies every byte a tool returns, so each result is shown with
its control characters written out. No tool writes, and none runs a command.
"""

import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from arde.adapters.child_text import visible_text
from arde.adapters.repo_links import is_inside
from arde.application.ports import Tool, ToolDef, ToolOutcome
from arde.application.tool_schema import strict_tool_schema
from arde.domain.base import Frozen

# How much of one file read_file shows and grep searches. A file cut short says so.
READ_LIMIT_BYTES = 200_000
LISTED_FILE_LIMIT = 1000
GREP_MATCH_LIMIT = 100
# A minified file can hold one line of megabytes, and a match shows its whole line.
GREP_LINE_LIMIT = 500


def clean_repo_text(text: str) -> str:
    """Repo text as a model may see it, with nothing a reader cannot see.

    Each control, format or separator character but the line break and the tab is written out as
    visible_text does, and so is each lone surrogate, which is what a file name that is not UTF-8
    decodes to.
    """
    return visible_text(text).encode("utf-8", "backslashreplace").decode("utf-8")


class _FolderInput(Frozen):
    path: str = Field(description="A folder, as a path from the repo root. Use . for the root.")


class _FileInput(Frozen):
    path: str = Field(description="A file, as a path from the repo root.")


class _GrepInput(Frozen):
    text: str = Field(
        description=(
            "The text to find, on one line, matched exactly as written. It is not a regular "
            "expression, and case counts."
        )
    )
    path: str = Field(
        description="A folder or a file, as a path from the repo root. Use . for the whole repo."
    )


LIST_FILES = ToolDef(
    name="list_files",
    description=(
        "Lists every file under a folder of the repo, and under its subfolders, as paths from the "
        f"repo root, sorted. Shows at most {LISTED_FILE_LIMIT} paths."
    ),
    input_schema=strict_tool_schema(_FolderInput),
    strict=True,
)
READ_FILE = ToolDef(
    name="read_file",
    description=(
        "Reads one file of the repo, with each line numbered. Shows at most the first "
        f"{READ_LIMIT_BYTES} bytes of a file."
    ),
    input_schema=strict_tool_schema(_FileInput),
    strict=True,
)
GREP = ToolDef(
    name="grep",
    description=(
        "Finds the lines that contain some text, in one file or in every file under a folder, "
        "and shows each as path:line: text. Binary files are passed over. Searches the same "
        "part of a file that read_file shows."
    ),
    input_schema=strict_tool_schema(_GrepInput),
    strict=True,
)


class _Refused(Exception):
    """A call the model can correct. Its message goes back to the model as the tool's error."""


def repo_tools(root: Path) -> tuple[Tool, ...]:
    repo = Path(os.path.realpath(root))
    return (
        Tool(
            definition=LIST_FILES,
            handler=_handler(_FolderInput, lambda folder: _list_files(repo, folder.path)),
        ),
        Tool(
            definition=READ_FILE,
            handler=_handler(_FileInput, lambda file: _read_file(repo, file.path)),
        ),
        Tool(definition=GREP, handler=_handler(_GrepInput, lambda search: _grep(repo, search))),
    )


def _handler[T: BaseModel](
    schema: type[T], run: Callable[[T], str]
) -> Callable[[dict[str, object]], ToolOutcome]:
    def handle(tool_input: dict[str, object]) -> ToolOutcome:
        # A ValidationError goes up to the agent loop, which sends it back to the model.
        arguments = schema.model_validate(tool_input)
        try:
            text = run(arguments)
        except _Refused as refusal:
            return ToolOutcome(content=clean_repo_text(str(refusal)), is_error=True)
        return ToolOutcome(content=clean_repo_text(text))

    return handle


def _list_files(repo: Path, chosen: str) -> str:
    folder = _chosen_path(repo, chosen)
    kind = _kind_of(folder)
    if kind == "file":
        raise _Refused(f"{chosen} is a file. Use read_file to read it.")
    if kind != "folder":
        raise _Refused(f"There is no folder named {chosen}. Use list_files on . to see the repo.")
    names = [file.name for file in _files_under(repo, folder)]
    if not names:
        return f"There is no file under {chosen}."
    listed = "\n".join(_on_one_line(name) for name in names[:LISTED_FILE_LIMIT])
    if len(names) <= LISTED_FILE_LIMIT:
        return listed
    return (
        f"{listed}\n[ARDE listed the first {LISTED_FILE_LIMIT} of {len(names)} files. "
        "List a folder further down to see the rest.]"
    )


def _read_file(repo: Path, chosen: str) -> str:
    file = _chosen_path(repo, chosen)
    kind = _kind_of(file)
    if kind == "folder":
        raise _Refused(f"{chosen} is a folder. Use list_files to see what it holds.")
    if kind != "file":
        raise _Refused(f"There is no file named {chosen}. Use list_files to see what exists.")
    try:
        start = _read_start(file)
    except OSError as error:
        raise _Refused(f"{chosen} cannot be read: {error.strerror or error}.") from error
    if start.is_binary:
        raise _Refused(f"{chosen} is a binary file, which read_file does not show.")
    if not start.data:
        return f"{chosen} is empty."
    lines = _lines(start.text)
    numbered = "\n".join(f"{number:6}\t{line}" for number, line in enumerate(lines, start=1))
    if not start.is_cut:
        return numbered
    return (
        f"{numbered}\n[ARDE showed the first {len(start.data)} of the {start.size} bytes in this "
        "file. The tools read no further into a file.]"
    )


def _grep(repo: Path, search: _GrepInput) -> str:
    if not search.text or "\n" in search.text:
        raise _Refused("grep matches within one line. Give it text with no line break.")
    found = _search(_files_to_search(repo, search.path), search.text)
    shown = "\n".join(found.lines[:GREP_MATCH_LIMIT])
    notes: list[str] = []
    if len(found.lines) > GREP_MATCH_LIMIT:
        notes.append(
            f"[ARDE showed the first {GREP_MATCH_LIMIT} of {len(found.lines)} matching lines. "
            "Search for longer text or in a smaller folder to see the rest.]"
        )
    if found.cut_files:
        notes.append(
            f"[ARDE searched only the first {READ_LIMIT_BYTES} bytes of "
            f"{_files(found.cut_files)} here.]"
        )
    if found.unreadable_files:
        notes.append(f"[ARDE could not read {_files(found.unreadable_files)} here.]")
    return "\n".join([shown or f"No line in {search.path} holds that text.", *notes])


@dataclass(frozen=True)
class _RepoFile:
    # As the walk met it, from the repo root: a link keeps its own name.
    name: str
    real_path: Path


@dataclass(frozen=True)
class _Found:
    lines: list[str]
    cut_files: int
    unreadable_files: int


def _search(files: list[_RepoFile], text: str) -> _Found:
    lines: list[str] = []
    cut_files = unreadable_files = 0
    for file in files:
        try:
            start = _read_start(file.real_path)
        except OSError:
            unreadable_files += 1
            continue
        if start.is_binary:
            continue
        cut_files += start.is_cut
        lines.extend(
            f"{_on_one_line(file.name)}:{number}: {_shortened(line)}"
            for number, line in enumerate(_lines(start.text), start=1)
            if text in line
        )
    return _Found(lines=lines, cut_files=cut_files, unreadable_files=unreadable_files)


def _files(count: int) -> str:
    return f"{count} file" if count == 1 else f"{count} files"


def _files_to_search(repo: Path, chosen: str) -> list[_RepoFile]:
    path = _chosen_path(repo, chosen)
    match _kind_of(path):
        case "folder":
            return _files_under(repo, path)
        case "file":
            return [_RepoFile(name=path.relative_to(repo).as_posix(), real_path=path)]
        case _:
            raise _Refused(
                f"There is no file or folder named {chosen}. Use list_files to see what exists."
            )


def _chosen_path(repo: Path, chosen: str) -> Path:
    if "\x00" in chosen:
        raise _Refused("A path cannot hold a NUL character. Name a path from the repo root.")
    path = _real_path_inside(repo, repo / chosen)
    if path is None:
        raise _Refused(
            f"{chosen} leads out of the repo, by .., by an absolute path or through a link. "
            "Name a path inside the repo, from its root."
        )
    return path


# The one guard on every path a tool opens. realpath, not Path.resolve, because a link that loops
# must come back as a path that fails to open, not as an exception.
def _real_path_inside(repo: Path, path: Path) -> Path | None:
    """The path with every link followed, or None when that leads out of the repo."""
    real_path = os.path.realpath(path)
    return Path(real_path) if is_inside(real_path, str(repo)) else None


# Links to folders are not followed, so the walk can neither loop nor leave. A folder that cannot
# be read hides only its own files, so the walk passes over it.
def _files_under(repo: Path, folder: Path) -> list[_RepoFile]:
    """Every regular file under the folder whose real path is inside the repo, sorted."""
    found: list[_RepoFile] = []
    for parent, _, names in os.walk(folder):
        for name in names:
            path = Path(parent, name)
            real_path = _real_path_inside(repo, path)
            if real_path is not None and _kind_of(real_path) == "file":
                found.append(_RepoFile(name=path.relative_to(repo).as_posix(), real_path=real_path))
    return sorted(found, key=lambda file: file.name)


_Kind = Literal["folder", "file", "other", "missing"]


# os.path answers False where Path.is_file can raise, such as for a name in a folder that can be
# listed but not entered. Anything but a regular file is "other": a FIFO would block a read.
def _kind_of(path: Path) -> _Kind:
    if os.path.isdir(path):
        return "folder"
    if os.path.isfile(path):
        return "file"
    return "other" if os.path.lexists(path) else "missing"


@dataclass(frozen=True)
class _Start:
    """The part of a file the tools read."""

    data: bytes
    size: int
    is_cut: bool

    @property
    def is_binary(self) -> bool:
        return b"\x00" in self.data

    @property
    def text(self) -> str:
        return self.data.decode("utf-8", errors="replace")


def _read_start(path: Path) -> _Start:
    with path.open("rb") as file:
        size = os.fstat(file.fileno()).st_size
        data = file.read(READ_LIMIT_BYTES + 1)
    if len(data) <= READ_LIMIT_BYTES:
        return _Start(data=data, size=size, is_cut=False)
    return _Start(data=_whole_lines(data[:READ_LIMIT_BYTES]), size=size, is_cut=True)


def _whole_lines(data: bytes) -> bytes:
    last_break = data.rfind(b"\n")
    return data if last_break == -1 else data[: last_break + 1]


# Split at the line feed alone, as compilers count lines, so a number here is the number a tool
# reports for the same line. The carriage return of a CRLF ending belongs to the break.
def _lines(text: str) -> list[str]:
    lines = text.split("\n")
    if lines[-1] == "":
        lines.pop()
    return [line.removesuffix("\r") for line in lines]


def _shortened(line: str) -> str:
    if len(line) <= GREP_LINE_LIMIT:
        return line
    return f"{line[:GREP_LINE_LIMIT]} [ARDE cut this line at {GREP_LINE_LIMIT} characters]"


# clean_repo_text keeps line breaks and tabs, and in a name either one would make a line of the
# list that names no file.
def _on_one_line(name: str) -> str:
    return name.replace("\n", "\\x0a").replace("\t", "\\x09")

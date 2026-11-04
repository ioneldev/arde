"""A patch of whole-file changes, in git's format, that git apply turns back into the same bytes.

Lines are split at LF alone, so a CR before it, or anywhere else in a line, stays part of the
line, and a CRLF file is patched as CRLF. Bytes that are not UTF-8 pass through unchanged.
"""

import difflib
import re
from collections.abc import Sequence
from dataclasses import dataclass

_DEV_NULL = "/dev/null"
_NEW_FILE_MODE = "100644"
_NO_NEWLINE_MARK = "\\ No newline at end of file\n"
_LINE = re.compile(r"[^\n]*\n|[^\n]+\Z")
# git quotes a name that holds one of these, and reads it back only so.
_QUOTED_ESCAPES = {'"': '\\"', "\\": "\\\\", "\t": "\\t", "\n": "\\n", "\r": "\\r"}
# surrogateescape, so a byte that is not UTF-8 decodes to a stand-in and encodes back to itself.
_BYTE_EXACT = "surrogateescape"


@dataclass(frozen=True)
class FileChange:
    # From the repo root, with / between folders.
    path: str
    # None when the file does not exist on that side.
    before: bytes | None
    after: bytes | None


def unified_patch(changes: Sequence[FileChange]) -> bytes:
    """One patch for every change that changes something, in the order given."""
    return "".join(_file_patch(change) for change in changes).encode("utf-8", _BYTE_EXACT)


def _file_patch(change: FileChange) -> str:
    if change.before == change.after:
        return ""
    header = [f"diff --git {_name('a/', change.path)} {_name('b/', change.path)}\n"]
    if change.before is None:
        header.append(f"new file mode {_NEW_FILE_MODE}\n")
    if change.after is None:
        header.append(f"deleted file mode {_NEW_FILE_MODE}\n")
    hunks = difflib.unified_diff(
        _lines(change.before),
        _lines(change.after),
        fromfile=_DEV_NULL if change.before is None else _name("a/", change.path),
        tofile=_DEV_NULL if change.after is None else _name("b/", change.path),
    )
    return "".join(header) + "".join(_marked(line) for line in hunks)


def _lines(content: bytes | None) -> list[str]:
    if content is None:
        return []
    return _LINE.findall(content.decode("utf-8", _BYTE_EXACT))


# difflib writes a last line with no LF as it is. git needs the line ended and marked.
def _marked(line: str) -> str:
    return line if line.endswith("\n") else f"{line}\n{_NO_NEWLINE_MARK}"


def _name(prefix: str, path: str) -> str:
    named = prefix + path
    if not any(_needs_quoting(character) for character in named):
        return named
    return '"' + "".join(_escaped(character) for character in named) + '"'


def _needs_quoting(character: str) -> bool:
    return character in _QUOTED_ESCAPES or ord(character) < 0x20 or ord(character) == 0x7F


def _escaped(character: str) -> str:
    if character in _QUOTED_ESCAPES:
        return _QUOTED_ESCAPES[character]
    if _needs_quoting(character):
        return f"\\{ord(character):03o}"
    return character

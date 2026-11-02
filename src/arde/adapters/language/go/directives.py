"""Comments that are code. The AST comparison ignores every comment, so these are checked here.

Doubt answers "this may change behaviour": a wrong answer of that kind costs a fraction of a cent
in a model call, and the opposite wrong answer ignores a real change.
"""

import re
from pathlib import Path

# Wider than the compiler's own list, from the directive experiment. It is searched for, not
# matched from the start of the line, so a trailing //go:noinline and a /*line*/ in the middle of
# a line count too. The forms: //go:build and the other //word:word, //line, //export, // +build,
# /*line, // #cgo, and the // Output: lines that decide whether an example runs.
_DIRECTIVE = re.compile(
    r"//[a-z0-9]+:[a-z0-9]|//(line|export)\s|//\s*\+build|/\*line\s|//\s*#cgo"
    r"|//\s*(unordered\s+)?output:",
    re.IGNORECASE,
)
# go/build reads a // +build block only when a blank line follows it.
_PLUS_BUILD = re.compile(r"//\s*\+build")

# go test reads the expected output of an example from comments inside its body, and a note
# beside them can change what it reads. Any comment edit in such a file is treated as real.
_EXAMPLE_FUNCTION = re.compile(r"^func\s+Example", re.MULTILINE)

# The comment before this import is C source that cgo compiles. Any "C" or `C` in the file is
# enough to doubt every comment in it: the import can share a line with other code or comments.
_C_IMPORT_TEXT = re.compile(r'"C"|`C`')


def is_directive(line: str) -> bool:
    return _DIRECTIVE.search(line) is not None


def read_source(path: Path) -> str:
    # utf-8-sig: a byte order mark is not part of line one, and go/build ignores it too.
    return path.read_text(encoding="utf-8-sig", errors="replace")


def holds_example(source: str) -> bool:
    return _EXAMPLE_FUNCTION.search(source) is not None


def may_import_c(source: str) -> bool:
    return _C_IMPORT_TEXT.search(source) is not None


def comment_edit_may_change_behaviour(old_source: str, new_source: str) -> bool:
    """True when a difference that the AST comparison ignores could still matter."""
    return (
        any(holds_example(source) or may_import_c(source) for source in (old_source, new_source))
        or anchored_directives(old_source) != anchored_directives(new_source)
        # A new /* can swallow code and directives up to the next */, wherever it closes.
        or old_source.count("/*") != new_source.count("/*")
    )


# A directive means something only for the code it sits above: the same //go:embed line moved from
# one variable to the next embeds another file. So the line is kept with the code it applies to.
def anchored_directives(source: str) -> list[tuple[str, str]]:
    lines = [line.strip() for line in source.replace("\r\n", "\n").split("\n")]
    return [
        (line, _anchor_of(line, lines[number + 1 :]))
        for number, line in enumerate(lines)
        if is_directive(line)
    ]


def _anchor_of(directive: str, lines_below: list[str]) -> str:
    if _PLUS_BUILD.search(directive):
        return lines_below[0] if lines_below else ""
    return next((line for line in lines_below if line and not line.startswith("//")), "")

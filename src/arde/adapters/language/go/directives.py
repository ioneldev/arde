"""The comment-only rule, as an allow-list.

Take the ordinary comment lines and the tags ARDE adds out of both versions, and what is left must
match. A list of dangerous comment forms is never complete (two rounds left 26 wrong answers), so
every other difference counts as real. A wrong "real" costs a fraction of a cent in a model call,
and a wrong "comment-only" ignores a change.
"""

import re
from pathlib import Path

# A whole line: indentation, //, then whitespace or the end. "//word" is how every directive is
# written, so it is not ordinary. The spaced forms below are read by the compiler or go test too.
_LINE_COMMENT = re.compile(r"[ \t]*//(?:[ \t].*)?")
_SPACED_DIRECTIVE = re.compile(
    r"[ \t]*//\s*(?:\+build|#cgo|(?:unordered\s+)?output:)", re.IGNORECASE
)
# A field that had no tag is left with empty backticks, and gofmt realigns its neighbours' columns.
_ALLOWED_TAG = re.compile(r'oneOf:"[^"]*"|binding:"optional"')
_PADDED_BACKTICKS = re.compile(r"`\s*(.*?)\s*`")


def read_source(path: Path) -> str:
    # utf-8-sig: a byte order mark is not part of line one, and go/build ignores it too.
    return path.read_text(encoding="utf-8-sig", errors="replace")


def is_ordinary_comment(line: str) -> bool:
    return (
        _LINE_COMMENT.fullmatch(line) is not None
        and _SPACED_DIRECTIVE.match(line) is None
        and "/*" not in line
        and "*/" not in line
    )


def only_ordinary_comments_differ(old_source: str, new_source: str, *, is_test_file: bool) -> bool:
    # In a file that may import C, the preamble is C source. In a test file, an example reads its
    # expected output from comments. Plain substrings: a pattern would be one more list to miss.
    for source in (old_source, new_source):
        if '"C"' in source or "`C`" in source or (is_test_file and "Example" in source):
            return False
    return _without_ordinary_comments(old_source) == _without_ordinary_comments(new_source)


def _without_ordinary_comments(source: str) -> list[str]:
    lines = source.replace("\r\n", "\n").split("\n")
    return [_kept(line) for line in lines if not is_ordinary_comment(line)]


def _kept(line: str) -> str:
    if line.lstrip().startswith(("//", "/*")):
        return line
    without_tags = _PADDED_BACKTICKS.sub(
        lambda tag: f"`{tag[1]}`" if tag[1] else "", _ALLOWED_TAG.sub("", line)
    )
    return " ".join(without_tags.split())

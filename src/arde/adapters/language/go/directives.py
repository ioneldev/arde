"""The comment-only rule, as an allow-list.

Take the ordinary comment lines and the tags ARDE adds out of both versions, and what is left must
match. A list of dangerous comment forms can never be complete (two rounds of patches left 26
wrong answers), so every other difference counts as real: a block comment, a trailing comment, a
blank line, a directive. A wrong "real" costs a fraction of a cent in a model call, and a wrong
"comment-only" ignores a change.
"""

import re
from pathlib import Path

# A whole line: optional indentation, //, then whitespace or the end of the line. "//word" with no
# space is how every directive is written, so it is not ordinary.
_LINE_COMMENT = re.compile(r"[ \t]*//(?:[ \t].*)?")
# The directive forms that do have a space, which the compiler or go test read all the same.
_SPACED_DIRECTIVE = re.compile(
    r"[ \t]*//\s*(?:\+build|#cgo|(?:unordered\s+)?output:)", re.IGNORECASE
)

# The tags ARDE adds. A field that had no tag is left with empty backticks, and gofmt realigns the
# columns of its neighbours, which the whitespace collapse takes care of.
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
    """True when nothing differs except ordinary comment lines and ARDE's tags."""
    if any(
        _may_change_what_a_comment_means(source, is_test_file)
        for source in (old_source, new_source)
    ):
        return False
    return _without_ordinary_comments(old_source) == _without_ordinary_comments(new_source)


# Any comment edit counts as real in a file that may import C, whose preamble is C source, and in
# a test file with an example, whose expected output is read from comments. Plain substrings,
# because a pattern for either would be one more list that can miss a form.
def _may_change_what_a_comment_means(source: str, is_test_file: bool) -> bool:
    return '"C"' in source or "`C`" in source or (is_test_file and "Example" in source)


def _without_ordinary_comments(source: str) -> list[str]:
    lines = source.replace("\r\n", "\n").split("\n")
    return [_kept(line) for line in lines if not is_ordinary_comment(line)]


def _kept(line: str) -> str:
    if line.lstrip().startswith(("//", "/*")):
        return line
    return _collapsed(_ALLOWED_TAG.sub("", line))


def _collapsed(code_line: str) -> str:
    without_empty_tags = _PADDED_BACKTICKS.sub(
        lambda tag: f"`{tag[1]}`" if tag[1] else "", code_line
    )
    return " ".join(without_empty_tags.split())

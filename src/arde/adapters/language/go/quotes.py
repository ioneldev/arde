"""What a quote from a repo file holds: code, a comment, or the text of a string literal.

Go source and the module files are read with Go's lexical rules, and the Makefile with its comments
and quoted strings. Each counts only where it shapes the service: Go source in a file the service
is built from, the module files at the repo root, and the one Makefile make reads there. Code
planted in test data, a vendored copy, a file the build leaves out, another program or another
Makefile is no evidence about the service. Any other file is not source this adapter reads.

Every choice errs towards free text. A Go comment or literal that never ends runs to the end of
its line or of the file. In the Makefile, a line is free text from its first # or quote to its
end, through every line a backslash continues it on, with no escape read, so planted text can
never pass for code by leaving a quote open or escaping one.
"""

import re
from pathlib import PurePosixPath
from typing import NamedTuple

from arde.adapters.language.go.makefile import MAKEFILE_NAMES
from arde.application.ports import QuoteContent, SourceQuote

# The tokens of Go that hold free text. A rune literal is lexed too, so that '"' opens no string.
# An interpreted string or a rune that never closes ends with its line, as the compiler reports it.
_GO_FREE_TEXT = re.compile(
    r"(?P<comment>//[^\n]*|/\*.*?(?:\*/|\Z))"
    r'|(?P<literal>"(?:[^"\\\n]|\\.)*"?|`[^`]*`?|\'(?:[^\'\\\n]|\\.)*\'?)',
    re.DOTALL,
)
# The rest of a logical line of the Makefile: a backslash before a line break carries it on.
_REST_OF_LINE = r"(?:\\\n|[^\n])*"
# A # outside a recipe starts make's comment, and inside one the shell's. A quote either belongs to
# a value or goes to the shell, as the argument of echo does.
_MAKE_FREE_TEXT = re.compile(rf"(?P<comment>#{_REST_OF_LINE})|(?P<literal>[\"']{_REST_OF_LINE})")

_MODULE_FILES = frozenset({"go.mod", "go.work"})
_MAKE_SUFFIX = ".mk"


class _Syntax(NamedTuple):
    lexer: re.Pattern[str]
    in_the_build: bool


def quote_content(quote: SourceQuote) -> QuoteContent:
    if not _is_listed_form(quote.file):
        return "not a path in the repo"
    syntax = _syntax_of(quote)
    if syntax is None:
        return "not source"
    if not syntax.in_the_build:
        return "outside the build"
    free_text = _free_text_spans(quote.source, syntax.lexer)
    readings = [_reading(quote.source, place, free_text) for place in quote.places]
    if "code" in readings:
        return "code"
    return "string literal" if "string literal" in readings else "comment"


# The form the declarations and the agent's tools write a path in: from the repo root, with / alone
# between folders. Any other spelling is refused rather than rewritten, so the path checked
# against the build is the path that was read.
def _is_listed_form(file: str) -> bool:
    path = PurePosixPath(file)
    return (
        "\\" not in file
        and file not in ("", ".")
        and not path.is_absolute()
        and ".." not in path.parts
        and path.as_posix() == file
    )


def _syntax_of(quote: SourceQuote) -> _Syntax | None:
    file = quote.file
    name = PurePosixPath(file).name
    if name.endswith(".go"):
        return _Syntax(_GO_FREE_TEXT, in_the_build=file in quote.build_files)
    if name in _MODULE_FILES:
        return _Syntax(_GO_FREE_TEXT, in_the_build=file == name)
    if name in MAKEFILE_NAMES or name.endswith(_MAKE_SUFFIX):
        return _Syntax(_MAKE_FREE_TEXT, in_the_build=file == _makefile_make_reads(quote.root_files))
    return None


# make reads the first of its names it finds at the root, as make_targets_of does.
def _makefile_make_reads(root_files: frozenset[str]) -> str | None:
    return next((name for name in MAKEFILE_NAMES if name in root_files), None)


# (start, end, kind) for each comment and literal, in file order.
type _Span = tuple[int, int, str]


def _free_text_spans(source: str, lexer: re.Pattern[str]) -> list[_Span]:
    return [(found.start(), found.end(), str(found.lastgroup)) for found in lexer.finditer(source)]


def _reading(source: str, place: tuple[int, int], free_text: list[_Span]) -> QuoteContent:
    start, end = place
    inside = [span for span in free_text if span[0] < end and span[1] > start]
    covered = start
    for span_start, span_end, _ in inside:
        if source[covered:span_start].strip():
            return "code"
        covered = max(covered, span_end)
    if source[covered:end].strip():
        return "code"
    kinds = {kind for _, _, kind in inside}
    return "string literal" if "literal" in kinds else "comment"

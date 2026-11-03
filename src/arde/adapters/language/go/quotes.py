"""What a quote from a repo file holds: code, a comment, or the text of a string literal.

Go source and the module files are read with Go's lexical rules, and a Makefile with its comments
and quoted strings. Any other file is not source this adapter reads, and Go source counts only in
a file the service is built from, so code planted in test data, a vendored copy, a file the build
leaves out or another program is no evidence about the service. The lexer errs towards free text:
a comment or a literal that never ends runs to the end of its line or of the file, so planted text
can never pass for code by leaving it open.
"""

import re
from pathlib import PurePosixPath

from arde.adapters.language.go.makefile import MAKEFILE_NAMES
from arde.application.ports import QuoteContent, SourceQuote

# The tokens of Go that hold free text. A rune literal is lexed too, so that '"' opens no string.
# An interpreted string or a rune that never closes ends with its line, as the compiler reports it.
_GO_FREE_TEXT = re.compile(
    r"(?P<comment>//[^\n]*|/\*.*?(?:\*/|\Z))"
    r'|(?P<literal>"(?:[^"\\\n]|\\.)*"?|`[^`]*`?|\'(?:[^\'\\\n]|\\.)*\'?)',
    re.DOTALL,
)
# A # outside a recipe starts a comment, and inside one it starts the shell's. Either way, what
# follows is free text, so every # is read as the start of a comment. A quoted string on any line is
# free text too, as the argument of echo is: make passes a recipe's quotes to the shell, and a
# value's quotes are part of the value.
_MAKE_FREE_TEXT = re.compile(r"(?P<comment>#[^\n]*)|(?P<literal>\"[^\"\n]*\"?|'[^'\n]*'?)")

_GO_FILES = frozenset({"go.mod", "go.work"})
_MAKE_SUFFIX = ".mk"


def quote_content(quote: SourceQuote) -> QuoteContent:
    file = _as_listed(quote.file)
    lexer = _lexer_for(file)
    if lexer is None:
        return "not source"
    if file.endswith(".go") and file not in quote.build_files:
        return "outside the build"
    free_text = _free_text_spans(quote.source, lexer)
    readings = [_reading(quote.source, place, free_text) for place in quote.places]
    if "code" in readings:
        return "code"
    return "string literal" if "string literal" in readings else "comment"


# The declarations name a file from the repo root with forward slashes and no leading "./".
def _as_listed(file: str) -> str:
    return PurePosixPath(file.replace("\\", "/")).as_posix()


def _lexer_for(file: str) -> re.Pattern[str] | None:
    name = PurePosixPath(file).name
    if name.endswith(".go") or name in _GO_FILES:
        return _GO_FREE_TEXT
    if name in MAKEFILE_NAMES or name.endswith(_MAKE_SUFFIX):
        return _MAKE_FREE_TEXT
    return None


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

"""The parts of a Markdown page a reader can act on: its code blocks and its link destinations.

Read by hand, with no Markdown library. Where the reading is unsure, it finds more rather than
less, because what it misses is never checked.
"""

import re
from collections.abc import Iterator
from dataclasses import dataclass

_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
# Four columns, the indentation of a code block.
_CODE_INDENT = 4

_INLINE_LINK = re.compile(r"!?\[(?:[^\[\]]|\[[^\[\]]*\])*\]\(\s*(<[^<>\n]*>|[^\s)]+)")
# A footnote, [^1]: text, is no link, so a label that starts with ^ is left out.
_LINK_DEFINITION = re.compile(r"^ {0,3}\[(?!\^)[^\]]+\]:[ \t]*(<[^<>\n]*>|\S+)", re.MULTILINE)
_AUTOLINK = re.compile(r"<([A-Za-z][A-Za-z0-9+.-]{1,31}:[^\s<>]*)>")
_ATTRIBUTE = re.compile(
    r"""\b(?:href|src)\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'=<>`]+))""", re.IGNORECASE
)
# A backslash before ASCII punctuation in a destination is dropped when the page is shown.
_ESCAPED_PUNCTUATION = re.compile(r"\\([!-/:-@\[-`{-~])")


@dataclass(frozen=True)
class CodeBlock:
    # The first word of a fenced block's info string, lower case, or empty.
    language: str
    lines: tuple[str, ...]


def code_blocks(text: str) -> Iterator[CodeBlock]:
    """Every fenced block, and every indented one, in the order they come."""
    lines = text.splitlines()
    index = 0
    # An indented line starts a block only where no paragraph is open, as after a blank line.
    may_start_indented = True
    while index < len(lines):
        line = lines[index]
        opening = _FENCE.match(line)
        if opening is not None:
            block, index = _fenced(lines, start=index, opening=opening)
            yield block
            may_start_indented = True
        elif may_start_indented and line.strip() and _indent(line) >= _CODE_INDENT:
            block, index = _indented(lines, start=index)
            yield block
        else:
            may_start_indented = not line.strip()
            index += 1


def link_destinations(text: str) -> Iterator[str]:
    """Where each link, image, autolink, href and src of the page points, as a browser reads it."""
    for pattern in (_INLINE_LINK, _LINK_DEFINITION, _AUTOLINK):
        for match in pattern.finditer(text):
            yield _as_shown(match.group(1))
    for match in _ATTRIBUTE.finditer(text):
        yield _as_shown(next(value for value in match.groups() if value is not None))


def _fenced(lines: list[str], *, start: int, opening: re.Match[str]) -> tuple[CodeBlock, int]:
    fence, info = opening.groups()
    closing = re.compile(rf"^ {{0,3}}{re.escape(fence[0])}{{{len(fence)},}}\s*$")
    end = start + 1
    while end < len(lines) and closing.match(lines[end]) is None:
        end += 1
    words = info.split()
    block = CodeBlock(
        language=words[0].lower() if words else "", lines=tuple(lines[start + 1 : end])
    )
    return block, end + 1


def _indented(lines: list[str], *, start: int) -> tuple[CodeBlock, int]:
    end = start
    while end < len(lines) and (not lines[end].strip() or _indent(lines[end]) >= _CODE_INDENT):
        end += 1
    body = tuple(line.expandtabs(_CODE_INDENT)[_CODE_INDENT:] for line in lines[start:end])
    return CodeBlock(language="", lines=body), end


def _indent(line: str) -> int:
    expanded = line.expandtabs(_CODE_INDENT)
    return len(expanded) - len(expanded.lstrip(" "))


def _as_shown(destination: str) -> str:
    if destination.startswith("<") and destination.endswith(">"):
        destination = destination[1:-1]
    return _ESCAPED_PUNCTUATION.sub(r"\1", destination).strip()

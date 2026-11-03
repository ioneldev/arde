"""An example reply for the API reference: the body a probe recorded, printed for a person.

Only the layout changes. Every value is printed as the body holds it: a number keeps its digits
and a key that comes twice stays twice, where parsing into Python values would round the one and
merge the other.
"""

import json
import re
from collections.abc import Iterator
from dataclasses import dataclass

from arde.domain.base import UNPRINTABLE_CHARACTERS
from arde.domain.facts import EndpointFact, ProbeCitation, ProbeRecord, Wiki

LINE_LIMIT = 40
KEPT_ITEMS = 2
_INDENT = "  "
_WILDCARD = re.compile(r"\{[^{}/]+\}")
_REST_WILDCARD = re.compile(r"\{[^{}/]+\.\.\.\}")
# A reply can hold any text. Written as escapes, an unprintable character cannot reorder what a
# reader sees, a lone surrogate cannot stop the page from being written as UTF-8, and a < cannot
# make the page hold an HTML tag, which the docs checks refuse.
_ESCAPED = re.compile(f"[<>&\\ud800-\\udfff{UNPRINTABLE_CHARACTERS}]")
_BMP_END = 0xFFFF


@dataclass(frozen=True)
class ReplyExample:
    json: str
    # Each array was cut to its first KEPT_ITEMS items, because the whole ran past LINE_LIMIT.
    cut: bool


class _Number(str):
    """A number as the body writes it."""


@dataclass(frozen=True)
class _Object:
    pairs: tuple[tuple[str, object], ...]


def reply_example(endpoint: EndpointFact, wiki: Wiki, *, status: int) -> ReplyExample | None:
    """The first recorded reply of the endpoint with this status and a JSON object or array for
    a body, from the probes it cites first. None when the wiki holds no such reply."""
    for probe in _probes_of(endpoint, wiki):
        document = _document(probe.body) if probe.status == status else None
        if document is not None:
            return _example(document)
    return None


def _probes_of(endpoint: EndpointFact, wiki: Wiki) -> Iterator[ProbeRecord]:
    cited = {
        (evidence.method, evidence.path_and_query)
        for evidence in endpoint.evidence
        if isinstance(evidence, ProbeCitation)
    }
    # The sort is stable, so each group keeps the wiki's order.
    cited_first = sorted(
        wiki.probes, key=lambda probe: (probe.method, probe.path_and_query) not in cited
    )
    method, pattern = endpoint.route.split(" ", 1)
    return (
        probe
        for probe in cited_first
        if probe.method == method and pattern_matches(pattern, probe.path_and_query)
    )


# A wildcard matches one segment that is not empty, and a trailing one with ... the rest.
def pattern_matches(pattern: str, path_and_query: str) -> bool:
    wanted = pattern.split("/")
    given = path_and_query.split("?", 1)[0].split("/")
    for index, part in enumerate(wanted):
        if _REST_WILDCARD.fullmatch(part):
            return True
        if index >= len(given):
            return False
        if _WILDCARD.fullmatch(part):
            if not given[index]:
                return False
        elif part != given[index]:
            return False
    return len(given) == len(wanted)


def _document(body: str) -> _Object | list[object] | None:
    try:
        parsed = json.loads(
            body,
            object_pairs_hook=lambda pairs: _Object(tuple(pairs)),
            parse_int=_Number,
            parse_float=_Number,
            parse_constant=_refuse_constant,
        )
    except (ValueError, RecursionError):
        return None
    return parsed if isinstance(parsed, _Object | list) else None


# NaN and Infinity are no JSON, though Python's parser takes them.
def _refuse_constant(name: str) -> object:
    raise ValueError(f"{name} is not JSON")


def _example(document: _Object | list[object]) -> ReplyExample:
    whole = _printed(document, depth=0, cut=False)
    if whole.count("\n") < LINE_LIMIT:
        return ReplyExample(json=whole, cut=False)
    shortened = _printed(document, depth=0, cut=True)
    return ReplyExample(json=shortened, cut=shortened != whole)


def _printed(value: object, *, depth: int, cut: bool) -> str:
    if isinstance(value, _Object):
        members = [
            f"{_string(key)}: {_printed(item, depth=depth + 1, cut=cut)}"
            for key, item in value.pairs
        ]
        return _laid_out(members, "{}", depth=depth)
    if isinstance(value, list):
        kept = value[:KEPT_ITEMS] if cut else value
        items = [_printed(item, depth=depth + 1, cut=cut) for item in kept]
        return _laid_out(items, "[]", depth=depth)
    if isinstance(value, _Number):
        return str(value)
    if isinstance(value, str):
        return _string(value)
    return json.dumps(value)


def _laid_out(members: list[str], brackets: str, *, depth: int) -> str:
    if not members:
        return brackets
    inner = _INDENT * (depth + 1)
    lines = ",\n".join(f"{inner}{member}" for member in members)
    return f"{brackets[0]}\n{lines}\n{_INDENT * depth}{brackets[1]}"


def _string(text: str) -> str:
    written = json.dumps(text, ensure_ascii=False)
    return _ESCAPED.sub(lambda found: _escape(found.group()), written)


# JSON escapes a character past the first plane as its two UTF-16 halves.
def _escape(character: str) -> str:
    point = ord(character)
    if point <= _BMP_END:
        return f"\\u{point:04x}"
    point -= _BMP_END + 1
    return f"\\u{0xD800 + (point >> 10):04x}\\u{0xDC00 + (point & 0x3FF):04x}"

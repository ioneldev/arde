"""The API reference and the error table, which code renders from facts, never a model.

Each param, response, field and error code is one table row, so a new one shows as one new row.
Fact text goes into a table cell, a heading, a list item or a paragraph of one line. A cell or a
heading holds no block, a block opened in a list item ends with the item, and a paragraph's first
mark is escaped, so no text a fact holds can open a block that swallows the rest of the page.
"""

import re
from collections import defaultdict
from collections.abc import Callable

from arde.application.ports import JsonShape
from arde.application.reply_bodies import Body, BodyRow, ReplyBodies, ReplyStruct, TypeLabel
from arde.application.reply_examples import KEPT_ITEMS, reply_example
from arde.domain.base import one_line
from arde.domain.facts import (
    EndpointFact,
    ParamFact,
    ResponseFact,
    RuleFact,
    UnreachableFact,
    Wiki,
)
from arde.domain.inventory import Inventory

# Statements and reasons may span lines, and a table cell or a list item holds one.
_PROSE_LIMIT = 10_000
_CODE_LIMIT = 200
_SUCCESS = range(200, 300)
_ERROR_FLOOR = 400

_INTRO = (
    "ARDE writes this page from the service's checked facts, so it changes only when they do. "
    "It shows every endpoint with its request, its responses and the JSON body of a success, "
    "then the body of the error replies and every error code. A field is named by its path from "
    "the body's root: a dot leads into an object, `[]` into each item of an array, and `*` into "
    "each value of an object whose keys are data."
)
_PARAM_HEADER = ("Parameter", "In", "Type", "Required", "Rules", "Description")
_RESPONSE_HEADER = ("Status", "When", "Error codes")
_BODY_HEADER = ("Field", "Type", "Always present", "Description")
_ERROR_HEADER = ("Code", "Status", "Returned by", "Note")
# A fact writes a param's type in the spec's words, and a reader of JSON knows these two longer.
_PARAM_JSON_TYPES = {"int": "integer", "bool": "boolean"}
_NO_PARAMS = "No parameters."
_CUT_NOTE = f"Arrays are cut to their first {KEPT_ITEMS} items."
_NOT_EXPANDED = "Its fields are not shown here."
_OTHER_STRUCTS_INTRO = (
    "No endpoint above reaches these structs through the types its responses declare, so each "
    "is listed here with its own fields."
)
# A line of its own opens a block when it starts with a mark such as a fence, #, -, > or 1., and
# [x]: makes it a link definition, which hides it. ASCII punctuation but the backslash, which the
# text escaping has already doubled.
_BLOCK_MARK = re.compile(r"(\d*)[!-/:-@\[\]-`{-~]")


def api_reference(
    wiki: Wiki, inventory: Inventory, *, json_shape: Callable[[str], JsonShape]
) -> str:
    """The body of the API reference page: the endpoints, the error replies, the notes, then the
    reply structs no endpoint reaches. json_shape says what a declared type is in JSON."""
    bodies = ReplyBodies.of(wiki, inventory, json_shape=json_shape)
    endpoints = _endpoints_by_path(wiki)
    sections = [_INTRO, "## Endpoints"]
    sections.extend(_endpoint(fact, wiki=wiki, bodies=bodies) for fact in endpoints)
    sections.append(_error_replies(endpoints, wiki=wiki, bodies=bodies))
    notes = [fact for fact in wiki.by_topic("api") if isinstance(fact, RuleFact)]
    if notes:
        sections.append("## Notes\n\n" + "\n".join(f"- {_prose(fact.statement)}" for fact in notes))
    # Last, because only once every body is written is it known which structs none reached.
    unreached = bodies.unreached()
    if unreached:
        sections.append(_other_structs(unreached, bodies=bodies))
    return "\n\n".join(sections)


def error_table(wiki: Wiki) -> str:
    """One row per error code and status the endpoints return, and one per code never returned."""
    rows = sorted([*_returned_codes(wiki), *_unreachable_codes(wiki)])
    return _table(_ERROR_HEADER, [(_code(code), *cells) for code, *cells in rows])


def _returned_codes(wiki: Wiki) -> list[tuple[str, str, str, str]]:
    routes_by_code: dict[tuple[str, int], set[str]] = defaultdict(set)
    for fact in wiki.facts:
        if isinstance(fact, EndpointFact):
            for response in fact.responses:
                for code in response.error_codes:
                    routes_by_code[(code, response.status)].add(fact.route)
    return [
        (code, str(status), ", ".join(_code(route) for route in sorted(routes)), "")
        for (code, status), routes in routes_by_code.items()
    ]


# The wiki holds no status for a code no request can reach, and no route returns it.
def _unreachable_codes(wiki: Wiki) -> list[tuple[str, str, str, str]]:
    return [
        (one_line(fact.error_code, _CODE_LIMIT), "", "", f"Never returned: {_prose(fact.reason)}")
        for fact in wiki.facts
        if isinstance(fact, UnreachableFact)
    ]


def _endpoints_by_path(wiki: Wiki) -> list[EndpointFact]:
    endpoints = [fact for fact in wiki.facts if isinstance(fact, EndpointFact)]
    return sorted(endpoints, key=lambda fact: tuple(reversed(fact.route.split(" ", 1))))


# An endpoint


def _endpoint(fact: EndpointFact, *, wiki: Wiki, bodies: ReplyBodies) -> str:
    blocks = [
        f"### {_code(fact.route)}",
        _paragraph(fact.summary),
        _request(fact.params),
        "#### Responses",
        _table(_RESPONSE_HEADER, [_response_row(response) for response in fact.responses]),
    ]
    for response in fact.responses:
        if response.status in _SUCCESS:
            blocks.extend(_success(fact, response, wiki=wiki, bodies=bodies))
    return "\n\n".join(block for block in blocks if block)


def _request(params: tuple[ParamFact, ...]) -> str:
    if not params:
        return _NO_PARAMS
    return "#### Request\n\n" + _table(_PARAM_HEADER, [_param_row(param) for param in params])


def _param_row(param: ParamFact) -> tuple[str, ...]:
    declared = str(param.declared_type)
    return (
        _code(param.name),
        _text(str(param.location)),
        _text(_PARAM_JSON_TYPES.get(declared, declared)),
        "yes" if param.required else "no",
        _rules(param),
        _text(param.description),
    )


def _rules(param: ParamFact) -> str:
    rules: list[str] = []
    low, high = param.minimum, param.maximum
    if low is not None and high is not None:
        rules.append(f"{low} to {high}")
    elif low is not None:
        rules.append(f"at least {low}")
    elif high is not None:
        rules.append(f"at most {high}")
    if param.enum:
        rules.append("one of " + ", ".join(_code(choice) for choice in param.enum))
    if param.default is not None:
        rules.append(f"default {_code(param.default)}")
    return "; ".join(rules)


def _response_row(response: ResponseFact) -> tuple[str, ...]:
    return (
        str(response.status),
        _text(response.description),
        ", ".join(_code(code) for code in response.error_codes),
    )


def _success(
    fact: EndpointFact, response: ResponseFact, *, wiki: Wiki, bodies: ReplyBodies
) -> list[str]:
    status = _code(str(response.status))
    body = bodies.of_response(response.declared_type, route=fact.route)
    blocks = [f"#### {status} response body", *_body_blocks(body)]
    example = reply_example(fact, wiki, status=response.status)
    if example is not None:
        blocks += [f"#### Example {status} response", _json_block(example.json)]
        if example.cut:
            blocks.append(_CUT_NOTE)
    return blocks


# The error replies, and the reply structs no endpoint reaches


# The error responses of every endpoint, once per struct their bodies are, so that a body many
# routes share is shown once.
def _error_replies(endpoints: list[EndpointFact], *, wiki: Wiki, bodies: ReplyBodies) -> str:
    groups: dict[ReplyStruct | str, list[tuple[EndpointFact, ResponseFact]]] = {}
    for fact in endpoints:
        for response in fact.responses:
            if response.status >= _ERROR_FLOOR:
                struct = bodies.struct_of_response(response.declared_type, route=fact.route)
                groups.setdefault(struct or response.declared_type, []).append((fact, response))
    blocks = ["## Error replies"]
    for replies in groups.values():
        fact, first = replies[0]
        statuses = sorted({response.status for _, response in replies})
        blocks.append(
            f"The {_listed([str(status) for status in statuses])} responses above reply with "
            f"{_code(first.declared_type)}:"
        )
        blocks.extend(_body_blocks(bodies.of_response(first.declared_type, route=fact.route)))
    blocks += ["### Error codes", error_table(wiki)]
    return "\n\n".join(blocks)


def _other_structs(structs: list[ReplyStruct], *, bodies: ReplyBodies) -> str:
    blocks = ["## Other reply structs", _OTHER_STRUCTS_INTRO]
    for struct in structs:
        blocks += [f"### {_code(struct.title)}", *_body_blocks(bodies.of_struct(struct))]
    return "\n\n".join(blocks)


def _listed(words: list[str]) -> str:
    return words[0] if len(words) == 1 else f"{', '.join(words[:-1])} and {words[-1]}"


# A body


def _body_blocks(body: Body) -> list[str]:
    blocks: list[str] = []
    if body.root is not None:
        blocks.append(f"Type of the body: {_label(body.root)}.")
    if body.rows:
        blocks.append(_body_table(body.rows))
    for shape in body.shapes:
        blocks.append(f"##### {_code(shape.path)} as {_code(shape.name)}")
        blocks.append(_NOT_EXPANDED if shape.rows is None else _body_table(shape.rows))
    return blocks


def _body_table(rows: tuple[BodyRow, ...]) -> str:
    return _table(
        _BODY_HEADER,
        [
            (
                _code(row.path),
                _label(row.type),
                "yes" if row.always_present else "no",
                _text(row.description),
            )
            for row in rows
        ],
    )


def _label(label: TypeLabel) -> str:
    return label.words + (_code(label.name) if label.name else "")


# The fence is longer than any run of backticks in the reply, so the reply cannot close it.
def _json_block(json_text: str) -> str:
    fence = "`" * max(3, longest_backtick_run(json_text) + 1)
    return f"{fence}json\n{json_text}\n{fence}"


# Markdown


def _table(header: tuple[str, ...], rows: list[tuple[str, ...]]) -> str:
    lines = [_row(header), "|" + "---|" * len(header), *(_row(row) for row in rows)]
    return "\n".join(lines)


# GitHub's tables read an escaped pipe as a pipe, inside a code span too.
def _row(cells: tuple[str, ...]) -> str:
    return "| " + " | ".join(cell.replace("|", "\\|") for cell in cells) + " |"


# A backslash is doubled, so one written before a pipe cannot cancel the escape _row puts there,
# and a < is escaped, so it cannot open an HTML comment or tag.
def _text(text: str) -> str:
    return text.replace("\\", "\\\\").replace("<", "\\<")


def _prose(text: str) -> str:
    return _text(one_line(text, _PROSE_LIMIT))


def _paragraph(text: str) -> str:
    escaped = _text(text.strip())
    mark = _BLOCK_MARK.match(escaped)
    if mark is None:
        return escaped
    return f"{mark.group(1)}\\{escaped[mark.end(1) :]}"


# A fence longer than any run of backticks inside, so a name holding one stays one code span.
def _code(text: str) -> str:
    fence = "`" * (longest_backtick_run(text) + 1)
    padding = " " if text.startswith("`") or text.endswith("`") else ""
    return f"{fence}{padding}{text}{padding}{fence}"


def longest_backtick_run(text: str) -> int:
    return max((len(run) for run in re.findall(r"`+", text)), default=0)

"""The API reference and the error table, which code renders from facts, never a model.

Each param, response, field and error code is one table row, so a new one shows as one new row.
Every text a fact holds goes into a table cell, a heading or a list item, never on a line of its
own, where a fence it opened would swallow the rest of the page. A cell or a heading holds no
block at all, and a block opened in a list item ends with the item.
"""

import re
from collections import defaultdict

from arde.domain.base import one_line, printable_name
from arde.domain.errors import ArdeError
from arde.domain.facts import (
    EndpointFact,
    FieldFact,
    ParamFact,
    PolymorphicFact,
    ResponseFact,
    RuleFact,
    UnreachableFact,
    Wiki,
)
from arde.domain.inventory import Inventory, ResponseField
from arde.domain.report import ExitCode

# Statements and reasons may span lines, and a table cell or a list item holds one.
_PROSE_LIMIT = 10_000
_CODE_LIMIT = 200

_INTRO = (
    "ARDE writes this page from the service's checked facts, so it changes only when they do. "
    "It lists every endpoint with its parameters and responses, the fields of the replies, and "
    "the error codes."
)
_PARAM_HEADER = (
    "Parameter",
    "In",
    "Type",
    "Required",
    "Description",
    "Minimum",
    "Maximum",
    "Choices",
    "Default",
)
_RESPONSE_HEADER = ("Status", "Type", "Description", "Error codes")
_FIELD_HEADER = ("Struct", "Field", "JSON key", "Type", "May be absent", "Description")
_ERROR_HEADER = ("Code", "Status", "Returned by", "Note")


class ReferenceTableError(ArdeError):
    """The wiki describes something the inventory cannot place, so no row can be written."""

    def __init__(self, message: str, *, next_action: str) -> None:
        super().__init__(message, next_action=next_action, exit_code=ExitCode.CHECKS_FAILED)


def api_reference(wiki: Wiki, inventory: Inventory) -> str:
    """The body of the API reference page: endpoints, reply fields, error codes, then notes."""
    sections = [
        _INTRO,
        "## Endpoints",
        *(_endpoint(fact) for fact in _endpoints_by_path(wiki)),
        f"## Response fields\n\n{_field_table(wiki, inventory)}",
        f"## Error codes\n\n{error_table(wiki)}",
    ]
    notes = [fact for fact in wiki.by_topic("api") if isinstance(fact, RuleFact)]
    if notes:
        sections.append("## Notes\n\n" + "\n".join(f"- {_prose(fact.statement)}" for fact in notes))
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


def _endpoint(fact: EndpointFact) -> str:
    return "\n\n".join(
        [
            f"### {_code(fact.route)}: {_text(fact.summary)}",
            _table(_PARAM_HEADER, [_param_row(param) for param in fact.params]),
            _table(_RESPONSE_HEADER, [_response_row(response) for response in fact.responses]),
        ]
    )


def _param_row(param: ParamFact) -> tuple[str, ...]:
    return (
        _code(param.name),
        _text(str(param.location)),
        _text(str(param.declared_type)),
        "yes" if param.required else "no",
        _text(param.description),
        _number(param.minimum),
        _number(param.maximum),
        ", ".join(_code(choice) for choice in param.enum),
        "" if param.default is None else _code(param.default),
    )


def _response_row(response: ResponseFact) -> tuple[str, ...]:
    return (
        str(response.status),
        _code(response.declared_type),
        _text(response.description),
        ", ".join(_code(code) for code in response.error_codes),
    )


def _field_table(wiki: Wiki, inventory: Inventory) -> str:
    described: dict[int, list[FieldFact | PolymorphicFact]] = defaultdict(list)
    for fact in wiki.facts:
        if isinstance(fact, FieldFact | PolymorphicFact):
            described[_inventory_index(fact, inventory)].append(fact)
    rows = [
        _field_row(inventory.response_fields[index], described[index])
        for index in sorted(described)
    ]
    return _table(_FIELD_HEADER, rows)


def _field_row(field: ResponseField, facts: list[FieldFact | PolymorphicFact]) -> tuple[str, ...]:
    descriptions = [fact.description for fact in facts if isinstance(fact, FieldFact)]
    variants = [fact.variants for fact in facts if isinstance(fact, PolymorphicFact)]
    field_type = _code(field.declared_type)
    if variants:
        field_type += ", one of " + ", ".join(_code(variant) for variant in variants[0])
    return (
        _code(field.struct),
        _code(field.name),
        _code(field.json_name),
        field_type,
        "yes" if field.may_be_absent else "no",
        _text(descriptions[0]) if descriptions else "",
    )


# A fact names a field by its struct and its name in the source, and two packages can each declare
# a struct by one name: the fact's links then say which file holds the one it means.
def _inventory_index(fact: FieldFact | PolymorphicFact, inventory: Inventory) -> int:
    named = [
        index
        for index, field in enumerate(inventory.response_fields)
        if (field.struct, field.name) == (fact.struct, fact.field)
    ]
    linked_files = {declaration.split("::", 1)[0] for declaration in fact.links.declarations}
    linked_files.update(fact.links.files)
    if len(named) > 1:
        named = [index for index in named if inventory.response_fields[index].file in linked_files]
    if len(named) == 1:
        return named[0]
    field_name = f"{fact.struct}.{fact.field}"
    if not named:
        raise ReferenceTableError(
            f"The wiki describes field {field_name}, which no reply of the service holds.",
            next_action="Remove the fact from the wiki, then run again.",
        )
    raise ReferenceTableError(
        f"The wiki describes field {field_name}, which several structs of that name hold, and "
        f"fact {printable_name(fact.id)} does not link the declaration of the one it means.",
        next_action="Link the fact to the declaration of its struct, then run again.",
    )


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


# A fence longer than any run of backticks inside, so a name holding one stays one code span.
def _code(text: str) -> str:
    fence = "`" * (longest_backtick_run(text) + 1)
    padding = " " if text.startswith("`") or text.endswith("`") else ""
    return f"{fence}{padding}{text}{padding}{fence}"


def longest_backtick_run(text: str) -> int:
    return max((len(run) for run in re.findall(r"`+", text)), default=0)


def _number(value: int | None) -> str:
    return "" if value is None else str(value)

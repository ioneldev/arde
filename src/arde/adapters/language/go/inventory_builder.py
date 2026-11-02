"""Turns what goinspect reports into the inventory. Pure: it reads no file and runs nothing."""

import re
from collections.abc import Callable, Iterable
from typing import Any, NamedTuple, Protocol

from pydantic import TypeAdapter, ValidationError

from arde.adapters.language.go.goinspect_client import TOO_LONG_TO_PRINT, CallMatch, FieldReply
from arde.domain.base import Frozen, describe_problems, one_line, printable_name
from arde.domain.errors import ArdeError
from arde.domain.inventory import (
    INLINE_HANDLER,
    PATH_LIMIT,
    STATUS_LIMIT,
    ErrorCode,
    Inventory,
    NameText,
    Param,
    ResponseField,
    Route,
)
from arde.domain.report import ExitCode

_HTTP_VERBS = frozenset(
    {"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "CONNECT", "OPTIONS", "TRACE"}
)
_METHOD_THEN_PATH = re.compile(r"([A-Z]+)[ \t]+(.*)", re.DOTALL)
_PLAIN_NAME = re.compile(r"[^\W\d]\w*(?:\.[^\W\d]\w*)*")
_TAG_ENTRY = re.compile(r' *([^\s:"\x7f]+):"((?:[^"\\]|\\.)*)"')
# Where the text of an argument is cut when it goes into a message, and into a dropped line.
_SHOWN_LENGTH = 80
_DROPPED_TEXT_LENGTH = 40
# How much of the reason a dropped line keeps. Several fields of one model can fail together.
_DROPPED_REASON_LENGTH = 250
_NAME_TEXT: TypeAdapter[str] = TypeAdapter(NameText)


class SourceReading(Frozen):
    matches: tuple[CallMatch, ...]
    fields: tuple[FieldReply, ...]
    make_targets: tuple[str, ...]


# Two causes need two pieces of advice: an argument that is not a literal, and a literal that is
# not a pattern ARDE reads.
USE_A_LITERAL = (
    "Change the route pattern in the profile so its path argument points at a string literal, "
    "or register the route with one, then run again."
)
USE_A_PATH_PATTERN = (
    "ARDE reads route patterns of the form [METHOD ]/path, with no host. Register the route "
    "that way, or change the route pattern in the profile to match one that does, then run again."
)
USE_A_READABLE_ROUTE = (
    f"ARDE reads a route as one line of plain text, with a path of at most {PATH_LIMIT} "
    "characters. Register the route that way, then run again."
)


class RouteNotUnderstoodError(ArdeError):
    def __init__(self, call: CallMatch, problem: str, *, next_action: str) -> None:
        super().__init__(
            f"Cannot read the route registered at {printable_name(call.file)}:{call.line}: "
            f"{problem}.",
            next_action=next_action,
            exit_code=ExitCode.DISCOVERY_NEEDS_INPUT,
        )


class NoRoutesError(ArdeError):
    def __init__(self) -> None:
        super().__init__(
            "The profile's patterns found no route in this repo.",
            next_action=(
                "Give Discovery a route pattern that matches how this repo registers routes, "
                "or fix the one in the profile, then run again."
            ),
            exit_code=ExitCode.DISCOVERY_NEEDS_INPUT,
        )


def build_inventory(reading: SourceReading) -> Inventory:
    routes = _sorted_unique(
        (_route_from(match) for match in _of_kind(reading.matches, "route")),
        key=lambda route: (route.path, route.method, route.handler),
    )
    if not routes:
        raise NoRoutesError
    params, params_dropped = _read_all(_of_kind(reading.matches, "param"), read=_param)
    error_codes, errors_dropped = _read_all(_of_kind(reading.matches, "error"), read=_error_code)
    env_vars, env_dropped = _read_all(_of_kind(reading.matches, "env"), read=_env_var)
    fields, fields_dropped = _read_all(_listed_fields(reading.fields), read=_response_field)
    targets, targets_dropped = _read_all(map(_MakeTarget, reading.make_targets), read=_target_name)
    return Inventory(
        routes=routes,
        params=_sorted_unique(params, key=lambda param: param.name),
        error_codes=_sorted_unique(error_codes, key=lambda error: (error.code, error.status)),
        response_fields=_sorted_unique(fields, key=_whole_model),
        env_vars=_sorted_unique(env_vars, key=str),
        make_targets=_sorted_unique(targets, key=str),
        dropped=_dropped_lines(
            [*params_dropped, *errors_dropped, *env_dropped, *fields_dropped, *targets_dropped]
        ),
    )


# Every field takes part, so nothing that differs merges and ties never fall back on set order.
def _whole_model(model: Frozen) -> tuple[object, ...]:
    return tuple(model.model_dump().values())


def _sorted_unique[T](items: Iterable[T], *, key: Callable[[T], Any]) -> tuple[T, ...]:
    return tuple(sorted(set(items), key=key))


def _of_kind(matches: Iterable[CallMatch], kind: str) -> list[CallMatch]:
    return [match for match in matches if match.kind == kind]


def _route_from(match: CallMatch) -> Route:
    pattern = match.literals.get("path")
    if pattern is None:
        raise RouteNotUnderstoodError(match, _no_literal(match), next_action=USE_A_LITERAL)
    method, path = _split_pattern(pattern, callee=match.call)
    if not path.startswith("/"):
        raise RouteNotUnderstoodError(
            match,
            f"the path `{one_line(path, _SHOWN_LENGTH)}` must start with /, after any method",
            next_action=USE_A_PATH_PATTERN,
        )
    return _route(match, method=method, path=path.removesuffix("{$}"))


def _no_literal(match: CallMatch) -> str:
    shown = match.args.get("path")
    if shown is None:
        return "its path argument is missing"
    return f"its path argument `{one_line(shown, _SHOWN_LENGTH)}` is not a string literal"


# "from None", because the validation error holds the refused text, which may be megabytes.
def _route(match: CallMatch, *, method: str, path: str) -> Route:
    try:
        return Route(method=method, path=path, handler=_handler_name(match))
    except ValidationError as error:
        problem = describe_problems(error, whole="the route")
        raise RouteNotUnderstoodError(match, problem, next_action=USE_A_READABLE_ROUTE) from None


# The method is the start of the pattern, as in "GET /items", or else the last part of the call,
# as in r.GET("/items"). A call that names no verb, such as mux.HandleFunc, accepts any method.
def _split_pattern(pattern: str, *, callee: str) -> tuple[str, str]:
    with_method = _METHOD_THEN_PATH.fullmatch(pattern)
    if with_method:
        return with_method.group(1), with_method.group(2)
    return _verb_ending(callee), pattern


def _verb_ending(callee: str) -> str:
    last_part = callee.rsplit(".", 1)[-1].upper()
    return last_part if last_part in _HTTP_VERBS else "ANY"


def _handler_name(match: CallMatch) -> str:
    handler = match.args.get("handler", "")
    return handler if _PLAIN_NAME.fullmatch(handler) else INLINE_HANDLER


class _Unreadable(Exception):
    """Something that was found and gives nothing. The message says which part failed."""


class _Located(Protocol):
    @property
    def file(self) -> str: ...

    @property
    def line(self) -> int: ...


# The Makefile's rules are read by a regex that keeps no line.
class _MakeTarget(NamedTuple):
    name: str
    file: str = "Makefile"
    line: int = 0


class _Dropped(NamedTuple):
    file: str
    line: int
    why: str


def _read_all[S: _Located, T](
    sources: Iterable[S], *, read: Callable[[S], T]
) -> tuple[list[T], list[_Dropped]]:
    items: list[T] = []
    dropped: list[_Dropped] = []
    for source in sources:
        try:
            items.append(read(source))
        except (_Unreadable, ValidationError) as problem:
            dropped.append(_Dropped(source.file, source.line, _why_unreadable(problem)))
    return items, dropped


def _why_unreadable(problem: Exception) -> str:
    if isinstance(problem, ValidationError):
        return f"unreadable {describe_problems(problem, whole='text')}"
    return str(problem)


def _dropped_lines(dropped: list[_Dropped]) -> tuple[str, ...]:
    return tuple(
        f"{printable_name(one.file)}:{one.line}: {one.why[:_DROPPED_REASON_LENGTH]}"
        for one in sorted(set(dropped))
    )


def _param(match: CallMatch) -> Param:
    return Param(name=_literal(match, "name"))


def _env_var(match: CallMatch) -> str:
    return _NAME_TEXT.validate_python(_literal(match, "name"))


def _target_name(target: _MakeTarget) -> str:
    return _NAME_TEXT.validate_python(target.name)


# The status is kept as written, on one line: a constant, a number or a call all say something to
# a reader, and the start of a function written in place says enough.
def _error_code(match: CallMatch) -> ErrorCode:
    status = match.args.get("status")
    code = _literal(match, "code")
    if status is None:
        raise _Unreadable(f"{match.kind} argument `status` is missing")
    return ErrorCode(code=code, status=one_line(status, STATUS_LIMIT))


def _literal(match: CallMatch, name: str) -> str:
    value = match.literals.get(name)
    if value:
        return value
    if value == "":
        raise _Unreadable(f"{match.kind} argument `{name}` is an empty string")
    shown = match.args.get(name)
    if shown is None:
        raise _Unreadable(f"{match.kind} argument `{name}` is missing")
    raise _Unreadable(
        f"{match.kind} argument `{name}` is `{one_line(shown, _DROPPED_TEXT_LENGTH)}`, "
        "not a string literal"
    )


def _listed_fields(fields: Iterable[FieldReply]) -> list[FieldReply]:
    in_service = [field for field in fields if not field.file.endswith("_test.go")]
    response_structs = {
        (field.file, field.struct) for field in in_service if _may_hold_a_json_key(field.tag)
    }
    return [
        field
        for field in in_service
        if (field.file, field.struct) in response_structs and _is_listed(field)
    ]


# A tag too long to print may hold a json key, and passing over it would lose the struct silently.
def _may_hold_a_json_key(tag: str) -> bool:
    return tag == TOO_LONG_TO_PRINT or _json_tag(tag) is not None


# An embedded field is left out: its own fields are promoted into the parent's JSON, and the
# inventory has no way to say so. docs/limitations.md lists this.
def _is_listed(field: FieldReply) -> bool:
    return field.name[:1].isupper() and not field.embedded and _json_tag(field.tag) != "-"


def _response_field(field: FieldReply) -> ResponseField:
    if TOO_LONG_TO_PRINT in (field.tag, field.go_type):
        raise _Unreadable(f"field {field.struct}.{field.name} has a tag or type too long to read")
    name, *options = (_json_tag(field.tag) or "").split(",")
    return ResponseField(
        file=field.file,
        struct=field.struct,
        name=field.name,
        json_name=name or field.name,
        declared_type=field.go_type,
        # omitzero also drops the key, so the field may be absent from the reply.
        omitempty="omitempty" in options or "omitzero" in options,
    )


# Reads the value of the json key the way Go's reflect does: entry by entry, so text that looks
# like a json key inside another key's quoted value is not mistaken for one.
def _json_tag(tag: str) -> str | None:
    position = 0
    while entry := _TAG_ENTRY.match(tag, position):
        if entry.group(1) == "json":
            return entry.group(2)
        position = entry.end()
    return None

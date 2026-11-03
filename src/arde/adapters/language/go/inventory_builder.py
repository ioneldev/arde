"""Turns what goinspect reports into the inventory. Pure: it reads no file and runs nothing."""

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any, NamedTuple, Protocol

from pydantic import TypeAdapter, ValidationError

from arde.adapters.language.go.goinspect_client import (
    TOO_LONG_TO_PRINT,
    CallMatch,
    DeclarationReply,
    FieldReply,
    RouteSite,
    RoutesReply,
    UnmatchedPattern,
)
from arde.application.ports import ProfileProblem
from arde.domain.base import Frozen, describe_problems, one_line, printable_name
from arde.domain.inventory import (
    INLINE_HANDLER,
    PATH_LIMIT,
    PATTERN_LIMIT,
    STATUS_LIMIT,
    DeclarationText,
    ErrorCode,
    Inventory,
    NameText,
    Param,
    ResponseField,
    Route,
)

_HTTP_VERBS = frozenset(
    {"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "CONNECT", "OPTIONS", "TRACE"}
)
_METHOD_THEN_PATH = re.compile(r"([A-Z]+)[ \t]+(.*)", re.DOTALL)
_PLAIN_NAME = re.compile(r"[^\W\d]\w*(?:\.[^\W\d]\w*)*")
# Where the text of an argument is cut when it goes into a message, and into a dropped line.
_SHOWN_LENGTH = 80
_DROPPED_TEXT_LENGTH = 40
# How much of the reason a dropped line keeps. Several fields of one model can fail together.
_DROPPED_REASON_LENGTH = 250
_ROUTE_NOT_IN_THIS_BUILD = "route in a file this build leaves out"
# The argument each kind is read for, which tells a wrapper's body from a call worth reporting.
_ARGUMENT_READ = {"param": "name", "env": "name", "error": "code"}
_NAME_TEXT: TypeAdapter[str] = TypeAdapter(NameText)
_DECLARATION_TEXT: TypeAdapter[str] = TypeAdapter(DeclarationText)


class SourceReading(Frozen):
    main_package: str
    service: RoutesReply
    matches: tuple[CallMatch, ...]
    unmatched_patterns: tuple[UnmatchedPattern, ...]
    declarations: tuple[DeclarationReply, ...]
    fields: tuple[FieldReply, ...]
    make_targets: tuple[str, ...]


# None of these may send anyone to a narrower pattern: a route left out is worse than a stopped run,
# and the model reading this text is working to make the check pass.
DO_NOT_NARROW = "Do not narrow or remove a pattern to get past this."
READ_A_CONSTANT_PATH = (
    "ARDE reads a path the compiler knows. If this call is inside a function that registers routes "
    "for its callers, add a route pattern for the calls to that function. "
    f"{DO_NOT_NARROW} If the path is computed or comes from a table, this repo cannot be "
    "documented yet: report that."
)
USE_A_PATH_PATTERN = (
    "ARDE reads route patterns of the form [METHOD ]/path, with no host. Register the route "
    f"that way, then run again. {DO_NOT_NARROW}"
)
PREFIXES_ARE_NOT_RESOLVED = (
    f"ARDE does not resolve path prefixes, so this repo cannot be documented yet: report that. "
    f"{DO_NOT_NARROW}"
)
GIVE_THE_HANDLER_POSITION = (
    "The route pattern gives a handler position past the end of this call. Correct that position "
    "in the profile, then run again."
)
USE_A_READABLE_ROUTE = (
    f"ARDE reads a route as one line of plain text, with a path of at most {PATH_LIMIT} "
    "characters. Register the route that way, then run again."
)


class _Located(Protocol):
    @property
    def file(self) -> str: ...

    @property
    def line(self) -> int: ...


class RouteNotUnderstoodError(ProfileProblem):
    def __init__(self, place: _Located, problem: str, *, next_action: str) -> None:
        super().__init__(
            f"Cannot read the route registered at {printable_name(place.file)}:{place.line}: "
            f"{problem}.",
            next_action=next_action,
        )


class NoRoutesError(ProfileProblem):
    def __init__(self) -> None:
        super().__init__(
            "The profile's patterns found no route in this repo.",
            next_action=(
                "Give Discovery a route pattern that matches how this repo registers routes, "
                "or fix the one in the profile, then run again."
            ),
        )


def build_inventory(reading: SourceReading) -> Inventory:
    _stop_where_a_path_changes(reading.service)
    routes = _one_row_per_route(_route_from(site) for site in reading.service.sites)
    if not routes:
        raise NoRoutesError
    scope = _Scope.of(reading)
    matches, matches_outside = scope.matches_of_the_service(reading.matches)
    response_fields, fields_outside = scope.split(_listed_fields(reading.fields), "response field")
    declarations, _ = scope.split(reading.declarations, "declaration")
    params, params_dropped = _read_all(_of_kind(matches, "param"), read=_param)
    error_codes, errors_dropped = _read_all(_of_kind(matches, "error"), read=_error_code)
    env_vars, env_dropped = _read_all(_of_kind(matches, "env"), read=_env_var)
    fields, fields_dropped = _read_all(response_fields, read=_response_field)
    targets, targets_dropped = _read_all(map(_MakeTarget, reading.make_targets), read=_target_name)
    declaration_ids, ids_dropped = _read_all(declarations, read=_declaration_id)
    return Inventory(
        routes=routes,
        params=_sorted_unique(params, key=lambda param: param.name),
        error_codes=_sorted_unique(error_codes, key=lambda error: (error.code, error.status)),
        response_fields=_sorted_unique(fields, key=_whole_model),
        env_vars=_sorted_unique(env_vars, key=str),
        make_targets=_sorted_unique(targets, key=str),
        declarations=_sorted_unique(declaration_ids, key=str),
        unmatched_patterns=_sorted_unique(
            (one_line(f"{p.kind} {p.call}", PATTERN_LIMIT) for p in reading.unmatched_patterns),
            key=str,
        ),
        dropped=_dropped_lines(
            [
                *matches_outside,
                *fields_outside,
                *params_dropped,
                *errors_dropped,
                *env_dropped,
                *fields_dropped,
                *targets_dropped,
                *ids_dropped,
            ]
        ),
    )


def _stop_where_a_path_changes(service: RoutesReply) -> None:
    if service.stripped_prefixes:
        raise RouteNotUnderstoodError(
            service.stripped_prefixes[0],
            "http.StripPrefix changes the paths under it, so they cannot be read",
            next_action=PREFIXES_ARE_NOT_RESOLVED,
        )
    if service.wrapper_prefixes:
        raise RouteNotUnderstoodError(
            service.wrapper_prefixes[0],
            "the function around this call adds to the path, so its callers' paths cannot be read",
            next_action=PREFIXES_ARE_NOT_RESOLVED,
        )


@dataclass(frozen=True)
class _Scope:
    """What belongs to the service, by folder. main_package's imports decide, not its path."""

    main_package: str
    folders: frozenset[str]
    build_files: frozenset[str]
    wrapper_calls: frozenset[tuple[str, str, int]]

    @classmethod
    def of(cls, reading: SourceReading) -> "_Scope":
        return cls(
            main_package=reading.main_package,
            folders=frozenset(reading.service.package_folders),
            build_files=frozenset(reading.service.build_files),
            wrapper_calls=frozenset(
                (call.kind, call.file, call.line) for call in reading.service.wrapper_calls
            ),
        )

    def holds(self, source: _Located) -> bool:
        return PurePosixPath(source.file).parent.as_posix() in self.folders

    def outside(self, what: str, source: _Located) -> "_Dropped":
        why = f"{what} outside main_package {self.main_package}"
        return _Dropped(source.file, source.line, why)

    def split[S: _Located](
        self, sources: Iterable[S], what: str
    ) -> tuple[list[S], list["_Dropped"]]:
        inside: list[S] = []
        dropped: list[_Dropped] = []
        for source in sources:
            if self.holds(source):
                inside.append(source)
            else:
                dropped.append(self.outside(what, source))
        return inside, dropped

    # A route of the service comes from the sites, which say more about it than a match can. A
    # route match adds only what the sites cannot show, because the type checker never read it.
    def matches_of_the_service(
        self, matches: Iterable[CallMatch]
    ) -> tuple[list[CallMatch], list["_Dropped"]]:
        kept: list[CallMatch] = []
        dropped: list[_Dropped] = []
        for match in matches:
            if not self.holds(match):
                dropped.append(self.outside(match.kind, match))
            elif match.kind == "route":
                if match.file not in self.build_files:
                    dropped.append(_Dropped(match.file, match.line, _ROUTE_NOT_IN_THIS_BUILD))
            elif not self._hands_on_a_value(match):
                kept.append(match)
        return kept, dropped

    # The pattern on the wrapper's calls reads that value, so this call has lost nothing.
    def _hands_on_a_value(self, match: CallMatch) -> bool:
        is_inside = (match.kind, match.file, match.line) in self.wrapper_calls
        return is_inside and _ARGUMENT_READ[match.kind] not in match.literals


# Every field takes part, so nothing that differs merges and ties never fall back on set order.
def _whole_model(model: Frozen) -> tuple[object, ...]:
    return tuple(model.model_dump().values())


def _sorted_unique[T](items: Iterable[T], *, key: Callable[[T], Any]) -> tuple[T, ...]:
    return tuple(sorted(set(items), key=key))


def _of_kind(matches: Iterable[CallMatch], kind: str) -> list[CallMatch]:
    return [match for match in matches if match.kind == kind]


# One function registered twice under one key is one route, at its first place. Two functions under
# one key stay two rows, so a mounted router's routes do not merge away.
def _one_row_per_route(routes: Iterable[Route]) -> tuple[Route, ...]:
    rows: dict[tuple[str, str, str], Route] = {}
    for route in sorted(routes, key=_route_order):
        rows.setdefault((route.method, route.path, route.handler_id or route.handler), route)
    return tuple(rows.values())


def _route_order(route: Route) -> tuple[str, str, str, str, str, int]:
    return (
        route.path,
        route.method,
        route.handler,
        route.handler_id or "",
        route.file,
        route.line,
    )


def _route_from(site: RouteSite) -> Route:
    pattern = site.literals.get("path")
    if pattern is None:
        raise RouteNotUnderstoodError(site, _unknown_path(site), next_action=READ_A_CONSTANT_PATH)
    method, path = _split_pattern(pattern, callee=site.callee)
    if not path.startswith("/"):
        raise RouteNotUnderstoodError(
            site,
            f"the path `{one_line(path, _SHOWN_LENGTH)}` must start with /, after any method",
            next_action=USE_A_PATH_PATTERN,
        )
    return _route(site, method=method, path=path.removesuffix("{$}"))


def _unknown_path(site: RouteSite) -> str:
    shown = site.args.get("path")
    if shown is None:
        return "its path argument is missing"
    return f"its path argument `{one_line(shown, _SHOWN_LENGTH)}` is not a constant"


# "from None", because the validation error holds the refused text, which may be megabytes.
def _route(site: RouteSite, *, method: str, path: str) -> Route:
    try:
        return Route(
            method=method,
            path=path,
            handler=_handler_name(site),
            file=site.file,
            line=site.line,
            handler_id=site.handler_id,
            handler_file=site.handler_file,
            handler_line=site.handler_line,
        )
    except ValidationError as error:
        problem = describe_problems(error, whole="the route")
        raise RouteNotUnderstoodError(site, problem, next_action=USE_A_READABLE_ROUTE) from None


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


def _handler_name(site: RouteSite) -> str:
    handler = site.args.get("handler")
    if handler is None:
        raise RouteNotUnderstoodError(
            site, "its handler argument is missing", next_action=GIVE_THE_HANDLER_POSITION
        )
    return handler if _PLAIN_NAME.fullmatch(handler) else INLINE_HANDLER


class _Unreadable(Exception):
    """Something that was found and gives nothing. The message says which part failed."""


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


def _declaration_id(declaration: DeclarationReply) -> str:
    return _DECLARATION_TEXT.validate_python(declaration.id)


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
        (field.file, field.struct) for field in in_service if _may_hold_a_json_key(field)
    }
    return [
        field
        for field in in_service
        if (field.file, field.struct) in response_structs and _is_listed(field)
    ]


# A tag too long to print may hold a json key, and passing over it would lose the struct silently.
def _may_hold_a_json_key(field: FieldReply) -> bool:
    return field.tag == TOO_LONG_TO_PRINT or field.json_tag is not None


# An embedded field is left out: its own fields are promoted into the parent's JSON, and the
# inventory has no way to say so. docs/limitations.md lists this.
def _is_listed(field: FieldReply) -> bool:
    return field.name[:1].isupper() and not field.embedded and field.json_tag != "-"


def _response_field(field: FieldReply) -> ResponseField:
    if TOO_LONG_TO_PRINT in (field.tag, field.go_type):
        raise _Unreadable(f"field {field.struct}.{field.name} has a tag or type too long to read")
    name, *options = (field.json_tag or "").split(",")
    return ResponseField(
        file=field.file,
        package=field.package,
        struct=field.struct,
        name=field.name,
        json_name=name or field.name,
        declared_type=field.go_type,
        # omitzero also drops the key, so the field may be absent from the reply.
        may_be_absent="omitempty" in options or "omitzero" in options,
    )

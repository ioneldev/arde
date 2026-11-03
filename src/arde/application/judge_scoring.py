"""How code scores the blind judge, with no model in the loop.

A prediction is compared with the reply exactly: the status, and each field the judge predicted,
at its dotted path. The judge's arithmetic is done here too, on numbers alone. And code works out
from the inventory and the wiki which items the judge must try before it may finish: on a first
run every route, param and reachable error code, and on a later run only those the changed facts
link. The judge is told the names of those items, which the API reference shows anyway, and never
sees the inventory or the wiki.
"""

import ast
import json
import math
import operator
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from functools import partial
from typing import Annotated, Literal, TypeGuard
from urllib.parse import parse_qsl

from pydantic import AfterValidator, Field

from arde.application.fact_names import ANY_METHOD, fact_routes, route_is_writable
from arde.application.fact_validation import ERROR_STATUS_FLOOR, holds_code
from arde.application.ports import HttpRequest, PathAndQuery, RequestMethod
from arde.application.reply_examples import pattern_matches
from arde.domain.base import Frozen
from arde.domain.facts import EndpointFact, Fact, ProbeRecord, UnreachableFact, Wiki
from arde.domain.inventory import Inventory, Route

# A finite float, because the trace is JSON, which holds no infinity, and a model can write 1e400.
FieldValue = str | int | Annotated[float, Field(allow_inf_nan=False)] | bool | None
# Long enough for any total a reply holds, and short enough that a parse stays cheap.
EXPRESSION_LIMIT = 500
_SHOWN_VALUE_LIMIT = 200
_SENDABLE_METHODS = ("GET", "HEAD")
_NOT_SENT = "the judge sends GET and HEAD alone"
_NO_HEADERS = "the judge's requests carry no headers"


def _require_dotted_path(path: str) -> str:
    if "" in path.split("."):
        raise ValueError(
            "must be a dotted path from the body's root, of keys and array indexes, such as "
            "items.0.price"
        )
    return path


FieldPath = Annotated[str, AfterValidator(_require_dotted_path)]


class Expectation(Frozen):
    """What the judge expects a reply to hold: its status, and the values of some of its fields."""

    status: Annotated[int, Field(ge=100, le=599)] | Literal["unknown"] = Field(
        description='The status the docs lead you to expect, or "unknown" when they do not say.'
    )
    fields: dict[FieldPath, FieldValue] = Field(
        description=(
            "Values you expect in the JSON body, each at its dotted path from the body's root, "
            "such as items.0.price for the price of the first item. Each value is a string, a "
            "number, true, false or null, written exactly as you expect it. Empty when the docs "
            "let you predict no value."
        )
    )


class Prediction(Expectation):
    """What the judge expects one request to get, from the docs alone, before it is sent."""

    method: RequestMethod
    path_and_query: PathAndQuery = Field(
        description="The path and query alone, such as /items?page=2, percent-encoded."
    )

    @property
    def request(self) -> HttpRequest:
        return HttpRequest(method=self.method, path_and_query=self.path_and_query)


class Mismatch(Frozen):
    prediction: Prediction
    actual: ProbeRecord
    # Each part the prediction got wrong, joined by "; ".
    detail: str


# A prediction against the reply


def compare_prediction(prediction: Prediction, actual: ProbeRecord) -> Mismatch | None:
    """What the prediction got wrong about the reply, or None when it got nothing wrong.

    The status is compared when the judge gave one. Each predicted field must be at its path with
    an equal value of the same JSON type, and a number is equal only to the same number, so 20.0
    is not 19.996. Fields the judge did not predict are not compared.
    """
    problems = [*_status_problems(prediction, actual), *_field_problems(prediction, actual)]
    if not problems:
        return None
    return Mismatch(prediction=prediction, actual=actual, detail="; ".join(problems))


def _status_problems(prediction: Prediction, actual: ProbeRecord) -> list[str]:
    if prediction.status == "unknown" or prediction.status == actual.status:
        return []
    return [f"status: predicted {prediction.status}, the service answered {actual.status}"]


class _Absent:
    """What a path holds when the body has nothing there."""


_ABSENT = _Absent()


def _field_problems(prediction: Prediction, actual: ProbeRecord) -> list[str]:
    if not prediction.fields:
        return []
    body = _parsed(actual.body)
    if body is _ABSENT:
        return [
            f"{path}: predicted {_shown(value)}, and the reply body is not JSON"
            for path, value in prediction.fields.items()
        ]
    problems = []
    for path, predicted in prediction.fields.items():
        held = _at_path(body, path)
        if held is _ABSENT:
            problems.append(f"{path}: predicted {_shown(predicted)}, the reply holds no such field")
        elif not _same_value(predicted, held):
            problems.append(
                f"{path}: predicted {_shown(predicted)}, the reply holds {_shown(held)}"
            )
    return problems


def _parsed(body: str) -> object:
    try:
        return json.loads(body, parse_constant=_refuse_constant)
    except (ValueError, RecursionError):
        return _ABSENT


# NaN and Infinity are no JSON, though Python's parser takes them.
def _refuse_constant(name: str) -> object:
    raise ValueError(f"{name} is not JSON")


def _at_path(document: object, path: str) -> object:
    value = document
    for key in path.split("."):
        if isinstance(value, dict) and key in value:
            value = value[key]
        elif isinstance(value, list) and key.isascii() and key.isdigit() and int(key) < len(value):
            value = value[int(key)]
        else:
            return _ABSENT
    return value


# Python counts True as 1 and 1 as 1.0, and JSON tells true from 1 but not 1 from 1.0.
def _same_value(predicted: FieldValue, held: object) -> bool:
    if isinstance(predicted, bool) or isinstance(held, bool):
        return isinstance(predicted, bool) and isinstance(held, bool) and predicted == held
    if _is_number(predicted) and _is_number(held):
        return predicted == held
    return type(predicted) is type(held) and predicted == held


def _is_number(value: object) -> TypeGuard[int | float]:
    return isinstance(value, int | float) and not isinstance(value, bool)


def _shown(value: object) -> str:
    text = json.dumps(value, ensure_ascii=False)
    return text if len(text) <= _SHOWN_VALUE_LIMIT else text[:_SHOWN_VALUE_LIMIT] + " [cut]"


# Arithmetic


class NotArithmetic(ValueError):
    """The expression is not plain arithmetic on numbers, or it has no finite result."""


_OPERATORS: dict[type[ast.operator], Callable[[float, float], float]] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
}
_SIGNS: dict[type[ast.unaryop], Callable[[float], float]] = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}
_ALLOWED = "numbers, + - * /, parentheses, round(x) and round(x, digits)"


def safe_calculate(expression: str) -> float:
    """The value of plain arithmetic: numbers, + - * /, parentheses and round, in floating point.

    Every number is read as a float, as a service that computes in floating point reads it, which
    also keeps a huge integer from costing memory or time. Anything else is refused.
    """
    if len(expression) > EXPRESSION_LIMIT:
        raise NotArithmetic(
            f"the expression has {len(expression)} characters, and at most {EXPRESSION_LIMIT} are "
            "read: split it into parts"
        )
    try:
        tree = ast.parse(expression, mode="eval")
    except (SyntaxError, ValueError) as error:
        raise NotArithmetic(f"it is not an expression: {error}") from error
    try:
        value = _value(tree.body)
    except ZeroDivisionError as error:
        raise NotArithmetic("it divides by zero") from error
    except OverflowError as error:
        raise NotArithmetic("a number in it is too large") from error
    if not math.isfinite(value):
        raise NotArithmetic("its result is not a finite number")
    return value


def _value(node: ast.expr) -> float:
    if isinstance(node, ast.Constant) and _is_number(node.value):
        return float(node.value)
    if isinstance(node, ast.BinOp) and type(node.op) in _OPERATORS:
        return _OPERATORS[type(node.op)](_value(node.left), _value(node.right))
    if isinstance(node, ast.UnaryOp) and type(node.op) in _SIGNS:
        return _SIGNS[type(node.op)](_value(node.operand))
    if isinstance(node, ast.Call) and _is_round(node):
        return _rounded(node)
    raise NotArithmetic(f"it holds {ast.unparse(node)[:60]}, and only {_ALLOWED} are allowed")


def _is_round(call: ast.Call) -> bool:
    return (
        isinstance(call.func, ast.Name)
        and call.func.id == "round"
        and 1 <= len(call.args) <= 2
        and not call.keywords
    )


# round ties to the even digit, as IEEE arithmetic does by default.
def _rounded(call: ast.Call) -> float:
    number = _value(call.args[0])
    if not math.isfinite(number):
        raise NotArithmetic("it rounds a number that is not finite")
    if len(call.args) == 1:
        return float(round(number))
    digits = _value(call.args[1])
    if not digits.is_integer():
        raise NotArithmetic("round(x, digits) needs a whole number of digits")
    return round(number, int(digits))


# The items a run requires


class RequiredParam(Frozen):
    # The route of the endpoint fact that describes the param, so its method is a concrete one.
    route: str
    name: str
    location: Literal["path", "query", "form"]

    @property
    def label(self) -> str:
        return f"param {self.name} ({self.location}) of {self.route}"


class RequiredItems(Frozen):
    """What the judge must try before it may finish, and what it is excused from."""

    # As the inventory writes them, so an ANY route is tried by a GET or a HEAD.
    routes: tuple[str, ...] = ()
    params: tuple[RequiredParam, ...] = ()
    error_codes: tuple[str, ...] = ()
    # Each to be sent again as written: the requests the last attempt's judge predicted wrong.
    requests: tuple[HttpRequest, ...] = ()
    # One line for each item in scope that the judge is excused from, saying why.
    exemptions: tuple[str, ...] = ()

    def is_empty(self) -> bool:
        return not (self.routes or self.params or self.error_codes or self.requests)

    def labels(self) -> list[str]:
        return [item.label for item in _items(self)]


class JudgeScope(Frozen):
    inventory: Inventory
    wiki: Wiki
    # The ids of the facts that changed or came since the last run. None on a first run, when
    # every item is required.
    changed_ids: frozenset[str] | None
    # The requests the last attempt's judge predicted wrong, which this attempt sends again.
    again: tuple[HttpRequest, ...] = ()


def required_items(scope: JudgeScope) -> RequiredItems:
    """What the judge must try: every item on a first run, and on a later one only the items the
    changed facts link, with each request the last attempt's judge predicted wrong.

    A route the judge cannot send, a header param and an error code no request it can send
    reaches are excused, each with a line saying why.
    """
    in_scope = _in_scope(scope)
    routes, route_exemptions = _routes(in_scope.routes)
    params, param_exemptions = _params(in_scope.endpoints)
    codes, code_exemptions = _error_codes(in_scope.error_codes, wiki=scope.wiki)
    return RequiredItems(
        routes=routes,
        params=params,
        error_codes=codes,
        requests=tuple(dict.fromkeys(scope.again)),
        exemptions=tuple(sorted({*route_exemptions, *param_exemptions, *code_exemptions})),
    )


@dataclass(frozen=True)
class _InScope:
    routes: tuple[Route, ...]
    endpoints: tuple[EndpointFact, ...]
    error_codes: tuple[str, ...]


# A route no fact can name gets no docs, and the run warns of it already.
def _in_scope(scope: JudgeScope) -> _InScope:
    writable = tuple(route for route in scope.inventory.routes if route_is_writable(route))
    if scope.changed_ids is None:
        return _InScope(
            routes=writable,
            endpoints=_endpoints(scope.wiki.facts),
            error_codes=tuple(error.code for error in scope.inventory.error_codes),
        )
    changed = tuple(fact for fact in scope.wiki.facts if fact.id in scope.changed_ids)
    endpoints = _endpoints(changed)
    linked = {key for fact in changed for key in fact.links.routes}
    described = {fact.route for fact in endpoints}
    return _InScope(
        routes=tuple(
            route
            for route in writable
            if route.key in linked or not described.isdisjoint(fact_routes(route))
        ),
        endpoints=endpoints,
        error_codes=(
            *_codes_listed(endpoints),
            *(fact.error_code for fact in changed if isinstance(fact, UnreachableFact)),
        ),
    )


def _endpoints(facts: Sequence[Fact]) -> tuple[EndpointFact, ...]:
    return tuple(fact for fact in facts if isinstance(fact, EndpointFact))


def _codes_listed(endpoints: Sequence[EndpointFact]) -> list[str]:
    return [
        code for fact in endpoints for response in fact.responses for code in response.error_codes
    ]


def _is_sendable(method: str) -> bool:
    return method in _SENDABLE_METHODS


def _routes(routes: Sequence[Route]) -> tuple[tuple[str, ...], list[str]]:
    required = {
        route.key for route in routes if _is_sendable(route.method) or route.method == ANY_METHOD
    }
    exempt = [f"route {route.key}: {_NOT_SENT}" for route in routes if route.key not in required]
    return tuple(sorted(required)), exempt


def _params(endpoints: Sequence[EndpointFact]) -> tuple[tuple[RequiredParam, ...], list[str]]:
    required: set[RequiredParam] = set()
    exempt: list[str] = []
    for fact in endpoints:
        method = fact.route.split(" ", 1)[0]
        for param in fact.params:
            label = f"param {param.name} ({param.location}) of {fact.route}"
            if not _is_sendable(method):
                exempt.append(f"{label}: {_NOT_SENT}")
            elif param.location == "header":
                exempt.append(f"{label}: {_NO_HEADERS}")
            else:
                required.add(
                    RequiredParam(route=fact.route, name=param.name, location=param.location)
                )
    return tuple(sorted(required, key=lambda p: (p.route, p.name, p.location))), exempt


def _error_codes(codes: Sequence[str], *, wiki: Wiki) -> tuple[tuple[str, ...], list[str]]:
    unreachable = {
        fact.error_code: fact for fact in wiki.facts if isinstance(fact, UnreachableFact)
    }
    endpoints = _endpoints(wiki.facts)
    required: set[str] = set()
    exempt: list[str] = []
    for code in dict.fromkeys(codes):
        returning = sorted({fact.route for fact in endpoints if code in _codes_listed([fact])})
        if code in unreachable:
            fact = unreachable[code]
            exempt.append(
                f"error code {code}: no request reaches it, as fact {fact.id} says: {fact.reason}"
            )
        elif returning and not any(_is_sendable(route.split(" ", 1)[0]) for route in returning):
            verb = "returns" if len(returning) == 1 else "return"
            exempt.append(
                f"error code {code}: only {' and '.join(returning)} {verb} it, and {_NOT_SENT}"
            )
        else:
            required.add(code)
    return tuple(sorted(required)), exempt


# What a reply tried


@dataclass(frozen=True)
class _Item:
    label: str
    is_tried_by: Callable[[ProbeRecord], bool]


def untried(required: RequiredItems, replies: Sequence[ProbeRecord]) -> list[str]:
    """Each required item that no reply tried, routes first, then params, error codes and
    requests."""
    return [
        item.label
        for item in _items(required)
        if not any(item.is_tried_by(reply) for reply in replies)
    ]


def tries_required(required: RequiredItems, reply: ProbeRecord) -> bool:
    """True when the reply tried any of the required items."""
    return any(item.is_tried_by(reply) for item in _items(required))


def _items(required: RequiredItems) -> list[_Item]:
    return [
        *(_Item(f"route {key}", partial(_tries_route, key)) for key in required.routes),
        *(_Item(param.label, partial(_tries_param, param)) for param in required.params),
        *(_Item(f"error code {code}", partial(_carries, code)) for code in required.error_codes),
        *(
            _Item(f"the request {request.method} {request.path_and_query}", partial(_is, request))
            for request in required.requests
        ),
    ]


def _tries_route(route: str, reply: ProbeRecord) -> bool:
    method, path = route.split(" ", 1)
    return method in (ANY_METHOD, reply.method) and pattern_matches(path, reply.path_and_query)


def _tries_param(param: RequiredParam, reply: ProbeRecord) -> bool:
    if not _tries_route(param.route, reply):
        return False
    return param.location == "path" or param.name in _query_names(reply.path_and_query)


def _query_names(path_and_query: str) -> set[str]:
    query = path_and_query.partition("?")[2]
    return {name for name, _ in parse_qsl(query, keep_blank_values=True)}


def _carries(code: str, reply: ProbeRecord) -> bool:
    return reply.status >= ERROR_STATUS_FLOOR and holds_code(reply.body, code)


def _is(request: HttpRequest, reply: ProbeRecord) -> bool:
    return (reply.method, reply.path_and_query) == (request.method, request.path_and_query)

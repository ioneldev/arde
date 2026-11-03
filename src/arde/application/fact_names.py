"""The names of the inventory that a fact can hold.

A fact's own types are narrower than the inventory's: a route must read as a method and a path
with no space, and a struct and a field must be plain identifiers. A name that fails them can be
in no fact, so the schema does not offer it and coverage does not ask for it, and the run says so
rather than leave it out in silence.
"""

import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from pydantic import TypeAdapter, ValidationError

from arde.domain.base import CodeIdentifier, ParamName, RoutePattern
from arde.domain.inventory import Inventory, ResponseField, Route

# A route registered with no method answers every one, and the inventory writes it as ANY. A fact
# never holds ANY: the agent probes the path and writes a fact for each method it serves.
ANY_METHOD = "ANY"
CONCRETE_METHODS = ("GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS")

# A part of a path in braces names a path param, as in /items/{id}. A trailing ... matches the
# rest of the path, and {$} only marks its end.
_WILDCARD = re.compile(r"\{([^{}/]+?)(?:\.\.\.)?\}")
_END_OF_PATH = "$"


def fact_routes(route: Route) -> tuple[str, ...]:
    """The routes a fact may name for this route of the inventory."""
    if route.method == ANY_METHOD:
        return tuple(f"{method} {route.path}" for method in CONCRETE_METHODS)
    return (route.key,)


def path_wildcards(path: str) -> tuple[str, ...]:
    return tuple(name for name in _WILDCARD.findall(path) if name != _END_OF_PATH)


def can_hold(type_: TypeAdapter[Any], name: str) -> bool:
    try:
        type_.validate_python(name)
    except ValidationError:
        return False
    return True


ROUTE_TEXT: TypeAdapter[Any] = TypeAdapter(RoutePattern)
PARAM_TEXT: TypeAdapter[Any] = TypeAdapter(ParamName)
IDENTIFIER: TypeAdapter[Any] = TypeAdapter(CodeIdentifier)


def route_is_writable(route: Route) -> bool:
    return all(can_hold(ROUTE_TEXT, name) for name in fact_routes(route))


def field_is_writable(field: ResponseField) -> bool:
    return can_hold(IDENTIFIER, field.struct) and can_hold(IDENTIFIER, field.name)


# A declaration is written as file::kind name.
def build_files(inventory: Inventory) -> frozenset[str]:
    """The files the service is built from, as the inventory's declarations name them."""
    return frozenset(declaration.rpartition("::")[0] for declaration in inventory.declarations)


# The id the inventory gives the struct's declaration, which a fact links to say which of two
# same-named structs it means.
def struct_declaration(field: ResponseField) -> str:
    return f"{field.file}::type {field.struct}"


@dataclass(frozen=True)
class FactNames:
    """Each kind of name a fact can hold, sorted, and a line for each name no fact can hold."""

    routes: tuple[str, ...]
    # The params the service reads, and the parts of its paths in braces.
    params: tuple[str, ...]
    error_codes: tuple[str, ...]
    structs: tuple[str, ...]
    fields: tuple[str, ...]
    left_out: tuple[str, ...]

    @classmethod
    def of(cls, inventory: Inventory) -> "FactNames":
        routes = [route for route in inventory.routes if route_is_writable(route)]
        fields = [field for field in inventory.response_fields if field_is_writable(field)]
        params = [param.name for param in inventory.params]
        params += [name for route in routes for name in path_wildcards(route.path)]
        return cls(
            routes=_sorted(name for route in routes for name in fact_routes(route)),
            params=_sorted(name for name in params if can_hold(PARAM_TEXT, name)),
            # The inventory reads an error code with a fact's own type, so each one is writable.
            error_codes=_sorted(error.code for error in inventory.error_codes),
            structs=_sorted(field.struct for field in fields),
            fields=_sorted(field.name for field in fields),
            left_out=_left_out(inventory),
        )


def _sorted(names: Iterable[str]) -> tuple[str, ...]:
    return tuple(sorted(set(names)))


def _left_out(inventory: Inventory) -> tuple[str, ...]:
    unwritable = [
        *(f"route {route.key}" for route in inventory.routes if not route_is_writable(route)),
        *(f"param {p.name}" for p in inventory.params if not can_hold(PARAM_TEXT, p.name)),
        *(
            f"field {field.struct}.{field.name}"
            for field in inventory.response_fields
            if not field_is_writable(field)
        ),
    ]
    return tuple(f"{name} cannot be written in a fact, so it gets no docs" for name in unwritable)

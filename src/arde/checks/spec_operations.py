"""The operations of the generated OpenAPI file, as the checks read them."""

import json
import math
from collections.abc import Iterator, Mapping
from pathlib import Path

from pydantic import Field

from arde.domain.base import Frozen
from arde.domain.errors import ArdeError
from arde.domain.report import ExitCode

# The keys of a path item that hold an operation. The others, such as summary, describe the path.
_OPERATION_KEYS = ("get", "put", "post", "delete", "options", "head", "patch", "trace")
# Where a reply's schema sits in a response.
_JSON = "application/json"


class SpecUnreadable(ArdeError):
    def __init__(self, path: Path, reason: str) -> None:
        super().__init__(
            f"Cannot read the generated spec {path}: {reason}.",
            next_action="This is a bug in ARDE's spec step. Report it with this message.",
            exit_code=ExitCode.CHECKS_FAILED,
        )


class SpecParam(Frozen):
    name: str
    # Where the param goes: path, query, header or cookie.
    location: str
    required: bool = False
    type: str | None = None
    # Each value as it is written in a request.
    enum: tuple[str, ...] = ()
    minimum: int | None = None
    maximum: int | None = None
    default: str | None = None


class Operation(Frozen):
    method: str
    path: str
    params: tuple[SpecParam, ...] = ()
    # By status, as the spec writes it, the schema of a JSON reply, or None for a reply it gives
    # no schema.
    responses: dict[str, dict[str, object] | None] = Field(default_factory=dict)

    @property
    def key(self) -> str:
        return f"{self.method} {self.path}"


def read_spec(path: Path) -> dict[str, object]:
    try:
        spec = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise SpecUnreadable(path, str(error)) from error
    if not isinstance(spec, dict):
        raise SpecUnreadable(path, "it holds no JSON object")
    return spec


def operations(spec: Mapping[str, object]) -> list[Operation]:
    """Every operation in the spec, by path and then in the order of _OPERATION_KEYS."""
    return [
        _operation(method, path, operation)
        for path, item in _items(spec.get("paths"))
        for method in _OPERATION_KEYS
        if isinstance(operation := _mapping(item).get(method), dict)
    ]


def _operation(method: str, path: str, operation: dict[str, object]) -> Operation:
    return Operation(
        method=method.upper(),
        path=path,
        params=tuple(_param(param) for param in _list(operation.get("parameters"))),
        responses={
            status: _reply_schema(response)
            for status, response in _items(operation.get("responses"))
        },
    )


def _param(param: object) -> SpecParam:
    fields = _mapping(param)
    schema = _mapping(fields.get("schema"))
    kind = schema.get("type")
    default = schema.get("default")
    return SpecParam(
        name=str(fields.get("name", "")),
        location=str(fields.get("in", "")),
        required=fields.get("required") is True,
        type=kind if isinstance(kind, str) else None,
        enum=tuple(_as_request_text(value) for value in _list(schema.get("enum"))),
        minimum=_whole_number(schema.get("minimum")),
        maximum=_whole_number(schema.get("maximum")),
        default=None if default is None else _as_request_text(default),
    )


def _reply_schema(response: object) -> dict[str, object] | None:
    media = _mapping(_mapping(_mapping(response).get("content")).get(_JSON))
    schema = media.get("schema")
    return schema if isinstance(schema, dict) else None


# How a value reads in a query, where true is written true and not True.
def _as_request_text(value: object) -> str:
    return json.dumps(value) if isinstance(value, bool) else str(value)


# The bounds ARDE renders are whole numbers. A bound with a fraction cannot be stepped past by one.
def _whole_number(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        return None
    return int(value) if value == int(value) else None


def _items(value: object) -> Iterator[tuple[str, object]]:
    return iter(_mapping(value).items())


def _mapping(value: object) -> dict[str, object]:
    return value if isinstance(value, dict) else {}


def _list(value: object) -> list[object]:
    return value if isinstance(value, list) else []

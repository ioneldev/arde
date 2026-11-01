"""What code extracts from the source with the profile's patterns. Never stored."""

from arde.domain.base import Frozen


class Route(Frozen):
    method: str
    path: str
    handler: str


class Param(Frozen):
    name: str


class ErrorCode(Frozen):
    code: str
    status: str


class ResponseField(Frozen):
    struct: str
    name: str
    go_type: str
    omitempty: bool


class Inventory(Frozen):
    routes: tuple[Route, ...]
    params: tuple[Param, ...]
    error_codes: tuple[ErrorCode, ...]
    response_fields: tuple[ResponseField, ...]
    env_vars: tuple[str, ...]
    make_targets: tuple[str, ...]

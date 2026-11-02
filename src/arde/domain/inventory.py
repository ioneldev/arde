"""What code extracts from the source with the profile's patterns. Never stored."""

from arde.domain.base import Frozen

# What Route.handler holds when the registration gives no plain name to follow, such as a function
# written in place or a call that wraps another handler.
INLINE_HANDLER = "(inline)"


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
    # The Go field name, which the renderer needs. json_name is the key a client sees.
    name: str
    json_name: str
    declared_type: str
    omitempty: bool


class Inventory(Frozen):
    routes: tuple[Route, ...]
    params: tuple[Param, ...]
    error_codes: tuple[ErrorCode, ...]
    response_fields: tuple[ResponseField, ...]
    env_vars: tuple[str, ...]
    make_targets: tuple[str, ...]

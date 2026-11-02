"""What code extracts from the source with the profile's patterns. Never stored.

Every text here was read from a repo and goes on to prompts and tool schemas, so each field is one
line with no control character, and of a length far past any real name, type or path.
"""

from typing import Annotated

from pydantic import Field

from arde.domain.base import ErrorCodeName, Frozen, SingleLine

NAME_LIMIT = 200
TYPE_LIMIT = 500
PATH_LIMIT = 1000
FILE_LIMIT = 1024
STATUS_LIMIT = 80
DROPPED_LIMIT = 500

NameText = Annotated[SingleLine, Field(max_length=NAME_LIMIT)]
TypeText = Annotated[SingleLine, Field(max_length=TYPE_LIMIT)]
PathText = Annotated[SingleLine, Field(max_length=PATH_LIMIT)]
FileText = Annotated[SingleLine, Field(max_length=FILE_LIMIT)]
StatusText = Annotated[SingleLine, Field(max_length=STATUS_LIMIT)]
DroppedLine = Annotated[SingleLine, Field(max_length=DROPPED_LIMIT)]
CodeText = Annotated[ErrorCodeName, Field(max_length=NAME_LIMIT)]

# What Route.handler holds when the registration gives no plain name to follow, such as a function
# written in place or a call that wraps another handler.
INLINE_HANDLER = "(inline)"


class Route(Frozen):
    method: NameText
    path: PathText
    handler: NameText


class Param(Frozen):
    name: NameText


class ErrorCode(Frozen):
    code: CodeText
    status: StatusText


class ResponseField(Frozen):
    struct: NameText
    # The Go field name, which the renderer needs. json_name is the key a client sees.
    name: NameText
    json_name: NameText
    declared_type: TypeText
    omitempty: bool
    # The file that declares the struct, so two structs with one name in different packages stay
    # apart. It comes last so that the model sorts by struct and name first.
    file: FileText


class Inventory(Frozen):
    routes: tuple[Route, ...]
    params: tuple[Param, ...]
    error_codes: tuple[ErrorCode, ...]
    response_fields: tuple[ResponseField, ...]
    env_vars: tuple[NameText, ...]
    make_targets: tuple[NameText, ...]
    # One short line for each thing that was found and gave nothing, because an argument was not a
    # string literal or its text could not be read. Without it a repo with constant error codes
    # looks like one with none.
    dropped: tuple[DroppedLine, ...] = ()

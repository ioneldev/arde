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
FUNCTION_LIMIT = 1000
DECLARATION_LIMIT = FILE_LIMIT + NAME_LIMIT + 10

NameText = Annotated[SingleLine, Field(max_length=NAME_LIMIT)]
TypeText = Annotated[SingleLine, Field(max_length=TYPE_LIMIT)]
PathText = Annotated[SingleLine, Field(max_length=PATH_LIMIT)]
FileText = Annotated[SingleLine, Field(max_length=FILE_LIMIT)]
StatusText = Annotated[SingleLine, Field(max_length=STATUS_LIMIT)]
DroppedLine = Annotated[SingleLine, Field(max_length=DROPPED_LIMIT)]
FunctionText = Annotated[SingleLine, Field(max_length=FUNCTION_LIMIT)]
DeclarationText = Annotated[SingleLine, Field(max_length=DECLARATION_LIMIT)]
CodeText = Annotated[ErrorCodeName, Field(max_length=NAME_LIMIT)]

# What Route.handler holds when the registration gives no plain name to show, such as a function
# written in place.
INLINE_HANDLER = "(inline)"


class Route(Frozen):
    method: NameText
    path: PathText
    # A name to show a reader. handler_id is what says which function it is.
    handler: NameText
    file: FileText
    line: int
    # The exact function behind the route, and where it is declared. None when the source does not
    # name one, as with a function written in place, a local variable or a field.
    handler_id: FunctionText | None = None
    handler_file: FileText | None = None
    handler_line: int | None = None

    @property
    def key(self) -> str:
        return f"{self.method} {self.path}"


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
    # Every declaration of the service, as file::kind name, so a fact can be checked against them.
    declarations: tuple[DeclarationText, ...] = ()
    # One short line for each thing that was found and gave nothing, because an argument was not a
    # string literal, its text could not be read or it lies outside the service. Without it a repo
    # with constant error codes looks like one with none.
    dropped: tuple[DroppedLine, ...] = ()

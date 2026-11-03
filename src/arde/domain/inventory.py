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
PATTERN_LIMIT = 600
ROUTE_KEY_LIMIT = NAME_LIMIT + 1 + PATH_LIMIT

NameText = Annotated[SingleLine, Field(max_length=NAME_LIMIT)]
TypeText = Annotated[SingleLine, Field(max_length=TYPE_LIMIT)]
PathText = Annotated[SingleLine, Field(max_length=PATH_LIMIT)]
FileText = Annotated[SingleLine, Field(max_length=FILE_LIMIT)]
StatusText = Annotated[SingleLine, Field(max_length=STATUS_LIMIT)]
DroppedLine = Annotated[SingleLine, Field(max_length=DROPPED_LIMIT)]
FunctionText = Annotated[SingleLine, Field(max_length=FUNCTION_LIMIT)]
DeclarationText = Annotated[SingleLine, Field(max_length=DECLARATION_LIMIT)]
PatternText = Annotated[SingleLine, Field(max_length=PATTERN_LIMIT)]
RouteKeyText = Annotated[SingleLine, Field(max_length=ROUTE_KEY_LIMIT)]
HashText = Annotated[str, Field(pattern=r"^(?:[0-9a-f]{64})?$")]
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
    # The one function the route's docs belong on, and where it is declared: the handler, or the
    # factory that makes it. None when no single function fits, as with a function written in
    # place, a local variable or a field, or when shares_anchor_with names other routes.
    handler_id: FunctionText | None = None
    handler_file: FileText | None = None
    handler_line: int | None = None
    # Every function of the service the handler expression names, handler_id among them: the
    # factory, the middleware and a function handed over as a value. The fingerprint covers them
    # all. Empty when no function fits. Kept when the anchor is shared, and present with no anchor
    # for a handler called through a middleware variable that nothing writes again.
    roots: tuple[FunctionText, ...] = ()
    # A hash of the constants the handler expression holds, such as the 5 in paged(5, h), or empty
    # when it holds none. The fingerprint covers it, because no root's code holds those constants.
    constants_hash: HashText = ""
    # The routes whose handlers differ from this one's and resolve to the function that would be
    # its anchor. One comment block would merge their operations in the spec, so this route has no
    # handler_id. Only a route whose handler is the anchor alone keeps it beside them.
    shares_anchor_with: tuple[RouteKeyText, ...] = ()

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
    # The field's name in the source, which the renderer needs. json_name is the key a client sees.
    name: NameText
    json_name: NameText
    declared_type: TypeText
    # The key can be left out of a reply, so a client may not find it.
    may_be_absent: bool
    # The file and package that declare the struct, so two structs with one name stay apart. They
    # come last so that the model sorts by struct and name first.
    file: FileText
    package: NameText


class Inventory(Frozen):
    routes: tuple[Route, ...]
    params: tuple[Param, ...]
    error_codes: tuple[ErrorCode, ...]
    response_fields: tuple[ResponseField, ...]
    env_vars: tuple[NameText, ...]
    make_targets: tuple[NameText, ...]
    # Every declaration of the service, as file::kind name, so a fact can be checked against them.
    declarations: tuple[DeclarationText, ...] = ()
    # One short line for each thing that was found and gave nothing, because an argument was
    # missing, empty or not a string literal, its text could not be read or it lies outside the
    # service. Without it a repo with constant error codes looks like one with none.
    dropped: tuple[DroppedLine, ...] = ()
    # Each pattern of the profile that matched no call, as "kind regex". A dead pattern looks the
    # same as a repo with no such calls, and only this tells them apart.
    unmatched_patterns: tuple[PatternText, ...] = ()

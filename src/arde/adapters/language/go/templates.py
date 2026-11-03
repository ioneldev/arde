"""The text of every comment ARDE writes into Go source, from facts that passed validation.

Each line is written so that swag v2.0.0-rc6 reads exactly what the fact says, and so that a later
run's comparison reads it as an ordinary comment, which it ignores.
"""

import re
from collections.abc import Iterator, Sequence

from arde.adapters.language.go.directives import is_ordinary_comment
from arde.adapters.language.go.errors import RenderError
from arde.domain.facts import EndpointFact, ParamFact, ResponseFact

# The repo holds no version of its API, and an invented one would read as a fact.
API_VERSION = "0.0.0"

# What swag accepts in an @Router line: one of these methods, and a path of these characters.
ROUTER_METHODS = frozenset({"GET", "PUT", "POST", "DELETE", "OPTIONS", "HEAD", "PATCH"})
_ROUTER_PATH = re.compile(r"/[A-Za-z0-9_./\-{}()+:$]*")

# swag finds a param's attributes, such as default(1), by searching the whole line for a name and
# a parenthesis, the description included. A space before each parenthesis there keeps the
# description from adding one, and a parenthesis in the name cannot be moved at all.
_PARENTHESIS_AFTER_TEXT = re.compile(r"(?<=\S)\(")

# Each mark that makes a line more than an ordinary comment to directives.is_ordinary_comment,
# and how a space or a hyphen breaks it. Only the start of a comment can be a spaced directive.
_MARKS = (
    (re.compile(r"/\*"), "/ *"),
    (re.compile(r"\*/"), "* /"),
    (re.compile(r"\+(?=build)", re.IGNORECASE), "+ "),
    (re.compile(r"Code generated"), "Code-generated"),
    (re.compile(r"^(\s*)#(?=cgo)", re.IGNORECASE), r"\1# "),
    (re.compile(r"^(\s*(?:unordered\s+)?output):", re.IGNORECASE), r"\1 :"),
)


def general_block(title: str) -> list[str]:
    """The annotations of the API as a whole, which go above func main."""
    return [_comment(f"@title {title}"), _comment(f"@version {API_VERSION}")]


def endpoint_block(facts: Sequence[EndpointFact]) -> list[str]:
    """The annotations of one function that serves the routes of the facts.

    A function carries one comment, so the first fact describes the operation, and each fact adds
    its route.
    """
    first = facts[0]
    return [
        _comment(f"@Summary {first.summary}"),
        _comment("@Produce json"),
        *(param_line(param) for param in first.params),
        *(_response_line(response) for response in first.responses),
        *(_router_line(fact.route) for fact in facts),
    ]


def param_line(param: ParamFact) -> str:
    if "(" in param.name or ")" in param.name:
        raise RenderError(
            f"The param {param.name} has a parenthesis in its name, which swag would read as "
            "an attribute of the param.",
            next_action="Rename the param in the code, or remove it from the wiki, then run again.",
        )
    required = "true" if param.required else "false"
    description = _between_quotes(param.description)
    attributes = " ".join(_attributes(param))
    return _comment(
        f'@Param {param.name} {param.location} {param.declared_type} {required} "{description}" '
        f"{attributes}"
    )


def field_comment(description: str) -> list[str]:
    """The comment above a field, or none for a description with nothing in it."""
    return [_comment(description)] if description.strip() else []


def _attributes(param: ParamFact) -> Iterator[str]:
    if param.enum:
        yield f"Enums({', '.join(param.enum)})"
    if param.minimum is not None:
        yield f"minimum({param.minimum})"
    if param.maximum is not None:
        yield f"maximum({param.maximum})"
    if param.default is not None:
        yield f"default({param.default})"


def _between_quotes(text: str) -> str:
    return _PARENTHESIS_AFTER_TEXT.sub(" (", text.replace('"', "'"))


def _response_line(response: ResponseFact) -> str:
    kind = "Success" if response.status < 400 else "Failure"
    # swag reads no pointer in a type, and a pointer is encoded as what it points to.
    declared_type = response.declared_type.replace("*", "")
    description = response.description.replace('"', "'")
    return _comment(f'@{kind} {response.status} {{object}} {declared_type} "{description}"')


def _router_line(route: str) -> str:
    method, path = route.split(" ", 1)
    if method not in ROUTER_METHODS or not _ROUTER_PATH.fullmatch(path):
        raise RenderError(
            f"swag cannot read the route {route} in an @Router line: it takes the methods "
            f"{', '.join(sorted(ROUTER_METHODS))}, and paths of ASCII letters, digits and "
            "_ . / - { } ( ) + : $ only.",
            next_action=(
                "Remove the fact for that route from the wiki, so the route goes without an "
                "annotation, then run again."
            ),
        )
    return _comment(f"@Router {path} [{method.lower()}]")


def _comment(text: str) -> str:
    for mark, broken in _MARKS:
        text = mark.sub(broken, text)
    line = f"// {text}".rstrip()
    if not is_ordinary_comment(line):
        raise RenderError(
            f"ARDE rendered a comment that is not an ordinary one: {line[:200]!r}",
            next_action="This is a bug in ARDE's renderer. Report it with this line.",
        )
    return line

"""The base every domain model builds on, and the helpers that keep model text safe."""

import re
from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict, ValidationError


class Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


# Without the input, because the text goes to the model and the logs, and the input may hold
# anything the model was sent.
def describe_problems(error: ValidationError, *, whole: str) -> str:
    """Says what broke which field, naming the object itself as whole when no field is to blame."""
    return "; ".join(
        f"{'.'.join(str(part) for part in problem['loc']) or whole}: {problem['msg']}"
        for problem in error.errors(include_input=False, include_url=False)
    )


def _reject_line_breaks(text: str) -> str:
    if "\n" in text or "\r" in text:
        raise ValueError("must be a single line: remove the line break")
    return text


# Text the renderer writes into source comments. A second line there could be a compiler
# directive, so a line break is refused when the model is built, not when it is rendered.
SingleLine = Annotated[str, AfterValidator(_reject_line_breaks)]


# fullmatch rather than "^...$", because "$" also accepts a trailing newline.
def _matching(pattern: str, requirement: str) -> AfterValidator:
    compiled = re.compile(pattern)

    def require_match(text: str) -> str:
        if compiled.fullmatch(text) is None:
            raise ValueError(f"must be {requirement}")
        return text

    return AfterValidator(require_match)


# The names below reach generated comments and struct tags, where a quote, a backtick, a comma
# or a line break would end the tag or start a directive. Each type admits the known shape and
# nothing more. Identifiers are ASCII only: a name outside it is refused rather than escaped.
_IDENTIFIER = r"[A-Za-z_][A-Za-z0-9_]*"
_TYPE_PREFIX = r"(?:\[\]|\*)*"  # stacks, as in []*Product
_PLAIN_CHAR = r"""[^\s"'`]"""

CodeIdentifier = Annotated[
    str,
    _matching(
        _IDENTIFIER,
        "a code identifier: use only letters, digits and underscore, and do not start with a digit",
    ),
]

QualifiedTypeName = Annotated[
    str,
    _matching(
        rf"{_TYPE_PREFIX}{_IDENTIFIER}(?:\.{_IDENTIFIER})*",
        "a type name: use identifiers joined by dots, with an optional leading [] or *, "
        "and no spaces, quotes, backticks or commas",
    ),
]

ErrorCodeName = Annotated[
    str,
    _matching(
        r"[A-Za-z0-9_.-]+",
        "an error code: use only letters, digits, underscore, hyphen and dot",
    ),
]

RoutePattern = Annotated[
    str,
    _matching(
        rf"[A-Z]+ /{_PLAIN_CHAR}*",
        "a route: an upper-case method, one space, then a path that starts with / and has "
        "no whitespace, quotes or backticks",
    ),
]

ParamName = Annotated[
    str,
    _matching(
        rf"{_PLAIN_CHAR}+",
        "a parameter name: use no whitespace, quotes or backticks",
    ),
]

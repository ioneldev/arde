"""The base every domain model builds on, and the helpers that keep model text safe."""

import json
import re
from pathlib import PurePosixPath
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


# How much of a name from a repo a message shows.
_SHOWN_NAME_LENGTH = 200


# A file name can hold anything but a slash and NUL. json.dumps escapes every control character
# and every letter outside ASCII, and the quotes it adds are cut, so a plain name reads as it is.
def printable_name(name: str) -> str:
    return json.dumps(name[:_SHOWN_NAME_LENGTH])[1:-1][:_SHOWN_NAME_LENGTH]


# Go refuses NUL and a byte order mark inside a source file, so either one stops the build after
# the paid work is done. The other control characters are refused for the same reason: none has a
# place in a comment or a tag. The two Unicode separators would make one line read as two.
_CONTROL_CHARACTERS = "\x00-\x1f\x7f-\x9f\u2028\u2029\ufeff"
_CONTROL_CHARACTER = re.compile(f"[{_CONTROL_CHARACTERS}]")


# Cut first, because one literal in a repo can run to many megabytes.
def one_line(text: str, limit: int) -> str:
    """The first characters of repo text, with each break or control character made a space."""
    return " ".join(_CONTROL_CHARACTER.sub(" ", text[:limit]).split())


def _require_clean_single_line(text: str) -> str:
    if "\n" in text or "\r" in text:
        raise ValueError("must be a single line: remove the line break")
    if _CONTROL_CHARACTER.search(text):
        raise ValueError("must hold no control character or byte order mark: remove it")
    return text


# Text the renderer writes into source comments. A second line there could be a compiler
# directive, so a line break is refused when the model is built, not when it is rendered.
SingleLine = Annotated[str, AfterValidator(_require_clean_single_line)]


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
_PLAIN_CHAR = rf"""[^\s"'`{_CONTROL_CHARACTERS}]"""

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


# A backslash counts as a separator: the host decides what the path means, and code that joins
# this text to a root must not need to know which host it is on.
def _as_posix_path(text: str) -> PurePosixPath:
    return PurePosixPath(text.replace("\\", "/"))


def _leaves_the_repo(path: str) -> bool:
    as_path = _as_posix_path(path)
    return as_path.is_absolute() or ".." in as_path.parts


def _require_package_inside_repo(package: str) -> str:
    if _leaves_the_repo(package):
        raise ValueError("must stay inside the repo: use no .. and no absolute path")
    return package


# Every package at once, which the Go tool writes with dots and which names no hidden folder.
_ALL_PACKAGES = "./..."


# A folder that starts with a dot is out of the Go tool's reach, so no real service lives in one,
# and .git is the one folder the repo-links gate does not walk.
def _require_no_hidden_folder(package: str) -> str:
    after_the_leading_dot = package.replace("\\", "/").split("/")[1:]
    if package != _ALL_PACKAGES and any(part.startswith(".") for part in after_the_leading_dot):
        raise ValueError("must name no folder that starts with a dot")
    return package


# The leading dot ties the package to this repo: any other text can name a package anywhere, such
# as cmd/go in the toolchain or a dependency's program. It also stops the text being read as a
# flag, because the build tool reads a leading hyphen as one, and one of its flags runs any command
# it is given.
MainPackage = Annotated[
    str,
    _matching(
        rf"\.(?:/{_PLAIN_CHAR}+)?",
        "a package in this repo: use . or a path that starts with ./, with no whitespace, quotes "
        "or backticks",
    ),
    AfterValidator(_require_package_inside_repo),
    AfterValidator(_require_no_hidden_folder),
]


def _require_repo_relative(path: str) -> str:
    if not path or "\x00" in path or _leaves_the_repo(path):
        raise ValueError("must be a file path relative to the repo root, with no ..")
    return path


# A path a model chose. Joined to a root, an absolute path discards the root, and that is a route
# from repo text to any file ARDE can read, the API key among them.
RepoRelativePath = Annotated[str, AfterValidator(_require_repo_relative)]


# The main file is handed to a tool as an argument, where a leading hyphen is a flag.
def _require_a_file_that_is_not_a_flag(path: str) -> str:
    if path.startswith("-"):
        raise ValueError("must not start with a hyphen, which a tool would read as a flag")
    if not _as_posix_path(path).parts:
        raise ValueError("must name a file, not the repo root")
    return path


MainFile = Annotated[
    RepoRelativePath,
    AfterValidator(_require_a_file_that_is_not_a_flag),
    _matching(
        rf"{_PLAIN_CHAR}+",
        "a file path: use no whitespace, quotes, backticks or control characters",
    ),
]

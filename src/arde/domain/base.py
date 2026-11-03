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


# The end of a long path names the file or folder that failed, and its start only the folders above
# the repo, so this keeps the end. A path is at most a few kilobytes, so the whole is escaped.
def printable_end_of_path(path: str) -> str:
    escaped = json.dumps(path)[1:-1]
    if len(escaped) <= _SHOWN_NAME_LENGTH:
        return escaped
    return "..." + escaped[-_SHOWN_NAME_LENGTH:]


# The characters no reader can see as themselves: every code point of the Unicode categories Cc
# (controls), Cf (format characters, such as the direction controls, the zero-width space and the
# byte order mark), Zl and Zp (the line and paragraph separators), as the Unicode database of this
# Python lists them, and a test checks the list against it. A message shows each one escaped.
UNPRINTABLE_RANGES: tuple[tuple[int, int], ...] = (
    (0x0000, 0x001F),
    (0x007F, 0x009F),
    (0x00AD, 0x00AD),
    (0x0600, 0x0605),
    (0x061C, 0x061C),
    (0x06DD, 0x06DD),
    (0x070F, 0x070F),
    (0x0890, 0x0891),
    (0x08E2, 0x08E2),
    (0x180E, 0x180E),
    (0x200B, 0x200F),
    (0x2028, 0x202E),
    (0x2060, 0x2064),
    (0x2066, 0x206F),
    (0xFEFF, 0xFEFF),
    (0xFFF9, 0xFFFB),
    (0x110BD, 0x110BD),
    (0x110CD, 0x110CD),
    (0x13430, 0x1343F),
    (0x1BCA0, 0x1BCA3),
    (0x1D173, 0x1D17A),
    (0xE0001, 0xE0001),
    (0xE0020, 0xE007F),
)
# The ranges as the inside of a regex character class.
UNPRINTABLE_CHARACTERS = "".join(
    f"\\U{first:08x}-\\U{last:08x}" for first, last in UNPRINTABLE_RANGES
)
_UNPRINTABLE_CHARACTER = re.compile(f"[{UNPRINTABLE_CHARACTERS}]")

# The characters text bound for Go source may not hold, which are fewer: Go refuses NUL and a byte
# order mark in a source file, a control or a separator makes one line read as two, and a direction
# embedding, override or isolate reorders what a reader of the source sees around it. Every other
# format character stays, because a language writes with it, as Persian does with the zero-width
# non-joiner and an emoji with the joiner, or because it does nothing in a comment.
REFUSED_RANGES: tuple[tuple[int, int], ...] = (
    (0x0000, 0x001F),
    (0x007F, 0x009F),
    (0x2028, 0x202E),
    (0x2066, 0x2069),
    (0xFEFF, 0xFEFF),
)
_REFUSED_CHARACTERS = "".join(f"\\U{first:08x}-\\U{last:08x}" for first, last in REFUSED_RANGES)
_REFUSED_CHARACTER = re.compile(f"[{_REFUSED_CHARACTERS}]")


# Cut first, because one literal in a repo can run to many megabytes.
def one_line(text: str, limit: int) -> str:
    """The first characters of repo text, with each break or unprintable character a space."""
    return " ".join(_UNPRINTABLE_CHARACTER.sub(" ", text[:limit]).split())


# The code point and where it is, because the character itself cannot be seen.
def _require_no_refused_character(text: str) -> str:
    refused = _REFUSED_CHARACTER.search(text)
    if refused is None:
        return text
    where = f"U+{ord(refused[0]):04X} at offset {refused.start()}"
    if refused[0] in "\n\r":
        raise ValueError(f"must be a single line: remove the line break {where}")
    raise ValueError(
        "must be plain text, with no control character, line or paragraph separator, byte order "
        f"mark or direction control: remove {where}"
    )


# Text the renderer writes into source comments. A second line there could be a compiler
# directive, so a line break is refused when the model is built, not when it is rendered.
SingleLine = Annotated[str, AfterValidator(_require_no_refused_character)]


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
_PLAIN_CHAR = rf"""[^\s"'`{_REFUSED_CHARACTERS}]"""

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
    AfterValidator(_require_no_refused_character),
    _matching(
        rf"[A-Z]+ /{_PLAIN_CHAR}*",
        "a route: an upper-case method, one space, then a path that starts with / and has "
        "no whitespace, quotes or backticks",
    ),
]

ParamName = Annotated[
    str,
    AfterValidator(_require_no_refused_character),
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


# Every package at once, which the build tool writes with dots and which names no hidden folder.
_ALL_PACKAGES = "./..."


# A folder that starts with a dot is out of the build tool's reach, so no real service lives in
# one, and a model has no reason to name one.
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
    AfterValidator(_require_no_refused_character),
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
    AfterValidator(_require_no_refused_character),
    AfterValidator(_require_a_file_that_is_not_a_flag),
    _matching(
        rf"{_PLAIN_CHAR}+",
        "a file path: use no whitespace, quotes, backticks or control characters",
    ),
]

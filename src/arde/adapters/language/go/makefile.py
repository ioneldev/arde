"""Make targets by regex. A Makefile can do anything, so only plain rule lines are read."""

import os
import re
from pathlib import Path

from arde.adapters.child_process import describe_os_error
from arde.domain.errors import ArdeError
from arde.domain.report import ExitCode

# The order GNU make looks for its file in.
MAKEFILE_NAMES = ("GNUmakefile", "makefile", "Makefile")

# Far past any file a person maintains. ARDE reads this itself, with no timeout to stop it.
MAKEFILE_BYTES_LIMIT = 1024 * 1024

# One or more names, then a colon that starts a rule. ":=" and "::=" assign a variable. A name
# that starts with "." is special (.PHONY), one with "%" is a pattern, and "$(" is a variable.
_RULE = re.compile(
    r"^([A-Za-z0-9_][\w./-]*(?:[ \t]+[A-Za-z0-9_][\w./-]*)*)[ \t]*(?::(?![:=])|::(?!=))",
    re.MULTILINE,
)


class MakefileNotReadableError(ArdeError):
    def __init__(self, name: str, reason: str) -> None:
        super().__init__(
            f"Cannot read the repo's {name}: {reason}.",
            next_action=(
                f"Make {name} a readable file of at most {MAKEFILE_BYTES_LIMIT} bytes, or remove "
                "it, then run again."
            ),
            exit_code=ExitCode.BAD_USAGE,
        )


def make_targets(text: str) -> tuple[str, ...]:
    names = {name for rule in _RULE.finditer(text) for name in rule.group(1).split()}
    return tuple(sorted(names))


def make_targets_of(repo: Path) -> tuple[str, ...]:
    for name in MAKEFILE_NAMES:
        makefile = repo / name
        if os.path.isfile(makefile):
            return make_targets(_text_of(makefile))
    return ()


def _text_of(makefile: Path) -> str:
    try:
        size = makefile.stat().st_size
        if size > MAKEFILE_BYTES_LIMIT:
            raise MakefileNotReadableError(makefile.name, f"it holds {size} bytes")
        return makefile.read_text(encoding="utf-8", errors="replace")
    except OSError as error:
        raise MakefileNotReadableError(makefile.name, describe_os_error(error)) from error

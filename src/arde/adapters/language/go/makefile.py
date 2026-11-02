"""Make targets by regex. A Makefile can do anything, so only plain rule lines are read."""

import re
from pathlib import Path

# The order GNU make looks for its file in.
MAKEFILE_NAMES = ("GNUmakefile", "makefile", "Makefile")

# One or more names, then a colon that starts a rule. ":=" and "::=" assign a variable. A name
# that starts with "." is special (.PHONY), one with "%" is a pattern, and "$(" is a variable.
_RULE = re.compile(
    r"^([A-Za-z0-9_][\w./-]*(?:[ \t]+[A-Za-z0-9_][\w./-]*)*)[ \t]*(?::(?![:=])|::(?!=))",
    re.MULTILINE,
)


def make_targets(text: str) -> tuple[str, ...]:
    names = {name for rule in _RULE.finditer(text) for name in rule.group(1).split()}
    return tuple(sorted(names))


def make_targets_of(repo: Path) -> tuple[str, ...]:
    for name in MAKEFILE_NAMES:
        makefile = repo / name
        if makefile.is_file():
            return make_targets(makefile.read_text(encoding="utf-8", errors="replace"))
    return ()

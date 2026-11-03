"""The environment a child process gets: a short allow-list, never a copy of ours.

This is the generic list. It holds no variable of any toolchain, so a service started to be
probed gets none. A language adapter builds on it for its own commands.
"""

import os
from collections.abc import Iterable

# A deny-list would have to know every secret in the parent's environment.
ALLOWED_VARIABLES = ("PATH", "HOME", "TMPDIR", "LANG", "LC_ALL")


def exported(names: Iterable[str]) -> dict[str, str]:
    return {name: os.environ[name] for name in names if name in os.environ}


def child_environment() -> dict[str, str]:
    environment = exported(ALLOWED_VARIABLES)
    absolute_entries = _absolute_entries(environment.pop("PATH", ""))
    if absolute_entries:
        environment["PATH"] = os.pathsep.join(absolute_entries)
    return environment


# A child changes folder before it looks for a program, so "." or an empty entry would find one the
# repo planted. An empty PATH reads as the current folder too, so one with nothing left is left out.
def _absolute_entries(path: str) -> list[str]:
    return [entry for entry in path.split(os.pathsep) if os.path.isabs(entry)]

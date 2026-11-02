"""The environment a child process gets: a short allow-list, never a copy of ours.

This is the generic list. It holds no variable of any toolchain, so a service started to be
probed gets none. A language adapter builds on it for its own commands.
"""

import os

# A deny-list would have to know every secret in the parent's environment.
ALLOWED_VARIABLES = ("PATH", "HOME", "TMPDIR", "LANG", "LC_ALL")


def child_environment() -> dict[str, str]:
    return {name: os.environ[name] for name in ALLOWED_VARIABLES if name in os.environ}

"""The environment of every Go command and of goinspect.

It builds on the generic list in child_env, which holds no Go variable, and adds the toolchain's
own: its caches, its proxy and the settings that keep private module paths private.
"""

import functools
import os
import tempfile
from pathlib import Path

from pydantic import TypeAdapter, ValidationError

from arde.adapters.child_env import child_environment
from arde.adapters.child_process import ChildCommand, run_child
from arde.adapters.language.go.errors import GoCommandError
from arde.adapters.language.go.output_limits import GO_ENV_REPLY_LIMIT
from arde.adapters.language.go.timeouts import GO_ENV_TIMEOUT_S

# Passed on when exported. GOFLAGS is left out on purpose: it can carry -toolexec, which names a
# program that every build step then runs.
TOOLCHAIN_VARIABLES = (
    "GOPATH",
    "GOCACHE",
    "GOMODCACHE",
    "GOROOT",
    "GOPROXY",
    "GOPRIVATE",
    "GONOPROXY",
    "GONOSUMDB",
    "GOSUMDB",
    "GOINSECURE",
    "GOTOOLCHAIN",
)

# Also read from the user's go env file, which "go env -w" writes and which Go tells people to use
# for them, when they are not exported. Without them a private repo fails to build, or its module
# paths go to the public proxy and the public checksum database. The file is read for these three
# and for nothing else: a GOPROXY, GOSUMDB or GOTOOLCHAIN that only the file holds stays ignored,
# so that a poisoned file cannot redirect where modules are fetched from or which toolchain runs.
# They name the paths they apply to and no more, and docs/limitations.md says what that costs.
USER_SETTINGS = ("GOPRIVATE", "GONOPROXY", "GONOSUMDB")

# HOME is allowed, so the file would be read by every command. That is done once, here, for the
# three settings above, and the commands themselves are told to read no file.
FIXED_VARIABLES = {"GOENV": "off"}


def go_environment() -> dict[str, str]:
    exported = {name: os.environ[name] for name in TOOLCHAIN_VARIABLES if name in os.environ}
    from_the_file = {name: value for name, value in _user_settings().items() if value}
    return {**child_environment(), **from_the_file, **exported, **FIXED_VARIABLES}


# Once per process. A failure is not remembered, so the next call asks again.
@functools.cache
def _user_settings() -> dict[str, str]:
    # An empty folder of its own, never the repo: a go.mod there could change what go env says.
    with tempfile.TemporaryDirectory(prefix="arde-goenv-") as folder:
        command = ChildCommand(
            argv=("go", "env", "-json", *USER_SETTINGS),
            cwd=Path(folder),
            timeout_s=GO_ENV_TIMEOUT_S,
            output=GO_ENV_REPLY_LIMIT,
            env=child_environment(),
        )
        outcome = run_child(command)
    if not outcome.succeeded:
        raise GoCommandError(
            f"Cannot read the Go settings with go env: {outcome.output.strip()}",
            next_action=_RUN_IT_BY_HAND,
        )
    try:
        reply = TypeAdapter(dict[str, str]).validate_json(outcome.stdout)
    except ValidationError as error:
        raise GoCommandError(
            "go env printed something other than the three settings that were asked for.",
            next_action=_RUN_IT_BY_HAND,
        ) from error
    return {name: reply.get(name, "") for name in USER_SETTINGS}


# ARDE stops here and does not go on without the settings: that would send private module paths to
# the public proxy without a word.
_RUN_IT_BY_HAND = (
    "Run `go env GOPRIVATE GONOPROXY GONOSUMDB` by hand to see why it fails, fix that, and run "
    "again. ARDE does not go on without them, because private module paths would then go to the "
    "public proxy."
)

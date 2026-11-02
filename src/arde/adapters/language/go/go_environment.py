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
from arde.adapters.child_text import MESSAGE_TAIL_BYTES, bounded_text
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

# The user's own go env file, which "go env -w" writes, can hold these three, so go env is asked
# for them once and its answer is used as it stands: Go decides which value wins, and derives
# GONOPROXY and GONOSUMDB from GOPRIVATE when neither is set. Without them a private repo fails
# to build, or its module paths go to the public proxy and checksum database. The file's GOPROXY,
# GOSUMDB and GOTOOLCHAIN are not taken, so a poisoned file cannot redirect downloads or switch
# the toolchain of a command.
USER_SETTINGS = ("GOPRIVATE", "GONOPROXY", "GONOSUMDB")

# Only the probe gets these, to look where the user's own go command looks. No other child does.
PROBE_ONLY_VARIABLES = ("XDG_CONFIG_HOME", "GOENV")

# The probe never switches toolchain. A GOTOOLCHAIN in the file or a go.mod above the temporary
# folder could otherwise start a program found on PATH, or download one.
PROBE_FIXED_VARIABLES = {"GOTOOLCHAIN": "local"}

# Every command is told to read no file: HOME is allowed, and the file can hold a GOFLAGS.
FIXED_VARIABLES = {"GOENV": "off"}

# Used unless the user exports another value, because otherwise a go.mod picks a program to start
# or to download. A repo that needs a newer Go stops with Go's own clear error.
DEFAULT_VARIABLES = {"GOTOOLCHAIN": "local"}


def go_environment(repo: Path) -> dict[str, str]:
    # An empty value counts as not exported: Go reads it as unset, which would undo the default.
    exported = {
        name: os.environ[name]
        for name in TOOLCHAIN_VARIABLES
        if os.environ.get(name) and name not in USER_SETTINGS
    }
    settings = {name: value for name, value in _user_settings().items() if value}
    return {
        **child_environment(),
        **DEFAULT_VARIABLES,
        **exported,
        **settings,
        **FIXED_VARIABLES,
        **_workspace(repo),
    }


# Go looks for a go.work in every folder above the one it runs in, so a file above the repo could
# choose the code that is built and run. A blanket "off" would break a repo that needs its own.
def _workspace(repo: Path) -> dict[str, str]:
    own_file = repo.resolve() / "go.work"
    return {"GOWORK": str(own_file) if os.path.isfile(own_file) else "off"}


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
            env=_probe_environment(),
        )
        outcome = run_child(command)
    if not outcome.succeeded:
        raise GoCommandError(
            "Cannot read the Go settings with go env: "
            f"{bounded_text(outcome.output.strip(), MESSAGE_TAIL_BYTES)}",
            next_action=_RUN_IT_BY_HAND,
        )
    return _the_three_settings(outcome.stdout)


def _probe_environment() -> dict[str, str]:
    given = (*USER_SETTINGS, *PROBE_ONLY_VARIABLES)
    exported = {name: os.environ[name] for name in given if name in os.environ}
    return {**child_environment(), **exported, **PROBE_FIXED_VARIABLES}


def _the_three_settings(reply_text: str) -> dict[str, str]:
    try:
        reply = TypeAdapter(dict[str, str]).validate_json(reply_text)
    except ValidationError as error:
        raise GoCommandError(
            "go env printed something other than the three settings that were asked for.",
            next_action=_RUN_IT_BY_HAND,
        ) from error
    missing = [name for name in USER_SETTINGS if name not in reply]
    if missing:
        raise GoCommandError(
            f"go env left out {', '.join(missing)}, which were asked for.",
            next_action=_RUN_IT_BY_HAND,
        )
    return {name: reply[name] for name in USER_SETTINGS}


# ARDE stops here and does not go on without the settings: that would send private module paths to
# the public proxy without a word.
_RUN_IT_BY_HAND = (
    "Run `go env GOPRIVATE GONOPROXY GONOSUMDB` by hand to see why it fails, fix that, and run "
    "again. ARDE does not go on without them, because private module paths would then go to the "
    "public proxy."
)

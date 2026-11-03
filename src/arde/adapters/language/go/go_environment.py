"""The environment of every Go command and of goinspect.

It builds on the generic list in child_env, which holds no Go variable, and adds the toolchain's
own: its caches, its proxy and the settings that keep private module paths private.
"""

import functools
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

from pydantic import TypeAdapter, ValidationError

from arde.adapters.child_env import child_environment, exported
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

# Every command is told to read no file: HOME is allowed, and the file can hold a GOFLAGS. And
# goinspect, which loads packages through go/packages, is told to run no gopackagesdriver it finds
# on PATH: that program would decide what the routes and fingerprints are.
FIXED_VARIABLES = {"GOENV": "off", "GOPACKAGESDRIVER": "off"}

# Used unless the user exports another value, because otherwise a go.mod picks a program to start
# or to download. A repo that needs a newer Go stops with Go's own clear error.
DEFAULT_VARIABLES = {"GOTOOLCHAIN": "local"}


@dataclass(frozen=True)
class GoCaches:
    """The build cache and the module folder of one repo's Go commands.

    In one cache that every repo shares, as one volume holds them in the container, a repo's build
    or tests could write entries that another repo's commands then read.
    """

    build: Path
    path: Path

    @classmethod
    def inside(cls, folder: Path) -> "GoCaches":
        return cls(build=folder / "gocache", path=folder / "gopath")

    # GOMODCACHE too, or an exported one would still name a module folder that others share.
    def variables(self) -> dict[str, str]:
        modules = self.path / "pkg" / "mod"
        return {"GOCACHE": str(self.build), "GOPATH": str(self.path), "GOMODCACHE": str(modules)}


# With no caches of the repo's own, the exported ones apply, or else Go's defaults.
def go_environment(repo: Path, caches: GoCaches | None = None) -> dict[str, str]:
    return {
        **child_environment(),
        **DEFAULT_VARIABLES,
        **_without_empty_values(exported(TOOLCHAIN_VARIABLES)),
        **({} if caches is None else caches.variables()),
        **_without_empty_values(_user_settings()),
        **FIXED_VARIABLES,
        **_workspace(repo),
    }


# Go reads an empty value as unset, so passing one on would undo a default.
def _without_empty_values(variables: dict[str, str]) -> dict[str, str]:
    return {name: value for name, value in variables.items() if value}


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
    given = exported((*USER_SETTINGS, *PROBE_ONLY_VARIABLES))
    return {**child_environment(), **given, **PROBE_FIXED_VARIABLES}


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

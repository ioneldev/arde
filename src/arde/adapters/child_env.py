"""The environment every child process gets: a short allow-list, never a copy of ours."""

import os

# A deny-list would have to know every secret in the parent's environment. The toolchain variables
# are here because a build needs its caches and proxy. GOFLAGS is left out on purpose: it can
# carry -toolexec, which names a program that every build step then runs.
ALLOWED_VARIABLES = (
    "PATH",
    "HOME",
    "TMPDIR",
    "LANG",
    "LC_ALL",
    "GOPATH",
    "GOCACHE",
    "GOMODCACHE",
    "GOROOT",
    "GOPROXY",
)

# HOME is allowed, and the toolchain keeps its own settings in a file under it, written by "go env
# -w". That file can hold the GOFLAGS that the list above leaves out, so children do not read it.
FIXED_VARIABLES = {"GOENV": "off"}


def child_environment() -> dict[str, str]:
    allowed = {name: os.environ[name] for name in ALLOWED_VARIABLES if name in os.environ}
    return {**allowed, **FIXED_VARIABLES}

"""The versions of the tools the Go adapter runs, so a trace says what produced a run."""

import hashlib
import tempfile
from pathlib import Path

from arde.adapters.child_process import ChildCommand, ChildOutcome, OutputLimit, run_child
from arde.adapters.child_text import MESSAGE_TAIL_BYTES, bounded_text
from arde.adapters.language.go.errors import GoCommandError
from arde.adapters.language.go.go_environment import go_environment
from arde.adapters.language.go.timeouts import GO_ENV_TIMEOUT_S

# A version, or a binary's build info: a line per module it was built from.
_VERSION_REPLY_LIMIT = OutputLimit(max_bytes=64 * 1024, when_longer="fail")


def tool_versions(*, goinspect: Path, swag: Path) -> dict[str, str]:
    return {
        "go": _go_version(),
        "swag": _swag_version(swag),
        # The helper is built from this repo's own source, so its version is what was built.
        "goinspect": f"sha256:{_sha256_of(goinspect)}",
    }


def _go_version() -> str:
    return _go_says("env", "GOVERSION").strip()


# Measured: swag v2.0.0-rc6 prints "swag version v2.0.0" for --version, so the version comes from
# the build info Go wrote into the binary, which names the module version it was built from.
def _swag_version(swag: Path) -> str:
    for line in _go_says("version", "-m", str(swag)).splitlines():
        parts = line.split("\t")
        if parts[1:2] == ["mod"] and len(parts) >= 4:
            return f"{parts[2]} {parts[3]}"
    return "unknown"


# Asked in an empty folder of its own, where no go.mod can switch the toolchain.
def _go_says(*arguments: str) -> str:
    with tempfile.TemporaryDirectory(prefix="arde-goversion-") as folder:
        command = ChildCommand(
            argv=("go", *arguments),
            cwd=Path(folder),
            timeout_s=GO_ENV_TIMEOUT_S,
            output=_VERSION_REPLY_LIMIT,
            env=go_environment(Path(folder)),
        )
        outcome = run_child(command)
    if not outcome.succeeded:
        raise _cannot_tell(outcome)
    return outcome.stdout


def _cannot_tell(outcome: ChildOutcome) -> GoCommandError:
    return GoCommandError(
        "Cannot read the version of a Go tool: "
        f"{bounded_text(outcome.output.strip(), MESSAGE_TAIL_BYTES)}",
        next_action="Check that Go is installed and runs, then run again.",
    )


def _sha256_of(binary: Path) -> str:
    return hashlib.sha256(binary.read_bytes()).hexdigest()

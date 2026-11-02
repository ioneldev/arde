"""Runs one child process to the end, with the allow-listed environment and a timeout that holds."""

import contextlib
import os
import signal
import subprocess
from pathlib import Path

from arde.adapters.child_env import child_environment
from arde.domain.base import Frozen
from arde.domain.errors import ArdeError
from arde.domain.report import ExitCode


class ChildCommand(Frozen):
    argv: tuple[str, ...]
    cwd: Path
    timeout_s: float


class ChildOutcome(Frozen):
    exit_code: int
    stdout: str
    stderr: str

    @property
    def succeeded(self) -> bool:
        return self.exit_code == 0

    @property
    def output(self) -> str:
        return self.stdout + self.stderr


class ChildTimeoutError(ArdeError):
    def __init__(self, command: ChildCommand) -> None:
        super().__init__(
            f"`{' '.join(command.argv)}` ran longer than {command.timeout_s:g} s and was stopped.",
            next_action=(
                f"Run it by hand in {command.cwd} to see where it hangs, fix that, and run again."
            ),
            exit_code=ExitCode.CHECKS_FAILED,
        )


class ChildStartError(ArdeError):
    def __init__(self, command: ChildCommand, reason: str) -> None:
        super().__init__(
            f"Cannot start `{command.argv[0]}`: {reason}.",
            next_action=(
                f"Install {command.argv[0]} or put it on PATH, then run again. "
                "If the command came from a profile, fix it there."
            ),
            exit_code=ExitCode.CHECKS_FAILED,
        )


def run_child(command: ChildCommand) -> ChildOutcome:
    process = _start(command)
    try:
        stdout, stderr = process.communicate(timeout=command.timeout_s)
    except subprocess.TimeoutExpired:
        _stop(process)
        raise ChildTimeoutError(command) from None
    except BaseException:
        # Ctrl-C reaches us and not the child, which runs in its own session.
        _stop(process)
        raise
    return ChildOutcome(exit_code=process.returncode, stdout=stdout, stderr=stderr)


# Its own session, so a timeout can stop what the child started as well. Killing only the child
# would leave a grandchild holding the pipes open, and reading them would never end.
def _start(command: ChildCommand) -> subprocess.Popen[str]:
    try:
        return subprocess.Popen(
            command.argv,
            cwd=command.cwd,
            env=child_environment(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            start_new_session=True,
        )
    except OSError as error:
        raise ChildStartError(command, _describe(error)) from error


# The file named is the program, or the folder to run it in, whichever the system could not find.
def _describe(error: OSError) -> str:
    reason = error.strerror or str(error)
    return f"{reason}: {error.filename}" if error.filename else reason


def _stop(process: subprocess.Popen[str]) -> None:
    with contextlib.suppress(ProcessLookupError):
        os.killpg(process.pid, signal.SIGKILL)
    process.communicate()

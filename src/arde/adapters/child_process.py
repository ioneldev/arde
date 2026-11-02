"""Runs one child process to the end, with the allow-listed environment and a timeout that holds.

Output goes to temporary files, not pipes. A pipe stays open as long as any process holds its
end, so a grandchild that outlived the child would keep the caller waiting after the timeout.
A file has no such end, and it is read back afterwards with a limit, so a chatty child costs disk
and never memory.
"""

import contextlib
import os
import signal
import subprocess
import tempfile
from pathlib import Path
from typing import IO, Literal

from arde.domain.base import Frozen
from arde.domain.errors import ArdeError
from arde.domain.report import ExitCode

# How long to wait for a child to die after it was killed, before giving up on seeing it die.
WAIT_AFTER_KILL_S = 5


class OutputLimit(Frozen):
    """How much of each stream the caller keeps, and what happens to the rest."""

    max_bytes: int
    # "fail" refuses a longer stream, for a caller that needs all of it. "keep_tail" keeps the end,
    # which is where a failing command says what went wrong.
    when_longer: Literal["fail", "keep_tail"]


class ChildCommand(Frozen):
    argv: tuple[str, ...]
    cwd: Path
    timeout_s: float
    output: OutputLimit
    # The whole environment of the child. The caller builds it, from child_environment() or from a
    # list of its own, because what a child may see depends on what it is.
    env: dict[str, str]


class ChildOutcome(Frozen):
    exit_code: int
    stdout: str
    stderr: str
    output_was_cut: bool = False

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


class ChildOutputTooLongError(ArdeError):
    def __init__(self, command: ChildCommand, *, stream: str, size: int) -> None:
        super().__init__(
            f"`{' '.join(command.argv)}` wrote {size} bytes to {stream}, past the limit of "
            f"{command.output.max_bytes} bytes.",
            next_action=(
                f"Run it by hand in {command.cwd} to see why it writes so much, fix that, "
                "and run again."
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
    with tempfile.TemporaryFile() as stdout_file, tempfile.TemporaryFile() as stderr_file:
        process = _start(command, stdout_file=stdout_file, stderr_file=stderr_file)
        _wait_for_exit(process, command)
        # What the child started in its group is not left running after it, such as a server.
        _kill_group(process)
        stdout, stdout_was_cut = _read_back(stdout_file, command, stream="stdout")
        stderr, stderr_was_cut = _read_back(stderr_file, command, stream="stderr")
    return ChildOutcome(
        exit_code=process.returncode,
        stdout=stdout,
        stderr=stderr,
        output_was_cut=stdout_was_cut or stderr_was_cut,
    )


# Its own session, so a timeout can stop what the child started as well.
def _start(
    command: ChildCommand, *, stdout_file: IO[bytes], stderr_file: IO[bytes]
) -> subprocess.Popen[bytes]:
    try:
        return subprocess.Popen(
            command.argv,
            cwd=command.cwd,
            env=command.env,
            stdin=subprocess.DEVNULL,
            stdout=stdout_file,
            stderr=stderr_file,
            start_new_session=True,
        )
    except OSError as error:
        raise ChildStartError(command, _describe(error)) from error


# The file named is the program, or the folder to run it in, whichever the system could not find.
def _describe(error: OSError) -> str:
    reason = error.strerror or str(error)
    return f"{reason}: {error.filename}" if error.filename else reason


def _wait_for_exit(process: subprocess.Popen[bytes], command: ChildCommand) -> None:
    try:
        process.wait(timeout=command.timeout_s)
    except subprocess.TimeoutExpired:
        _stop(process)
        raise ChildTimeoutError(command) from None
    except BaseException:
        # Ctrl-C reaches us and not the child, which runs in its own session.
        _stop(process)
        raise


# A grandchild that started its own session is out of reach of the group, and the wait is bounded
# so that nothing it does can hold the caller.
def _stop(process: subprocess.Popen[bytes]) -> None:
    _kill_group(process)
    with contextlib.suppress(ProcessLookupError):
        process.kill()
    with contextlib.suppress(subprocess.TimeoutExpired):
        process.wait(timeout=WAIT_AFTER_KILL_S)


def _kill_group(process: subprocess.Popen[bytes]) -> None:
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(process.pid, signal.SIGKILL)


def _read_back(file: IO[bytes], command: ChildCommand, *, stream: str) -> tuple[str, bool]:
    limit = command.output
    size = file.seek(0, os.SEEK_END)
    if size <= limit.max_bytes:
        file.seek(0)
        return file.read().decode("utf-8", errors="replace"), False
    if limit.when_longer == "fail":
        raise ChildOutputTooLongError(command, stream=stream, size=size)
    file.seek(size - limit.max_bytes)
    note = (
        f"[ARDE kept only the last {limit.max_bytes} bytes of this output; {size} were written]\n"
    )
    return note + file.read().decode("utf-8", errors="replace"), True

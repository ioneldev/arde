"""Runs one child process to the end, with the allow-listed environment and a timeout that holds.

Output goes to temporary files, not pipes. A pipe stays open as long as any process holds its
end, so a grandchild that outlived the child would keep the caller waiting after the timeout.
A file has no such end, and it is read back afterwards with a limit, so a chatty child costs disk,
and memory only up to that limit.
"""

import contextlib
import os
import signal
import subprocess
import tempfile
from pathlib import Path
from typing import IO, Literal

from arde.adapters.child_text import MESSAGE_TAIL_BYTES, bounded_text, kept_tail_note
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


# A go command can name a thousand folders of the repo, and a folder's name can hold anything.
def _shown_command(command: ChildCommand) -> str:
    return bounded_text(" ".join(command.argv), MESSAGE_TAIL_BYTES)


class ChildTimeoutError(ArdeError):
    def __init__(self, command: ChildCommand, *, output_tail: str) -> None:
        printed = f"\nThe end of what it printed:\n{output_tail}" if output_tail else ""
        super().__init__(
            f"`{_shown_command(command)}` ran longer than {command.timeout_s:g} s and was stopped."
            f"{printed}",
            next_action=(
                f"Run it by hand in {command.cwd} to see where it hangs, fix that, and run again."
            ),
            exit_code=ExitCode.CHECKS_FAILED,
        )


class ChildOutputTooLongError(ArdeError):
    def __init__(self, command: ChildCommand, *, stream: str, size: int) -> None:
        super().__init__(
            f"`{_shown_command(command)}` wrote {size} bytes to {stream}, past the limit of "
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
                f"Check that {command.argv[0]} is installed and on PATH, and that the folder "
                f"{command.cwd} exists, then run again. If the command came from a profile, fix "
                "it there."
            ),
            exit_code=ExitCode.CHECKS_FAILED,
        )


def run_child(command: ChildCommand) -> ChildOutcome:
    with tempfile.TemporaryFile() as stdout_file, tempfile.TemporaryFile() as stderr_file:
        process = _start(command, stdout_file=stdout_file, stderr_file=stderr_file)
        if not _exited_in_time(process, command):
            raise ChildTimeoutError(command, output_tail=_end_of_both(stdout_file, stderr_file))
        # What the child started in its group is not left running after it, such as a server.
        signal_group(process, signal.SIGKILL)
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
        raise ChildStartError(command, describe_os_error(error)) from error


# Names the file the system refused: the program, the folder to run it in, or a log opened for it.
# Without the name, a missing folder would be blamed on the program.
def describe_os_error(error: OSError) -> str:
    reason = error.strerror or str(error)
    return f"{reason}: {error.filename}" if error.filename else reason


def _exited_in_time(process: subprocess.Popen[bytes], command: ChildCommand) -> bool:
    try:
        process.wait(timeout=command.timeout_s)
    except subprocess.TimeoutExpired:
        force_stop(process)
        return False
    except BaseException:
        # Ctrl-C reaches us and not the child, which runs in its own session.
        force_stop(process)
        raise
    return True


# What a hung child printed last is often the one clue to what it was waiting for.
def _end_of_both(stdout_file: IO[bytes], stderr_file: IO[bytes]) -> str:
    output = read_tail(stdout_file, MESSAGE_TAIL_BYTES) + read_tail(stderr_file, MESSAGE_TAIL_BYTES)
    return bounded_text(output.strip(), MESSAGE_TAIL_BYTES)


def read_tail(file: IO[bytes], max_bytes: int) -> str:
    size = file.seek(0, os.SEEK_END)
    file.seek(max(size - max_bytes, 0))
    return file.read().decode("utf-8", errors="replace")


# A grandchild that started its own session is out of reach of the group, and the wait is bounded
# so that nothing it does can hold the caller.
def force_stop(process: subprocess.Popen[bytes]) -> None:
    signal_group(process, signal.SIGKILL)
    with contextlib.suppress(ProcessLookupError, PermissionError):
        process.kill()
    with contextlib.suppress(subprocess.TimeoutExpired):
        process.wait(timeout=WAIT_AFTER_KILL_S)
    # On macOS one kill can miss a child that is being forked at that instant. The leader can fork
    # no more once it is gone, so a second kill finds what the first missed.
    signal_group(process, signal.SIGKILL)


# The child leads its own group, so its process id is the group's.
def signal_group(process: subprocess.Popen[bytes], signal_number: int) -> None:
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(process.pid, signal_number)


def _read_back(file: IO[bytes], command: ChildCommand, *, stream: str) -> tuple[str, bool]:
    limit = command.output
    size = file.seek(0, os.SEEK_END)
    if size <= limit.max_bytes:
        file.seek(0)
        return file.read().decode("utf-8", errors="replace"), False
    if limit.when_longer == "fail":
        raise ChildOutputTooLongError(command, stream=stream, size=size)
    file.seek(size - limit.max_bytes)
    note = kept_tail_note(kept=limit.max_bytes, written=size)
    return note + file.read().decode("utf-8", errors="replace"), True

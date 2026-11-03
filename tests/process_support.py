"""What tests that start real processes share: telling whether one is alive, and cleaning up."""

import contextlib
import os
import signal
import subprocess
import time
from pathlib import Path


# A zombie is dead and only waits to be collected, so it does not count.
def _is_alive(state: str) -> bool:
    return not state.startswith("Z")


# ps exits 1 and prints nothing for a process that does not exist. Anything on stderr means it could
# not look, and that must not read as "gone" in a test that wants a process to have stopped.
def is_running(pid: int) -> bool:
    listing = subprocess.run(
        ["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True, check=False
    )
    assert not listing.stderr, listing.stderr
    state = listing.stdout.strip()
    return bool(state) and _is_alive(state)


def wait_until_gone(pid: int) -> bool:
    deadline = time.monotonic() + 5
    while is_running(pid) and time.monotonic() < deadline:
        time.sleep(0.05)
    return not is_running(pid)


def kill_leftover(folder: Path, name: str = "grandchild.pid") -> None:
    pid_file = folder / name
    if pid_file.exists():
        with contextlib.suppress(ProcessLookupError):
            os.kill(int(pid_file.read_text()), signal.SIGKILL)


def live_group_members(group: int) -> list[int]:
    listing = subprocess.run(
        ["ps", "-A", "-o", "pid=", "-o", "pgid=", "-o", "stat="],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    members: list[int] = []
    for line in listing.splitlines():
        pid, pgid, state = line.split(None, 2)
        if int(pgid) == group and _is_alive(state):
            members.append(int(pid))
    return members


def wait_until_group_is_empty(group: int, timeout_s: float = 3) -> bool:
    deadline = time.monotonic() + timeout_s
    while live_group_members(group) and time.monotonic() < deadline:
        time.sleep(0.02)
    return not live_group_members(group)

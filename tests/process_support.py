"""What tests that start real processes share: telling whether one is alive, and cleaning up."""

import contextlib
import os
import signal
import subprocess
import time
from pathlib import Path


def is_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


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


# A zombie is dead and only waits to be collected, so it does not count.
def live_group_members(group: int) -> list[int]:
    listing = subprocess.run(
        ["ps", "-axo", "pid=,pgid=,stat="], capture_output=True, text=True, check=True
    ).stdout
    members: list[int] = []
    for line in listing.splitlines():
        pid, pgid, state = line.split(None, 2)
        if int(pgid) == group and not state.startswith("Z"):
            members.append(int(pid))
    return members


def wait_until_group_is_empty(group: int, timeout_s: float = 3) -> bool:
    deadline = time.monotonic() + timeout_s
    while live_group_members(group) and time.monotonic() < deadline:
        time.sleep(0.02)
    return not live_group_members(group)

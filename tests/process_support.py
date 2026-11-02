"""What tests that start real processes share: telling whether one is alive, and cleaning up."""

import contextlib
import os
import signal
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

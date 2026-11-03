"""The helpers that other tests trust to say whether a process is alive."""

import os
import subprocess
import sys

from process_support import is_running, wait_until_group_is_empty


def test_a_process_that_runs_is_running() -> None:
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        assert is_running(child.pid)
    finally:
        child.kill()
        child.wait()


def test_a_process_that_was_collected_is_not_running() -> None:
    child = subprocess.Popen([sys.executable, "-c", ""])
    child.wait()

    assert not is_running(child.pid)


# A container with no process reaper keeps an orphan like this for good, so a test that waits for
# a process to go would fail there with nothing wrong.
def test_a_zombie_is_not_running() -> None:
    child = subprocess.Popen([sys.executable, "-c", ""], start_new_session=True)
    try:
        assert wait_until_group_is_empty(child.pid, timeout_s=30)
        os.kill(child.pid, 0)  # Nobody collected it, so it still holds its process id.

        assert not is_running(child.pid)
    finally:
        child.wait()

"""Running a child process: the allow-listed environment, a timeout that holds, bounded output."""

import contextlib
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from arde.adapters import child_process
from arde.adapters.child_process import (
    ChildCommand,
    ChildOutputTooLongError,
    ChildStartError,
    ChildTimeoutError,
    OutputLimit,
    run_child,
)
from arde.domain.errors import ArdeError
from arde.domain.report import ExitCode

ROOMY = OutputLimit(max_bytes=10_000_000, when_longer="keep_tail")


def python_command(
    code: str, *, cwd: Path, timeout_s: float = 20, output: OutputLimit = ROOMY
) -> ChildCommand:
    return ChildCommand(
        argv=(sys.executable, "-c", code), cwd=cwd, timeout_s=timeout_s, output=output
    )


def test_a_child_reports_its_exit_code_and_both_streams(tmp_path: Path) -> None:
    code = "import sys; print('out'); print('err', file=sys.stderr); sys.exit(3)"

    outcome = run_child(python_command(code, cwd=tmp_path))

    assert (outcome.exit_code, outcome.stdout.strip(), outcome.stderr.strip()) == (3, "out", "err")
    assert not outcome.succeeded
    assert not outcome.output_was_cut


def test_a_child_that_exits_zero_succeeded(tmp_path: Path) -> None:
    assert run_child(python_command("pass", cwd=tmp_path)).succeeded


def test_a_child_runs_in_the_folder_it_is_given(tmp_path: Path) -> None:
    outcome = run_child(python_command("import os; print(os.getcwd())", cwd=tmp_path))

    assert Path(outcome.stdout.strip()).resolve() == tmp_path.resolve()


def test_a_child_inherits_no_secret_from_the_parent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "not-a-real-key")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://example.invalid")
    monkeypatch.setenv("GOFLAGS", "-toolexec=/tmp/run-me")
    code = "import json, os; print(json.dumps(sorted(os.environ)))"

    names = json.loads(run_child(python_command(code, cwd=tmp_path)).stdout)

    assert not [name for name in names if name.startswith("ANTHROPIC_")]
    assert "GOFLAGS" not in names
    assert "PATH" in names


def test_a_child_cannot_wait_for_input(tmp_path: Path) -> None:
    outcome = run_child(python_command("import sys; print(repr(sys.stdin.read()))", cwd=tmp_path))

    assert outcome.stdout.strip() == "''"


# Output goes to temporary files and is read back with a limit, so a chatty child costs disk and
# never memory, and no pipe is left for a process that outlives the child to hold open.

PRINTS_FIVE_MEGABYTES = "for number in range(50_000): print(f'line {number:06d} ' + 'x' * 90)"


def test_output_within_the_limit_is_kept_whole(tmp_path: Path) -> None:
    outcome = run_child(python_command("print('a' * 100)", cwd=tmp_path, output=ROOMY))

    assert outcome.stdout == "a" * 100 + "\n"
    assert not outcome.output_was_cut


def test_output_past_a_tail_limit_keeps_the_end_and_says_what_was_cut(tmp_path: Path) -> None:
    limit = OutputLimit(max_bytes=2_000, when_longer="keep_tail")

    outcome = run_child(python_command(PRINTS_FIVE_MEGABYTES, cwd=tmp_path, output=limit))

    assert outcome.output_was_cut
    assert outcome.stdout.startswith("[ARDE kept only the last 2000 bytes of this output")
    assert outcome.stdout.rstrip().endswith("line 049999 " + "x" * 90)
    assert len(outcome.stdout) < 2_000 + 200


def test_each_stream_is_limited_on_its_own(tmp_path: Path) -> None:
    limit = OutputLimit(max_bytes=500, when_longer="keep_tail")
    code = "import sys; print('quiet'); print('e' * 100_000, file=sys.stderr)"

    outcome = run_child(python_command(code, cwd=tmp_path, output=limit))

    assert outcome.stdout == "quiet\n"
    assert outcome.stderr.startswith("[ARDE kept only the last 500 bytes")
    assert outcome.output_was_cut


def test_output_past_a_limit_that_refuses_it_stops_with_the_next_action(tmp_path: Path) -> None:
    limit = OutputLimit(max_bytes=2_000, when_longer="fail")

    with pytest.raises(ChildOutputTooLongError) as refused:
        run_child(python_command(PRINTS_FIVE_MEGABYTES, cwd=tmp_path, output=limit))

    assert isinstance(refused.value, ArdeError)
    assert "2000 bytes" in str(refused.value)
    assert str(tmp_path) in refused.value.next_action


def test_output_exactly_at_the_limit_is_not_cut(tmp_path: Path) -> None:
    limit = OutputLimit(max_bytes=101, when_longer="fail")

    outcome = run_child(python_command("print('a' * 100)", cwd=tmp_path, output=limit))

    assert outcome.stdout == "a" * 100 + "\n"


def test_a_stream_that_is_not_utf8_is_still_read(tmp_path: Path) -> None:
    code = "import sys; sys.stdout.buffer.write(b'ok \\xff\\xfe')"

    assert run_child(python_command(code, cwd=tmp_path)).stdout.startswith("ok ")


def test_a_child_past_its_timeout_is_stopped_and_the_error_names_the_next_action(
    tmp_path: Path,
) -> None:
    command = python_command("import time; time.sleep(60)", cwd=tmp_path, timeout_s=0.5)

    with pytest.raises(ChildTimeoutError) as stopped:
        run_child(command)

    assert isinstance(stopped.value, ArdeError)
    assert stopped.value.exit_code == ExitCode.CHECKS_FAILED
    assert "0.5 s" in str(stopped.value)
    assert str(tmp_path) in stopped.value.next_action


GRANDCHILD_THAT_KEEPS_THE_OUTPUT_OPEN = """
import subprocess, sys, time
grandchild = subprocess.Popen(
    [sys.executable, "-c", "import time; time.sleep(60)"], stdout=sys.stdout, stderr=sys.stderr
)
open("grandchild.pid", "w").write(str(grandchild.pid))
time.sleep(60)
"""

# start_new_session leaves the child's process group, so killing the group cannot reach it.
GRANDCHILD_IN_ITS_OWN_SESSION = """
import subprocess, sys, time
grandchild = subprocess.Popen(
    [sys.executable, "-c", "import time; time.sleep(25)"],
    stdout=sys.stdout, stderr=sys.stderr, start_new_session=True,
)
open("grandchild.pid", "w").write(str(grandchild.pid))
{what_the_child_does_next}
"""


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


def kill_leftover(folder: Path) -> None:
    pid_file = folder / "grandchild.pid"
    if pid_file.exists():
        with contextlib.suppress(ProcessLookupError):
            os.kill(int(pid_file.read_text()), signal.SIGKILL)


def test_a_timeout_also_stops_what_the_child_started(tmp_path: Path) -> None:
    command = python_command(GRANDCHILD_THAT_KEEPS_THE_OUTPUT_OPEN, cwd=tmp_path, timeout_s=1)

    started = time.monotonic()
    with pytest.raises(ChildTimeoutError):
        run_child(command)
    waited = time.monotonic() - started

    assert waited < 15
    assert wait_until_gone(int((tmp_path / "grandchild.pid").read_text()))


def test_a_timeout_returns_on_time_when_a_grandchild_left_the_group_and_kept_the_output(
    tmp_path: Path,
) -> None:
    code = GRANDCHILD_IN_ITS_OWN_SESSION.format(what_the_child_does_next="time.sleep(60)")
    command = python_command(code, cwd=tmp_path, timeout_s=2)

    started = time.monotonic()
    try:
        with pytest.raises(ChildTimeoutError):
            run_child(command)
        waited = time.monotonic() - started
    finally:
        kill_leftover(tmp_path)

    assert waited < 10, "the grandchild lives for 25 s, and the caller must not wait for it"


def test_a_child_that_exits_leaving_a_grandchild_on_its_output_does_not_hold_the_caller(
    tmp_path: Path,
) -> None:
    code = GRANDCHILD_IN_ITS_OWN_SESSION.format(
        what_the_child_does_next="print('child is done', flush=True)"
    )

    started = time.monotonic()
    try:
        outcome = run_child(python_command(code, cwd=tmp_path, timeout_s=60))
        waited = time.monotonic() - started
    finally:
        kill_leftover(tmp_path)

    assert outcome.stdout.strip() == "child is done"
    assert waited < 10, "the grandchild lives for 25 s, and the caller must not wait for it"


def test_a_group_that_cannot_be_signalled_does_not_end_the_stop_in_an_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def refused(group: int, signal_number: int) -> None:
        raise PermissionError(1, "Operation not permitted")

    monkeypatch.setattr(child_process.os, "killpg", refused)
    command = python_command("import time; time.sleep(60)", cwd=tmp_path, timeout_s=0.5)

    started = time.monotonic()
    with pytest.raises(ChildTimeoutError):
        run_child(command)

    assert time.monotonic() - started < 15


def test_the_wait_after_the_kill_is_bounded(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(child_process, "WAIT_AFTER_KILL_S", 0.2)
    monkeypatch.setattr(child_process.os, "killpg", lambda group, signal_number: None)
    monkeypatch.setattr(subprocess.Popen, "kill", lambda self: None)
    command = python_command("import time; time.sleep(5)", cwd=tmp_path, timeout_s=0.3)

    started = time.monotonic()
    with pytest.raises(ChildTimeoutError):
        run_child(command)

    assert time.monotonic() - started < 3


def test_an_interrupted_wait_stops_the_child_before_the_interrupt_goes_on(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    started: list[int] = []
    wait_for_exit = subprocess.Popen.wait

    def interrupted_once(self: subprocess.Popen[bytes], timeout: float | None = None) -> int:
        if started:
            return wait_for_exit(self, timeout)
        started.append(self.pid)
        raise KeyboardInterrupt

    monkeypatch.setattr(subprocess.Popen, "wait", interrupted_once)

    began = time.monotonic()
    with pytest.raises(KeyboardInterrupt):
        run_child(python_command("import time; time.sleep(60)", cwd=tmp_path))

    assert time.monotonic() - began < 15
    assert wait_until_gone(started[0])


def test_a_command_that_cannot_start_names_the_next_action(tmp_path: Path) -> None:
    command = ChildCommand(
        argv=("no-such-program-anywhere",), cwd=tmp_path, timeout_s=5, output=ROOMY
    )

    with pytest.raises(ChildStartError) as refused:
        run_child(command)

    assert "no-such-program-anywhere" in str(refused.value)
    assert "PATH" in refused.value.next_action


def test_a_folder_that_does_not_exist_is_named_and_not_blamed_on_the_program(
    tmp_path: Path,
) -> None:
    missing = tmp_path / "not-there"

    with pytest.raises(ChildStartError) as refused:
        run_child(python_command("pass", cwd=missing))

    assert str(missing) in str(refused.value)


def test_no_temporary_file_is_left_behind(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    monkeypatch.setattr("tempfile.tempdir", str(scratch))

    run_child(python_command("print('x' * 1000)", cwd=tmp_path))
    with pytest.raises(ChildTimeoutError):
        run_child(python_command("import time; time.sleep(60)", cwd=tmp_path, timeout_s=0.3))

    assert list(scratch.iterdir()) == []


# The go env file lives under HOME, which a child may keep, and it can hold GOFLAGS with a
# -toolexec that every build step then runs.
@pytest.mark.integration
def test_a_go_env_file_in_home_does_not_reach_a_child(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    for name in ("XDG_CONFIG_HOME", "GOENV", "GOFLAGS"):
        monkeypatch.delenv(name, raising=False)
    plain_go = {"HOME": str(home), "PATH": os.environ["PATH"]}
    env_file = Path(
        subprocess.run(
            ["go", "env", "GOENV"], env=plain_go, capture_output=True, text=True, check=True
        ).stdout.strip()
    )
    env_file.parent.mkdir(parents=True, exist_ok=True)
    env_file.write_text("GOFLAGS=-mod=readonly\n", encoding="utf-8")

    control = subprocess.run(
        ["go", "env", "GOFLAGS"], env=plain_go, capture_output=True, text=True, check=True
    )
    command = ChildCommand(argv=("go", "env", "GOFLAGS"), cwd=tmp_path, timeout_s=60, output=ROOMY)

    assert control.stdout.strip() == "-mod=readonly", "the control must read the file"
    assert run_child(command).stdout.strip() == ""

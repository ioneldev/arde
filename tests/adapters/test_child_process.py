"""Running a child process: the allow-listed environment, a timeout that holds, bounded output."""

import json
import subprocess
import sys
import time
from pathlib import Path

import pytest
from process_support import (
    kill_leftover,
    live_group_members,
    wait_until_gone,
    wait_until_group_is_empty,
)

from arde.adapters import child_process
from arde.adapters.child_env import child_environment
from arde.adapters.child_process import (
    ChildCommand,
    ChildOutputTooLongError,
    ChildStartError,
    ChildTimeoutError,
    OutputLimit,
    run_child,
)
from arde.adapters.child_text import MESSAGE_TAIL_BYTES
from arde.domain.errors import ArdeError
from arde.domain.report import ExitCode

ROOMY = OutputLimit(max_bytes=10_000_000, when_longer="keep_tail")


def python_command(
    code: str, *, cwd: Path, timeout_s: float = 20, output: OutputLimit = ROOMY
) -> ChildCommand:
    return ChildCommand(
        argv=(sys.executable, "-c", code),
        cwd=cwd,
        timeout_s=timeout_s,
        output=output,
        env=child_environment(),
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


def test_a_child_gets_the_environment_it_is_given_and_nothing_else(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("SOMETHING_ELSE", "x")
    command = python_command("import json, os; print(json.dumps(dict(os.environ)))", cwd=tmp_path)
    command = command.model_copy(update={"env": {"ONLY_THIS": "yes"}})

    seen = json.loads(run_child(command).stdout)

    assert seen.get("ONLY_THIS") == "yes"
    assert "SOMETHING_ELSE" not in seen
    assert "PATH" not in seen


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


# Without a pause the child is inside fork most of the time, and its children end on their own if a
# kill misses them. Ignoring SIGCHLD collects each at once, so none piles up.
FORKS_WITHOUT_A_PAUSE = """
import os, pathlib, signal, time
signal.signal(signal.SIGCHLD, signal.SIG_IGN)
pathlib.Path("leader.pid").write_text(str(os.getpid()))
for _ in range(3000):
    if os.fork() == 0:
        time.sleep(5)
        os._exit(0)
time.sleep(60)
"""
TIMEOUTS_AGAINST_A_FORKING_CHILD = 20
MOST_THAT_MAY_BE_TOO_EARLY = 10


def test_a_timeout_that_lands_while_the_child_is_forking_leaves_nothing(tmp_path: Path) -> None:
    pid_file = tmp_path / "leader.pid"
    too_early = 0

    for attempt in range(TIMEOUTS_AGAINST_A_FORKING_CHILD):
        pid_file.unlink(missing_ok=True)
        with pytest.raises(ChildTimeoutError):
            run_child(python_command(FORKS_WITHOUT_A_PAUSE, cwd=tmp_path, timeout_s=0.3))
        if not pid_file.exists():
            too_early += 1  # a loaded machine had not started the interpreter yet
            continue
        leader = int(pid_file.read_text())
        assert wait_until_group_is_empty(leader), (
            f"timeout {attempt} left {live_group_members(leader)} running"
        )

    assert too_early <= MOST_THAT_MAY_BE_TOO_EARLY, "the machine is too slow for this test"


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
        argv=("no-such-program-anywhere",),
        cwd=tmp_path,
        timeout_s=5,
        output=ROOMY,
        env=child_environment(),
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


def test_a_process_the_child_left_running_in_its_group_is_stopped_when_the_child_exits(
    tmp_path: Path,
) -> None:
    code = (
        "import subprocess, sys\n"
        "left = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
        "open('left.pid', 'w').write(str(left.pid))\n"
    )

    started = time.monotonic()
    try:
        outcome = run_child(python_command(code, cwd=tmp_path))
        waited = time.monotonic() - started
        left = int((tmp_path / "left.pid").read_text())
        gone = wait_until_gone(left)
    finally:
        kill_leftover(tmp_path, "left.pid")

    assert outcome.succeeded
    assert waited < 10
    assert gone, "the extra command left a process behind, as a server would keep its port"


# A go command can name a thousand folders of the repo, and a folder's name can hold anything.
def repo_folders(cwd: Path) -> ChildCommand:
    folders = tuple(f"./e\x1b[2Jvil{number}" for number in range(1000))
    return ChildCommand(
        argv=(sys.executable, "-c", "import time; print('x' * 200); time.sleep(30)", *folders),
        cwd=cwd,
        timeout_s=0.3,
        output=OutputLimit(max_bytes=10, when_longer="fail"),
        env=child_environment(),
    )


def test_a_timeout_message_stays_short_and_clean_whatever_the_command_names(
    tmp_path: Path,
) -> None:
    with pytest.raises(ChildTimeoutError) as stopped:
        run_child(repo_folders(tmp_path))

    assert "\x1b" not in str(stopped.value)
    assert len(str(stopped.value)) < 2 * MESSAGE_TAIL_BYTES


def test_a_too_long_output_message_stays_short_and_clean_whatever_the_command_names(
    tmp_path: Path,
) -> None:
    command = repo_folders(tmp_path).model_copy(
        update={"argv": (sys.executable, "-c", "print('x' * 200)", "./e\x1b[2Jvil" * 1000)}
    )

    with pytest.raises(ChildOutputTooLongError) as refused:
        run_child(command)

    assert "\x1b" not in str(refused.value)
    assert len(str(refused.value)) < 2 * MESSAGE_TAIL_BYTES

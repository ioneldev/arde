"""Running a child process with the allow-listed environment and a timeout that really stops it."""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from arde.adapters.child_process import (
    ChildCommand,
    ChildStartError,
    ChildTimeoutError,
    run_child,
)
from arde.domain.errors import ArdeError
from arde.domain.report import ExitCode


def python_command(code: str, *, cwd: Path, timeout_s: float = 20) -> ChildCommand:
    return ChildCommand(argv=(sys.executable, "-c", code), cwd=cwd, timeout_s=timeout_s)


def test_a_child_reports_its_exit_code_and_both_streams(tmp_path: Path) -> None:
    code = "import sys; print('out'); print('err', file=sys.stderr); sys.exit(3)"

    outcome = run_child(python_command(code, cwd=tmp_path))

    assert (outcome.exit_code, outcome.stdout.strip(), outcome.stderr.strip()) == (3, "out", "err")
    assert not outcome.succeeded


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


GRANDCHILD_THAT_KEEPS_THE_PIPES_OPEN = """
import subprocess, sys, time
grandchild = subprocess.Popen(
    [sys.executable, "-c", "import time; time.sleep(60)"], stdout=sys.stdout, stderr=sys.stderr
)
open("grandchild.pid", "w").write(str(grandchild.pid))
time.sleep(60)
"""


def is_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def test_a_timeout_also_stops_what_the_child_started(tmp_path: Path) -> None:
    command = python_command(GRANDCHILD_THAT_KEEPS_THE_PIPES_OPEN, cwd=tmp_path, timeout_s=1)

    started = time.monotonic()
    with pytest.raises(ChildTimeoutError):
        run_child(command)
    waited = time.monotonic() - started

    assert waited < 15
    grandchild = int((tmp_path / "grandchild.pid").read_text())
    deadline = time.monotonic() + 5
    while is_running(grandchild) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not is_running(grandchild)


def test_an_interrupted_wait_stops_the_child_before_the_interrupt_goes_on(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    started: list[int] = []
    wait_for_output = subprocess.Popen.communicate

    def interrupted_once(self: subprocess.Popen[str], *args: object, **kwargs: object) -> object:
        if started:
            return wait_for_output(self, *args, **kwargs)  # type: ignore[arg-type]
        started.append(self.pid)
        raise KeyboardInterrupt

    monkeypatch.setattr(subprocess.Popen, "communicate", interrupted_once)

    began = time.monotonic()
    with pytest.raises(KeyboardInterrupt):
        run_child(python_command("import time; time.sleep(60)", cwd=tmp_path))

    assert time.monotonic() - began < 15
    assert not is_running(started[0])


def test_a_command_that_cannot_start_names_the_next_action(tmp_path: Path) -> None:
    command = ChildCommand(argv=("no-such-program-anywhere",), cwd=tmp_path, timeout_s=5)

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

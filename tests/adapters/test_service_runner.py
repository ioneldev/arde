"""The service runner against real processes.

Stand-in services, scripts written to a temporary folder, cover the ways a service misbehaves. The
shop fixture service, built by the Go toolchain, covers the way a real one behaves.
"""

import json
import os
import shutil
import signal
import socket
import stat
import subprocess
import sys
import time
import urllib.request
from collections.abc import Iterator
from pathlib import Path

import pytest
from process_support import is_running, kill_leftover, wait_until_gone

from arde.adapters import child_process, service_runner
from arde.adapters.language.go.toolchain import build_service
from arde.adapters.service_runner import local_service_runner
from arde.application.ports import (
    ServiceConfig,
    ServiceRunner,
    ServiceStartError,
    ServiceStopError,
)
from arde.domain.errors import ArdeError
from arde.domain.profile import RepoProfile

pytestmark = pytest.mark.integration

SHOP = Path(__file__).resolve().parents[1] / "fixtures" / "shop"
PORT_ENV = "SERVICE_PORT"
# Kept here because the started fixture swaps subprocess.Popen for a function that records.
REAL_POPEN = subprocess.Popen
# An hour: a stand-in never ends a test by itself.
HOURS = "time.sleep(3600)"


def stand_in(folder: Path, body: str) -> Path:
    script = folder / "stand-in-service"
    script.write_text(
        f"#!{sys.executable}\nimport json, os, signal, socket, subprocess, sys, time\n{body}\n"
    )
    script.chmod(0o755)
    return script


LISTEN = f"""
server = socket.socket()
server.bind(("127.0.0.1", int(os.environ["{PORT_ENV}"])))
server.listen()
"""
IGNORE_SIGTERM = "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
SERVES = LISTEN + HOURS
SERVES_AND_IGNORES_SIGTERM = IGNORE_SIGTERM + LISTEN + HOURS
LISTENS_NEVER = HOURS

# A child says when its handler is in place, because a SIGTERM that arrived earlier would
# settle the test before the stop had any say.
CHILD_THAT_IGNORES_SIGTERM = """
import os, pathlib, signal, time
signal.signal(signal.SIGTERM, signal.SIG_IGN)
pathlib.Path("child.pid").write_text(str(os.getpid()))
time.sleep(3600)
"""
CHILD_THAT_LEAVES_WHEN_ASKED = """
import os, pathlib, signal, time
def leave(number, frame):
    pathlib.Path("child-heard-sigterm.txt").write_text("yes")
    os._exit(0)
signal.signal(signal.SIGTERM, leave)
pathlib.Path("child.pid").write_text(str(os.getpid()))
time.sleep(3600)
"""


def starts_a_child(child_code: str) -> str:
    return (
        f"import pathlib\nchild = subprocess.Popen([sys.executable, '-c', {child_code!r}])\n"
        "pid_file = pathlib.Path('child.pid')\n"
        "while not (pid_file.exists() and pid_file.read_text()):\n"
        "    time.sleep(0.01)\n"
    )


def config_for(binary: Path, folder: Path, **changes: object) -> ServiceConfig:
    fields: dict[str, object] = {
        "binary": binary,
        "cwd": folder,
        "port_env": PORT_ENV,
        "log": folder / "service.log",
        "timeout_s": 30,
    }
    return ServiceConfig.model_validate({**fields, **changes})


def accepts_connection(port: int) -> bool:
    try:
        socket.create_connection(("127.0.0.1", port), timeout=1).close()
    except OSError:
        return False
    return True


def get(url: str) -> str:
    with urllib.request.urlopen(url, timeout=5) as reply:
        return str(reply.read().decode())


@pytest.fixture
def runner() -> ServiceRunner:
    return local_service_runner()


# The handle holds no process id, so the tests learn it from the process the runner started.
@pytest.fixture
def started(monkeypatch: pytest.MonkeyPatch) -> list[subprocess.Popen[bytes]]:
    processes: list[subprocess.Popen[bytes]] = []

    def recording(*args: object, **kwargs: object) -> subprocess.Popen[bytes]:
        process: subprocess.Popen[bytes] = REAL_POPEN(*args, **kwargs)  # type: ignore[call-overload]
        processes.append(process)
        return process

    monkeypatch.setattr(subprocess, "Popen", recording)
    return processes


@pytest.fixture
def short_grace(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(service_runner, "STOP_GRACE_S", 0.5)


@pytest.fixture(scope="module")
def shop_binary(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Path]:
    repo = tmp_path_factory.mktemp("shop") / "shop"
    shutil.copytree(SHOP, repo)
    profile = RepoProfile(main_package=".", main_file="main.go", port_env="SHOP_PORT", patterns=())
    binary = build_service(repo, profile)
    yield binary
    shutil.rmtree(binary.parent, ignore_errors=True)


# The real service


def test_starts_on_free_port_and_answers(
    runner: ServiceRunner, shop_binary: Path, tmp_path: Path
) -> None:
    with runner.running(config_for(shop_binary, tmp_path, port_env="SHOP_PORT")) as service:
        body = get(f"{service.base_url}/")

    assert '"items"' in body
    assert service.base_url == f"http://127.0.0.1:{service.port}"
    assert not accepts_connection(service.port)


def test_two_services_run_side_by_side(
    runner: ServiceRunner, shop_binary: Path, tmp_path: Path
) -> None:
    one = config_for(shop_binary, tmp_path, port_env="SHOP_PORT", log=tmp_path / "one.log")
    two = config_for(shop_binary, tmp_path, port_env="SHOP_PORT", log=tmp_path / "two.log")

    with runner.running(one) as first, runner.running(two) as second:
        bodies = [get(f"{first.base_url}/"), get(f"{second.base_url}/")]

    assert first.port != second.port
    assert all('"items"' in body for body in bodies)
    assert not accepts_connection(first.port)
    assert not accepts_connection(second.port)


# Stopping


def test_sigterm_stops_cleanly(
    monkeypatch: pytest.MonkeyPatch,
    runner: ServiceRunner,
    started: list[subprocess.Popen[bytes]],
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(service_runner, "STOP_GRACE_S", 20)
    body = (
        "def goodbye(number, frame):\n"
        "    open('goodbye.txt', 'w').write('heard SIGTERM')\n"
        "    sys.exit(0)\n"
        "signal.signal(signal.SIGTERM, goodbye)\n" + SERVES
    )

    with runner.running(config_for(stand_in(tmp_path, body), tmp_path)):
        began = time.monotonic()
    waited = time.monotonic() - began

    assert (tmp_path / "goodbye.txt").read_text() == "heard SIGTERM"
    assert started[0].returncode == 0
    assert waited < 20, "a service that exits on SIGTERM must not be made to wait out the grace"


@pytest.mark.usefixtures("short_grace")
def test_force_kills_after_grace(
    runner: ServiceRunner, started: list[subprocess.Popen[bytes]], tmp_path: Path
) -> None:
    binary = stand_in(tmp_path, SERVES_AND_IGNORES_SIGTERM)

    with runner.running(config_for(binary, tmp_path)):
        began = time.monotonic()
    waited = time.monotonic() - began

    assert started[0].returncode == -signal.SIGKILL
    assert not is_running(started[0].pid)
    assert waited < 30


def test_an_interrupt_during_the_grace_still_kills_the_group(
    monkeypatch: pytest.MonkeyPatch,
    runner: ServiceRunner,
    started: list[subprocess.Popen[bytes]],
    tmp_path: Path,
) -> None:
    wait_for_exit = REAL_POPEN.wait
    interrupts: list[int] = []

    def interrupted_once(self: REAL_POPEN[bytes], timeout: float | None = None) -> int:
        if interrupts:
            return wait_for_exit(self, timeout)
        interrupts.append(self.pid)
        raise KeyboardInterrupt

    monkeypatch.setattr(REAL_POPEN, "wait", interrupted_once)
    binary = stand_in(tmp_path, SERVES_AND_IGNORES_SIGTERM)

    with pytest.raises(KeyboardInterrupt), runner.running(config_for(binary, tmp_path)):
        pass

    assert interrupts == [started[0].pid]
    assert started[0].returncode == -signal.SIGKILL


@pytest.mark.usefixtures("short_grace")
@pytest.mark.parametrize(
    "leader",
    [
        pytest.param(SERVES, id="the leader exits on SIGTERM"),
        pytest.param(SERVES_AND_IGNORES_SIGTERM, id="the leader ignores SIGTERM"),
    ],
)
def test_nothing_in_the_process_group_is_left_running(
    runner: ServiceRunner, tmp_path: Path, leader: str
) -> None:
    binary = stand_in(tmp_path, starts_a_child(CHILD_THAT_IGNORES_SIGTERM) + leader)

    try:
        with runner.running(config_for(binary, tmp_path)):
            child = int((tmp_path / "child.pid").read_text())
            assert is_running(child)
        assert wait_until_gone(child)
    finally:
        kill_leftover(tmp_path, "child.pid")


def test_every_process_in_the_group_is_asked_to_stop_before_any_is_killed(
    monkeypatch: pytest.MonkeyPatch, runner: ServiceRunner, tmp_path: Path
) -> None:
    monkeypatch.setattr(service_runner, "STOP_GRACE_S", 30)
    # The leader ignores SIGTERM and ends only when its child has left, so the child's goodbye
    # proves that SIGTERM went to the group and not to the leader alone.
    leader = IGNORE_SIGTERM + starts_a_child(CHILD_THAT_LEAVES_WHEN_ASKED) + LISTEN + "child.wait()"

    try:
        with runner.running(config_for(stand_in(tmp_path, leader), tmp_path)):
            pass
        heard = (tmp_path / "child-heard-sigterm.txt").exists()
    finally:
        kill_leftover(tmp_path, "child.pid")

    assert heard


def test_service_stops_when_body_raises(
    runner: ServiceRunner, started: list[subprocess.Popen[bytes]], tmp_path: Path
) -> None:
    binary = stand_in(tmp_path, SERVES)

    with (
        pytest.raises(ValueError, match="the body failed"),
        runner.running(config_for(binary, tmp_path)),
    ):
        raise ValueError("the body failed")

    assert not is_running(started[0].pid)


def test_keyboard_interrupt_stops_service(
    runner: ServiceRunner, started: list[subprocess.Popen[bytes]], tmp_path: Path
) -> None:
    binary = stand_in(tmp_path, SERVES)

    with pytest.raises(KeyboardInterrupt), runner.running(config_for(binary, tmp_path)):
        raise KeyboardInterrupt

    assert not is_running(started[0].pid)


def test_keyboard_interrupt_while_waiting_for_the_port_stops_the_service(
    monkeypatch: pytest.MonkeyPatch,
    runner: ServiceRunner,
    started: list[subprocess.Popen[bytes]],
    tmp_path: Path,
) -> None:
    def interrupted(*args: object, **kwargs: object) -> socket.socket:
        raise KeyboardInterrupt

    monkeypatch.setattr(socket, "create_connection", interrupted)

    with (
        pytest.raises(KeyboardInterrupt),
        runner.running(config_for(stand_in(tmp_path, SERVES), tmp_path)),
    ):
        pytest.fail("the body must not run before the port is open")

    assert not is_running(started[0].pid)


# A stop that fails is said aloud, and never replaces the error that came first.


@pytest.fixture
def a_stop_that_cannot_kill(
    monkeypatch: pytest.MonkeyPatch, started: list[subprocess.Popen[bytes]]
) -> Iterator[None]:
    monkeypatch.setattr(service_runner, "STOP_GRACE_S", 0.2)
    monkeypatch.setattr(child_process, "WAIT_AFTER_KILL_S", 0.2)
    monkeypatch.setattr(os, "killpg", lambda group, signal_number: None)
    monkeypatch.setattr(REAL_POPEN, "kill", lambda self: None)
    yield
    for process in started:
        if process.poll() is None:
            os.kill(process.pid, signal.SIGKILL)
            process.wait(timeout=10)


def test_a_service_that_cannot_be_stopped_is_reported_and_names_how_to_stop_it(
    runner: ServiceRunner,
    a_stop_that_cannot_kill: None,
    started: list[subprocess.Popen[bytes]],
    tmp_path: Path,
) -> None:
    binary = stand_in(tmp_path, SERVES)

    with pytest.raises(ServiceStopError) as refused, runner.running(config_for(binary, tmp_path)):
        pass

    assert isinstance(refused.value, ArdeError)
    assert str(started[0].pid) in str(refused.value)
    assert f"kill -9 -{started[0].pid}" in refused.value.next_action


def test_a_failed_stop_does_not_hide_the_error_that_caused_it(
    runner: ServiceRunner, a_stop_that_cannot_kill: None, tmp_path: Path
) -> None:
    binary = stand_in(tmp_path, SERVES)

    with (
        pytest.raises(ValueError, match="the body failed") as raised,
        runner.running(config_for(binary, tmp_path)),
    ):
        raise ValueError("the body failed")

    assert any("did not stop" in note for note in raised.value.__notes__)


def test_a_failed_stop_is_raised_after_a_good_body_even_inside_an_except_block(
    runner: ServiceRunner, a_stop_that_cannot_kill: None, tmp_path: Path
) -> None:
    binary = stand_in(tmp_path, SERVES)

    try:
        raise ValueError("an unrelated error being handled")
    except ValueError:
        with pytest.raises(ServiceStopError), runner.running(config_for(binary, tmp_path)):
            pass


# Starting


def test_start_timeout_raises_with_next_action(
    runner: ServiceRunner, started: list[subprocess.Popen[bytes]], tmp_path: Path
) -> None:
    config = config_for(stand_in(tmp_path, LISTENS_NEVER), tmp_path, timeout_s=0.5)

    began = time.monotonic()
    with pytest.raises(ServiceStartError) as failed, runner.running(config):
        pytest.fail("the body must not run when the port never opens")
    waited = time.monotonic() - began

    assert isinstance(failed.value, ArdeError)
    assert waited >= 0.5
    assert PORT_ENV in str(failed.value)
    assert str(config.log) in str(failed.value)
    assert PORT_ENV in failed.value.next_action
    assert str(config.log) in failed.value.next_action
    assert not is_running(started[0].pid)


def test_a_service_that_exits_before_opening_its_port_fails_at_once_with_its_log(
    runner: ServiceRunner, tmp_path: Path
) -> None:
    binary = stand_in(
        tmp_path, "print('cannot open the database', file=sys.stderr, flush=True)\nsys.exit(3)"
    )
    config = config_for(binary, tmp_path, timeout_s=120)

    began = time.monotonic()
    with pytest.raises(ServiceStartError) as failed, runner.running(config):
        pytest.fail("the body must not run for a service that is gone")
    waited = time.monotonic() - began

    assert waited < 100, "the runner must not wait out a timeout for a process that has exited"
    assert "exited with code 3" in str(failed.value)
    assert "cannot open the database" in str(failed.value)
    assert PORT_ENV in str(failed.value)
    assert str(config.log) in failed.value.next_action


def test_only_the_end_of_a_long_log_goes_into_the_error(
    runner: ServiceRunner, tmp_path: Path
) -> None:
    binary = stand_in(
        tmp_path, "for number in range(5000): print(f'line {number:04d}', flush=True)\nsys.exit(1)"
    )

    with pytest.raises(ServiceStartError) as failed, runner.running(config_for(binary, tmp_path)):
        pass

    assert "line 4999" in str(failed.value)
    assert "line 0000" not in str(failed.value)
    assert len(str(failed.value)) < 5000


def test_a_binary_that_cannot_be_started_is_named(runner: ServiceRunner, tmp_path: Path) -> None:
    missing = tmp_path / "no-such-binary"

    with pytest.raises(ServiceStartError) as failed, runner.running(config_for(missing, tmp_path)):
        pass

    assert str(missing) in str(failed.value)
    assert failed.value.next_action


def test_a_folder_that_does_not_exist_is_named_and_not_blamed_on_the_binary(
    runner: ServiceRunner, tmp_path: Path
) -> None:
    binary = stand_in(tmp_path, SERVES)
    missing = tmp_path / "not-there"

    with (
        pytest.raises(ServiceStartError) as failed,
        runner.running(config_for(binary, tmp_path, cwd=missing)),
    ):
        pass

    assert str(missing) in str(failed.value)


def test_a_log_that_cannot_be_created_is_named_and_no_service_is_started(
    runner: ServiceRunner, started: list[subprocess.Popen[bytes]], tmp_path: Path
) -> None:
    log = tmp_path / "no-such-folder" / "service.log"

    with (
        pytest.raises(ServiceStartError) as failed,
        runner.running(config_for(stand_in(tmp_path, SERVES), tmp_path, log=log)),
    ):
        pass

    assert str(log) in str(failed.value)
    assert started == []


def test_the_service_runs_in_the_folder_it_is_given(runner: ServiceRunner, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    binary = stand_in(tmp_path, "print(os.getcwd(), flush=True)\n" + SERVES)
    config = config_for(binary, tmp_path, cwd=repo)

    with runner.running(config):
        pass

    assert Path(config.log.read_text().strip()).resolve() == repo.resolve()


def test_a_relative_binary_is_found_whatever_the_folder_the_service_runs_in(
    monkeypatch: pytest.MonkeyPatch, runner: ServiceRunner, tmp_path: Path
) -> None:
    stand_in(tmp_path, SERVES)
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.chdir(tmp_path)

    with runner.running(config_for(Path("stand-in-service"), tmp_path, cwd=repo)) as service:
        assert accepts_connection(service.port)


# What the service writes, and what it sees


def test_output_goes_to_log(runner: ServiceRunner, tmp_path: Path) -> None:
    body = (
        "print('said on stdout', flush=True)\n"
        "print('said on stderr', file=sys.stderr, flush=True)\n" + SERVES
    )
    config = config_for(stand_in(tmp_path, body), tmp_path)

    with runner.running(config):
        pass

    assert "said on stdout" in config.log.read_text()
    assert "said on stderr" in config.log.read_text()
    assert stat.S_IMODE(config.log.stat().st_mode) == 0o600


def test_an_old_log_is_replaced_and_made_private(runner: ServiceRunner, tmp_path: Path) -> None:
    config = config_for(stand_in(tmp_path, SERVES), tmp_path)
    config.log.write_text("left from an earlier run\n")
    config.log.chmod(0o644)

    with runner.running(config):
        pass

    assert "left from an earlier run" not in config.log.read_text()
    assert stat.S_IMODE(config.log.stat().st_mode) == 0o600


def test_child_env_has_no_anthropic_vars(
    monkeypatch: pytest.MonkeyPatch, runner: ServiceRunner, tmp_path: Path
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "not-a-real-key")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://example.invalid")
    monkeypatch.setenv("GOFLAGS", "-toolexec=/tmp/run-me")
    monkeypatch.setenv("SOMETHING_ELSE", "x")
    binary = stand_in(tmp_path, "print(json.dumps(sorted(os.environ)), flush=True)\n" + SERVES)
    config = config_for(binary, tmp_path)

    with runner.running(config):
        seen = json.loads(config.log.read_text())

    assert not [name for name in seen if name.startswith("ANTHROPIC_")]
    assert "GOFLAGS" not in seen
    assert "SOMETHING_ELSE" not in seen
    assert PORT_ENV in seen
    assert "PATH" in seen

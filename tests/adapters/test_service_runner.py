"""The service runner against real processes.

Stand-in services, scripts written to a temporary folder, cover the ways a service misbehaves. They
need no Go and run with the fast tests. The shop fixture service, built by the Go toolchain, covers
the way a real one behaves, and its two tests are the ones marked integration.
"""

import contextlib
import errno
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
from process_support import (
    is_running,
    kill_leftover,
    live_group_members,
    wait_until_gone,
    wait_until_group_is_empty,
)
from shop_support import SHOP, shop_profile

from arde.adapters import child_process, service_runner
from arde.adapters.child_env import ALLOWED_VARIABLES
from arde.adapters.language.go.toolchain import built_service
from arde.adapters.service_runner import local_service_runner
from arde.application.ports import (
    ServiceConfig,
    ServiceDiedError,
    ServiceRunner,
    ServiceStartError,
    ServiceStopError,
)
from arde.domain.errors import ArdeError

PORT_ENV = "SERVICE_PORT"
# Kept here because the started fixture swaps subprocess.Popen for a function that records.
REAL_POPEN = subprocess.Popen
# Kept for the same reason: a test that makes os.kill refuse must still clean up after itself.
REAL_KILL = os.kill
REAL_KILLPG = os.killpg
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
CHILD_THAT_HOLDS_THE_PORT = f"""
import os, pathlib, socket, time
server = socket.socket()
server.bind(("127.0.0.1", int(os.environ["{PORT_ENV}"])))
server.listen()
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


# The port is taken by another process, so this service cannot listen. It stays up until the test
# says go, and then it exits the way a service does when its port is in use.
EXITS_WHEN_ITS_PORT_IS_TAKEN = f"""
try:
    server = socket.socket()
    server.bind(("127.0.0.1", int(os.environ["{PORT_ENV}"])))
except OSError as error:
    print(f"cannot listen: {{error}}", file=sys.stderr, flush=True)
while not pathlib.Path("go.txt").exists():
    time.sleep(0.01)
sys.exit(1)
"""
# Without a pause the leader is inside fork most of the time, and the children end on their own
# if a stop misses them. A child that ignores SIGCHLD is collected at once, so none piles up.
FORKS_WITHOUT_A_PAUSE = (
    "signal.signal(signal.SIGCHLD, signal.SIG_IGN)\n"
    "open('leader.pid', 'w').write(str(os.getpid()))\n"
    + IGNORE_SIGTERM
    + LISTEN
    + """
for _ in range(3000):
    if os.fork() == 0:
        time.sleep(5)
        os._exit(0)
time.sleep(3600)
"""
)
FORK_RACE_STOPS = 20


def starts_a_child(child_code: str, *, own_session: bool = False) -> str:
    return (
        "import pathlib\n"
        f"child = subprocess.Popen([sys.executable, '-c', {child_code!r}],"
        f" start_new_session={own_session})\n"
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
def started(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[subprocess.Popen[bytes]]]:
    processes: list[subprocess.Popen[bytes]] = []

    def recording(*args: object, **kwargs: object) -> subprocess.Popen[bytes]:
        process: subprocess.Popen[bytes] = REAL_POPEN(*args, **kwargs)  # type: ignore[call-overload]
        processes.append(process)
        return process

    monkeypatch.setattr(subprocess, "Popen", recording)
    yield processes
    for process in processes:
        if process.poll() is None:
            with contextlib.suppress(ProcessLookupError):
                REAL_KILLPG(process.pid, signal.SIGKILL)
            process.wait(timeout=10)


@pytest.fixture
def short_grace(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(service_runner, "STOP_GRACE_S", 0.5)


@pytest.fixture(scope="module")
def shop_binary(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Path]:
    repo = tmp_path_factory.mktemp("shop") / "shop"
    shutil.copytree(SHOP, repo)
    with built_service(repo, shop_profile()) as binary:
        yield binary


# The real service


@pytest.mark.integration
def test_starts_on_free_port_and_answers(
    runner: ServiceRunner, shop_binary: Path, tmp_path: Path
) -> None:
    with runner.running(config_for(shop_binary, tmp_path, port_env="SHOP_PORT")) as service:
        body = get(f"{service.base_url}/")

    assert '"items"' in body
    assert service.base_url == f"http://127.0.0.1:{service.port}"
    assert not accepts_connection(service.port)


@pytest.mark.integration
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
    real_connect = socket.create_connection
    probes: list[int] = []

    def interrupted_once(*args: object, **kwargs: object) -> socket.socket:
        if probes:
            return real_connect(*args, **kwargs)  # type: ignore[call-overload, no-any-return]
        probes.append(1)
        raise KeyboardInterrupt

    monkeypatch.setattr(socket, "create_connection", interrupted_once)

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
) -> None:
    monkeypatch.setattr(service_runner, "STOP_GRACE_S", 0.2)
    monkeypatch.setattr(child_process, "WAIT_AFTER_KILL_S", 0.2)
    monkeypatch.setattr(os, "killpg", lambda group, signal_number: None)
    monkeypatch.setattr(REAL_POPEN, "kill", lambda self: None)


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
    assert not getattr(refused.value, "__notes__", [])


def test_a_failed_stop_does_not_hide_the_error_that_caused_it(
    runner: ServiceRunner,
    a_stop_that_cannot_kill: None,
    started: list[subprocess.Popen[bytes]],
    tmp_path: Path,
) -> None:
    binary = stand_in(tmp_path, SERVES)

    with (
        pytest.raises(ValueError, match="the body failed") as raised,
        runner.running(config_for(binary, tmp_path)),
    ):
        raise ValueError("the body failed")

    assert any("did not stop" in note for note in raised.value.__notes__)
    assert any(f"kill -9 -{started[0].pid}" in note for note in raised.value.__notes__)


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
    assert "127.0.0.1" in str(failed.value)
    assert "127.0.0.1" in failed.value.next_action
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
    assert not getattr(failed.value, "__notes__", []), "one failure must not be reported twice"
    assert "cannot open the database" in str(failed.value)
    assert PORT_ENV in str(failed.value)
    assert str(config.log) in failed.value.next_action


def test_a_control_character_in_the_log_is_shown_and_never_passed_on(
    runner: ServiceRunner, tmp_path: Path
) -> None:
    binary = stand_in(
        tmp_path, "print('\\x1b]0;OWNED\\x07\\x1b[2Jstarting', flush=True)\nsys.exit(3)"
    )

    with pytest.raises(ServiceStartError) as failed, runner.running(config_for(binary, tmp_path)):
        pass

    assert "\x1b" not in str(failed.value)
    assert "\x07" not in str(failed.value)
    assert "starting" in str(failed.value)


def test_the_tail_of_a_log_stays_short_when_control_characters_grow_into_escapes(
    runner: ServiceRunner, tmp_path: Path
) -> None:
    binary = stand_in(tmp_path, "print('\\x1b' * 5000, flush=True)\nsys.exit(3)")

    with pytest.raises(ServiceStartError) as failed, runner.running(config_for(binary, tmp_path)):
        pass

    assert len(str(failed.value)) < 3000


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


# A service that dies on its own while the body is using it


@pytest.fixture
def quick_port_release(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(service_runner, "PORT_RELEASE_WAIT_S", 0.2)


def test_a_service_that_dies_after_opening_its_port_is_reported(
    runner: ServiceRunner, started: list[subprocess.Popen[bytes]], tmp_path: Path
) -> None:
    body = (
        LISTEN
        + "time.sleep(0.25)\n"
        + "print('fatal: lost the database', file=sys.stderr, flush=True)\n"
        + "sys.exit(5)"
    )
    config = config_for(stand_in(tmp_path, body), tmp_path)

    with pytest.raises(ServiceDiedError) as died, runner.running(config) as service:
        started[0].wait(timeout=30)

    assert isinstance(died.value, ArdeError)
    assert "exited with code 5" in str(died.value)
    assert "fatal: lost the database" in str(died.value)
    assert not getattr(died.value, "__notes__", [])
    assert str(config.log) in died.value.next_action
    assert not accepts_connection(service.port)


def test_a_service_that_dies_leaving_a_child_is_reported_and_the_child_is_stopped(
    runner: ServiceRunner, started: list[subprocess.Popen[bytes]], tmp_path: Path
) -> None:
    body = starts_a_child(CHILD_THAT_IGNORES_SIGTERM) + LISTEN + "time.sleep(0.25)\nsys.exit(9)"
    config = config_for(stand_in(tmp_path, body), tmp_path)

    try:
        with pytest.raises(ServiceDiedError) as died, runner.running(config):
            started[0].wait(timeout=30)
        child_is_gone = wait_until_gone(int((tmp_path / "child.pid").read_text()))
    finally:
        kill_leftover(tmp_path, "child.pid")

    assert "exited with code 9" in str(died.value)
    assert child_is_gone


@pytest.mark.usefixtures("quick_port_release")
def test_a_service_that_lost_its_port_to_another_process_is_reported(
    runner: ServiceRunner, started: list[subprocess.Popen[bytes]], tmp_path: Path
) -> None:
    other_process = starts_a_child(CHILD_THAT_HOLDS_THE_PORT, own_session=True)
    config = config_for(stand_in(tmp_path, other_process + EXITS_WHEN_ITS_PORT_IS_TAKEN), tmp_path)

    try:
        with pytest.raises(ServiceDiedError) as died, runner.running(config):
            (tmp_path / "go.txt").write_text("go")
            started[0].wait(timeout=30)
    finally:
        kill_leftover(tmp_path, "child.pid")

    assert "exited with code 1" in str(died.value)
    assert "Address already in use" in str(died.value)
    assert any("still accepts connections" in note for note in died.value.__notes__)


def test_a_dead_service_does_not_hide_the_error_of_the_body(
    runner: ServiceRunner, started: list[subprocess.Popen[bytes]], tmp_path: Path
) -> None:
    config = config_for(stand_in(tmp_path, LISTEN + "time.sleep(0.25)\nsys.exit(5)"), tmp_path)

    with pytest.raises(ValueError, match="the body failed") as raised, runner.running(config):
        started[0].wait(timeout=30)
        raise ValueError("the body failed")

    assert any("exited with code 5" in note for note in raised.value.__notes__)


# After the stop, the port is checked once more, and a process outside the group is found there


@pytest.mark.usefixtures("quick_port_release")
def test_a_daemon_that_outlives_the_stop_is_reported_with_its_port(
    runner: ServiceRunner, tmp_path: Path
) -> None:
    body = starts_a_child(CHILD_THAT_HOLDS_THE_PORT, own_session=True) + HOURS
    config = config_for(stand_in(tmp_path, body), tmp_path)

    try:
        with pytest.raises(ServiceStopError) as refused, runner.running(config) as service:
            assert accepts_connection(service.port)
    finally:
        kill_leftover(tmp_path, "child.pid")

    assert str(service.port) in str(refused.value)
    assert "lsof" in refused.value.next_action


# The first probe comes before the holder of the port is up and is refused, as it would be in life.
def refuse_the_first_probe(
    monkeypatch: pytest.MonkeyPatch, started: list[subprocess.Popen[bytes]]
) -> None:
    real_connect = socket.create_connection
    probes: list[int] = []

    def refused_at_first(*args: object, **kwargs: object) -> socket.socket:
        if not probes:
            probes.append(1)
            started[0].wait(timeout=30)
            raise ConnectionRefusedError
        return real_connect(*args, **kwargs)  # type: ignore[call-overload, no-any-return]

    monkeypatch.setattr(socket, "create_connection", refused_at_first)


@pytest.mark.usefixtures("quick_port_release")
def test_a_service_that_exits_leaving_a_daemon_on_its_port_does_not_claim_the_port_never_opened(
    monkeypatch: pytest.MonkeyPatch,
    runner: ServiceRunner,
    started: list[subprocess.Popen[bytes]],
    tmp_path: Path,
) -> None:
    refuse_the_first_probe(monkeypatch, started)
    body = starts_a_child(CHILD_THAT_HOLDS_THE_PORT, own_session=True) + "sys.exit(0)"
    config = config_for(stand_in(tmp_path, body), tmp_path)

    try:
        with pytest.raises(ServiceStartError) as failed, runner.running(config):
            pytest.fail("the body must not run for a service that is gone")
    finally:
        kill_leftover(tmp_path, "child.pid")

    assert "exited with code 0" in str(failed.value)
    assert "still accepts connections" in str(failed.value)
    assert "before it opened" not in str(failed.value)
    assert any("lsof" in note for note in failed.value.__notes__)


# ARDE sees that something holds the port and not who started it, so the message says no more.
@pytest.mark.usefixtures("quick_port_release")
def test_a_child_in_the_services_own_group_is_not_said_to_be_outside_it(
    monkeypatch: pytest.MonkeyPatch,
    runner: ServiceRunner,
    started: list[subprocess.Popen[bytes]],
    tmp_path: Path,
) -> None:
    refuse_the_first_probe(monkeypatch, started)
    body = starts_a_child(CHILD_THAT_HOLDS_THE_PORT) + "sys.exit(0)"
    config = config_for(stand_in(tmp_path, body), tmp_path)

    with pytest.raises(ServiceStartError) as failed, runner.running(config):
        pytest.fail("the body must not run for a service that is gone")

    assert "still accepts connections" in str(failed.value)
    assert "Something is holding port" in str(failed.value)
    assert "outside its process group" not in str(failed.value)
    assert wait_until_gone(int((tmp_path / "child.pid").read_text()))


@pytest.mark.usefixtures("quick_port_release")
def test_a_port_another_process_took_is_not_said_to_be_held_by_the_service(
    runner: ServiceRunner, started: list[subprocess.Popen[bytes]], tmp_path: Path
) -> None:
    other_process = starts_a_child(CHILD_THAT_HOLDS_THE_PORT, own_session=True)
    config = config_for(stand_in(tmp_path, other_process + EXITS_WHEN_ITS_PORT_IS_TAKEN), tmp_path)

    try:
        with pytest.raises(ServiceDiedError) as died, runner.running(config):
            (tmp_path / "go.txt").write_text("go")
            started[0].wait(timeout=30)
    finally:
        kill_leftover(tmp_path, "child.pid")

    assert any("something is holding it" in note for note in died.value.__notes__)
    assert not any("it started" in note for note in died.value.__notes__)


# Only a refusal or a timeout means nothing listens. Any other failure means no test took place.


def file_table_is_full(*args: object, **kwargs: object) -> socket.socket:
    raise OSError(errno.EMFILE, "Too many open files")


def test_a_port_that_cannot_be_tested_while_waiting_stops_with_the_reason_and_a_next_action(
    monkeypatch: pytest.MonkeyPatch, runner: ServiceRunner, tmp_path: Path
) -> None:
    monkeypatch.setattr(socket, "create_connection", file_table_is_full)
    config = config_for(stand_in(tmp_path, SERVES), tmp_path, timeout_s=1)

    with pytest.raises(ServiceStartError) as stopped, runner.running(config):
        pytest.fail("the body must not run on a port that was never tested")

    assert "Too many open files" in str(stopped.value)
    assert "did not accept" not in str(stopped.value)
    assert stopped.value.next_action


def test_a_service_that_exited_keeps_its_own_message_when_its_port_cannot_be_tested(
    monkeypatch: pytest.MonkeyPatch,
    runner: ServiceRunner,
    started: list[subprocess.Popen[bytes]],
    tmp_path: Path,
) -> None:
    def fails_once_it_has_gone(*args: object, **kwargs: object) -> socket.socket:
        if started and started[0].poll() is not None:
            raise OSError(errno.EMFILE, "Too many open files")
        raise ConnectionRefusedError

    monkeypatch.setattr(socket, "create_connection", fails_once_it_has_gone)
    config = config_for(stand_in(tmp_path, "sys.exit(3)"), tmp_path)

    with pytest.raises(ServiceStartError) as stopped, runner.running(config):
        pytest.fail("the body must not run for a service that is gone")

    assert "exited with code 3" in str(stopped.value)


def test_a_port_that_cannot_be_tested_after_the_stop_does_not_pass_as_released(
    monkeypatch: pytest.MonkeyPatch, runner: ServiceRunner, tmp_path: Path
) -> None:
    config = config_for(stand_in(tmp_path, SERVES), tmp_path)

    with pytest.raises(ServiceStopError) as stopped, runner.running(config):
        monkeypatch.setattr(socket, "create_connection", file_table_is_full)

    assert "Too many open files" in str(stopped.value)
    assert stopped.value.next_action


NO_CONNECTION = [
    ConnectionRefusedError,
    TimeoutError,
    ConnectionResetError,
    ConnectionAbortedError,
]


@pytest.mark.parametrize("failure", NO_CONNECTION)
def test_a_port_that_gives_no_connection_reads_as_not_listening_and_the_wait_goes_on(
    monkeypatch: pytest.MonkeyPatch,
    runner: ServiceRunner,
    tmp_path: Path,
    failure: type[OSError],
) -> None:
    real_connect = socket.create_connection
    probes: list[int] = []

    def fails_at_first(*args: object, **kwargs: object) -> socket.socket:
        if not probes:
            probes.append(1)
            raise failure
        return real_connect(*args, **kwargs)  # type: ignore[call-overload, no-any-return]

    monkeypatch.setattr(socket, "create_connection", fails_at_first)

    with runner.running(config_for(stand_in(tmp_path, SERVES), tmp_path)) as service:
        assert accepts_connection(service.port)


# A listener that dies during the handshake resets the connection, which is a stop going well.
@pytest.mark.parametrize("refusal", NO_CONNECTION)
def test_a_port_that_gives_no_connection_after_the_stop_is_released(
    monkeypatch: pytest.MonkeyPatch,
    runner: ServiceRunner,
    tmp_path: Path,
    refusal: type[OSError],
) -> None:
    def refuses(*args: object, **kwargs: object) -> socket.socket:
        raise refusal

    config = config_for(stand_in(tmp_path, SERVES), tmp_path)

    with runner.running(config):
        monkeypatch.setattr(socket, "create_connection", refuses)


# The process group is killed twice, because one kill can miss a fork in flight


def test_a_stop_that_lands_while_the_service_is_forking_leaves_nothing(
    monkeypatch: pytest.MonkeyPatch, runner: ServiceRunner, tmp_path: Path
) -> None:
    monkeypatch.setattr(service_runner, "STOP_GRACE_S", 0.05)
    config = config_for(stand_in(tmp_path, FORKS_WITHOUT_A_PAUSE), tmp_path)

    for attempt in range(FORK_RACE_STOPS):
        with runner.running(config):
            time.sleep(0.005 * (attempt % 6))
        leader = int((tmp_path / "leader.pid").read_text())
        assert wait_until_group_is_empty(leader), (
            f"stop {attempt} left {live_group_members(leader)} running"
        )


def test_a_stop_that_is_not_permitted_to_signal_ends_in_a_stop_error(
    monkeypatch: pytest.MonkeyPatch,
    runner: ServiceRunner,
    started: list[subprocess.Popen[bytes]],
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(service_runner, "STOP_GRACE_S", 0.2)
    monkeypatch.setattr(child_process, "WAIT_AFTER_KILL_S", 0.2)

    def refused(*args: object) -> None:
        raise PermissionError(1, "Operation not permitted")

    monkeypatch.setattr(os, "killpg", refused)
    monkeypatch.setattr(os, "kill", refused)

    with (
        pytest.raises(ServiceStopError),
        runner.running(config_for(stand_in(tmp_path, SERVES), tmp_path)),
    ):
        pass


def test_an_interrupt_at_the_first_signal_still_kills_the_group(
    monkeypatch: pytest.MonkeyPatch,
    runner: ServiceRunner,
    started: list[subprocess.Popen[bytes]],
    tmp_path: Path,
) -> None:
    def interrupted(process: REAL_POPEN[bytes], signal_number: int) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(service_runner, "signal_group", interrupted)
    binary = stand_in(tmp_path, SERVES_AND_IGNORES_SIGTERM)

    with pytest.raises(KeyboardInterrupt), runner.running(config_for(binary, tmp_path)):
        pass

    assert started[0].returncode == -signal.SIGKILL


def test_the_grace_is_five_seconds() -> None:
    assert service_runner.STOP_GRACE_S == 5


# What the log may be


def assert_the_log_is_refused(
    runner: ServiceRunner, config: ServiceConfig, *, started: list[subprocess.Popen[bytes]]
) -> None:
    with pytest.raises(ServiceStartError) as failed, runner.running(config):
        pytest.fail("the body must not run when the log cannot be used")

    assert str(config.log) in str(failed.value)
    assert failed.value.next_action
    assert started == []


def test_a_symlink_at_the_log_path_is_refused_and_its_target_left_alone(
    runner: ServiceRunner, started: list[subprocess.Popen[bytes]], tmp_path: Path
) -> None:
    precious = tmp_path / "precious.txt"
    precious.write_text("keep me\n")
    precious.chmod(0o644)
    config = config_for(stand_in(tmp_path, SERVES), tmp_path)
    config.log.symlink_to(precious)

    assert_the_log_is_refused(runner, config, started=started)

    assert precious.read_text() == "keep me\n"
    assert stat.S_IMODE(precious.stat().st_mode) == 0o644


def test_a_dangling_symlink_at_the_log_path_is_refused_and_creates_nothing(
    runner: ServiceRunner, started: list[subprocess.Popen[bytes]], tmp_path: Path
) -> None:
    target = tmp_path / "not-created.txt"
    config = config_for(stand_in(tmp_path, SERVES), tmp_path)
    config.log.symlink_to(target)

    assert_the_log_is_refused(runner, config, started=started)

    assert not target.exists()


def test_a_fifo_at_the_log_path_is_refused_and_does_not_hang(
    runner: ServiceRunner, started: list[subprocess.Popen[bytes]], tmp_path: Path
) -> None:
    config = config_for(stand_in(tmp_path, SERVES), tmp_path)
    os.mkfifo(config.log)

    assert_the_log_is_refused(runner, config, started=started)


def test_a_fifo_with_a_reader_at_the_log_path_is_refused(
    runner: ServiceRunner, started: list[subprocess.Popen[bytes]], tmp_path: Path
) -> None:
    config = config_for(stand_in(tmp_path, SERVES), tmp_path)
    os.mkfifo(config.log)
    reader = os.open(config.log, os.O_RDONLY | os.O_NONBLOCK)
    try:
        assert_the_log_is_refused(runner, config, started=started)
    finally:
        os.close(reader)


def test_a_folder_at_the_log_path_is_refused(
    runner: ServiceRunner, started: list[subprocess.Popen[bytes]], tmp_path: Path
) -> None:
    config = config_for(stand_in(tmp_path, SERVES), tmp_path)
    config.log.mkdir()

    assert_the_log_is_refused(runner, config, started=started)


def test_a_log_the_service_deletes_cannot_replace_the_error_about_the_service(
    runner: ServiceRunner, tmp_path: Path
) -> None:
    log = tmp_path / "service.log"
    binary = stand_in(tmp_path, f"os.unlink({str(log)!r})\nsys.exit(4)")
    config = config_for(binary, tmp_path, log=log)

    with pytest.raises(ServiceStartError) as failed, runner.running(config):
        pass

    assert "exited with code 4" in str(failed.value)
    assert "could not be read" in str(failed.value)
    assert str(config.log) in failed.value.next_action


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads any file")
def test_a_log_the_service_makes_unreadable_cannot_replace_the_error_about_the_service(
    runner: ServiceRunner, tmp_path: Path
) -> None:
    log = tmp_path / "service.log"
    binary = stand_in(tmp_path, f"os.chmod({str(log)!r}, 0)\nsys.exit(4)")
    config = config_for(binary, tmp_path, log=log)

    with pytest.raises(ServiceStartError) as failed, runner.running(config):
        pass

    assert "exited with code 4" in str(failed.value)
    assert "could not be read" in str(failed.value)


# What the runner promises about the child's side of the process


@pytest.mark.parametrize("name", ALLOWED_VARIABLES)
def test_a_port_variable_the_service_needs_as_it_is_is_refused(
    runner: ServiceRunner,
    started: list[subprocess.Popen[bytes]],
    tmp_path: Path,
    name: str,
) -> None:
    config = config_for(stand_in(tmp_path, SERVES), tmp_path, port_env=name)

    with pytest.raises(ServiceStartError) as failed, runner.running(config):
        pytest.fail("the body must not run with a port variable that would break the service")

    assert name in str(failed.value)
    assert failed.value.next_action
    assert started == []


@pytest.fixture
def a_stdin_that_is_not_devnull() -> Iterator[None]:
    saved = os.dup(0)
    read_end, write_end = os.pipe()
    os.dup2(read_end, 0)
    yield
    os.dup2(saved, 0)
    for descriptor in (saved, read_end, write_end):
        os.close(descriptor)


@pytest.mark.usefixtures("a_stdin_that_is_not_devnull")
def test_the_service_gets_no_input_from_arde(runner: ServiceRunner, tmp_path: Path) -> None:
    body = "print(os.path.samestat(os.fstat(0), os.stat(os.devnull)), flush=True)\n" + SERVES
    config = config_for(stand_in(tmp_path, body), tmp_path)

    with runner.running(config):
        pass

    assert config.log.read_text().strip() == "True"


def open_descriptors() -> list[str]:
    return sorted(os.listdir("/dev/fd"))


def test_arde_keeps_no_descriptor_open_on_the_log_or_the_probe(
    runner: ServiceRunner, tmp_path: Path
) -> None:
    config = config_for(stand_in(tmp_path, SERVES), tmp_path)
    before = open_descriptors()

    with runner.running(config):
        during = open_descriptors()
    after = open_descriptors()

    assert (during, after) == (before, before)

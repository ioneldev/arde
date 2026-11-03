"""Runs a service on a free port, with its output in a log, and always stops it.

The binary runs directly, never through a make target, in its own process group, so the stop
reaches what the service started as well. This file knows nothing about the language the service
is written in or the repo it came from.
"""

import contextlib
import os
import signal
import socket
import stat
import subprocess
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from arde.adapters.child_env import ALLOWED_VARIABLES, child_environment
from arde.adapters.child_process import describe_os_error, force_stop, read_tail, signal_group
from arde.adapters.child_text import MESSAGE_TAIL_BYTES, bounded_text
from arde.application.ports import (
    ServiceConfig,
    ServiceDiedError,
    ServiceHandle,
    ServiceRunner,
    ServiceStartError,
    ServiceStopError,
)
from arde.domain.errors import ArdeError

LOOPBACK = "127.0.0.1"
# How long a service gets to finish what it is doing after SIGTERM, before it is killed.
STOP_GRACE_S = 5
# The log may hold anything the service printed, so only its owner may read it.
LOG_MODE = 0o600
POLL_INTERVAL_S = 0.05
CONNECT_TIMEOUT_S = 1
# A process that was killed may take a moment to let go of the socket it held.
PORT_RELEASE_WAIT_S = 1


@dataclass(frozen=True)
class _Started:
    process: subprocess.Popen[bytes]
    config: ServiceConfig
    port: int


class LocalServiceRunner(ServiceRunner):
    @contextmanager
    def running(self, config: ServiceConfig) -> Iterator[ServiceHandle]:
        _require_a_port_variable_the_service_can_spare(config)
        started = _start(config, port=_free_port())
        cause: BaseException | None = None
        was_in_use = False
        try:
            _wait_until_listening(started)
            was_in_use = True
            yield ServiceHandle(base_url=f"http://{LOOPBACK}:{started.port}", port=started.port)
        except BaseException as error:
            cause = error
            raise
        finally:
            _stop_keeping_the_cause(started, cause=cause, was_in_use=was_in_use)


# The return type is the static proof that LocalServiceRunner satisfies the port: mypy fails here
# when the port gains a method that LocalServiceRunner lacks, or when a signature drifts.
def local_service_runner() -> ServiceRunner:
    return LocalServiceRunner()


# The ARDE port variable would replace a variable the service needs as it is, such as PATH.
def _require_a_port_variable_the_service_can_spare(config: ServiceConfig) -> None:
    if config.port_env in ALLOWED_VARIABLES:
        raise ServiceStartError(
            f"The port variable {config.port_env} is one the service needs as it is, and ARDE "
            "would replace it with the port number.",
            next_action=(
                "Correct the port variable in the profile to the one the service reads its port "
                "from, then run again."
            ),
        )


# Another process can take the port between the close and the service's bind. If that process is
# listening, the first probe connects to it and the wait ends, so the mistake shows only when the
# service, which could not bind, is found to have exited.
def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind((LOOPBACK, 0))
        return int(probe.getsockname()[1])


def _start(config: ServiceConfig, *, port: int) -> _Started:
    environment = {**child_environment(), config.port_env: str(port)}
    with _open_log(config) as log:
        try:
            process = subprocess.Popen(
                # Absolute, because the child changes folder before it looks for the program.
                [str(config.binary.absolute())],
                cwd=config.cwd,
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        except OSError as error:
            raise ServiceStartError(
                f"Cannot start the service {config.binary}: {describe_os_error(error)}.",
                next_action=(
                    "Check that the binary exists and can be run, and that the folder it runs "
                    "in exists, then run again."
                ),
            ) from error
    return _Started(process=process, config=config, port=port)


# A log left by an earlier run is replaced, so the tail of an error never shows another run. It is
# opened without following a link and checked after opening, because truncating a file the path
# only points to, or waiting for a reader on a FIFO, would be worse than refusing.
def _open_log(config: ServiceConfig) -> BinaryIO:
    flags = os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK
    try:
        log = os.fdopen(os.open(config.log, flags, LOG_MODE), "wb")
    except OSError as error:
        raise _cannot_write_the_log(config, describe_os_error(error)) from error
    try:
        _make_it_a_fresh_private_file(log, config)
    except BaseException:
        log.close()
        raise
    return log


def _make_it_a_fresh_private_file(log: BinaryIO, config: ServiceConfig) -> None:
    descriptor = log.fileno()
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise _cannot_write_the_log(config, "it is not a regular file")
        os.ftruncate(descriptor, 0)
        # The mode given to open applies only to a file it creates.
        os.fchmod(descriptor, LOG_MODE)
        # Opened without blocking to refuse a FIFO, and the service must not inherit that.
        os.set_blocking(descriptor, True)
    except OSError as error:
        raise _cannot_write_the_log(config, describe_os_error(error)) from error


def _cannot_write_the_log(config: ServiceConfig, reason: str) -> ServiceStartError:
    return ServiceStartError(
        f"Cannot write the service log {config.log}: {reason}.",
        next_action=(
            "Make sure the log is a plain file, or a path that can become one, then run again."
        ),
    )


def _wait_until_listening(started: _Started) -> None:
    deadline = time.monotonic() + started.config.timeout_s
    while True:
        if started.process.poll() is not None:
            raise _exited_before_listening(started, exit_code=started.process.returncode)
        if _is_listening(started):
            return
        if time.monotonic() >= deadline:
            raise _never_listened(started)
        time.sleep(POLL_INTERVAL_S)


# A connection is the whole test, because a service need not have a health route. A connection
# that fails says nothing accepts one, and a listener dying during the handshake resets it. Any
# other error, such as a full file table, means the port was never tested, and reading that as
# closed would pass the stop check on no evidence.
def _accepts_connection(port: int) -> bool:
    try:
        socket.create_connection((LOOPBACK, port), timeout=CONNECT_TIMEOUT_S).close()
    except (ConnectionError, TimeoutError):
        return False
    return True


def _is_listening(started: _Started) -> bool:
    try:
        return _accepts_connection(started.port)
    except OSError as error:
        raise ServiceStartError(
            f"Cannot test whether port {started.port} is open: {describe_os_error(error)}.",
            next_action="Fix what the message names on this machine, then run again.",
        ) from error


# This runs while an error is being built, and must never replace it with a worse one.
def _seems_to_accept_connections(port: int) -> bool:
    with contextlib.suppress(OSError):
        return _accepts_connection(port)
    return False


def _exited_before_listening(started: _Started, *, exit_code: int) -> ServiceStartError:
    config, port = started.config, started.port
    if _seems_to_accept_connections(port):
        return ServiceStartError(
            f"The service exited with code {exit_code}, but port {port} still accepts "
            f"connections. Something is holding port {port}, so ARDE cannot tell whether that is "
            f"the service.\n{_log_tail(config.log)}",
            next_action=(
                f"Find what listens on {LOOPBACK}:{port}, for example with `lsof -i :{port}`, "
                f"stop it, and read {config.log} to see why the service stopped, then run again."
            ),
        )
    return ServiceStartError(
        f"The service exited with code {exit_code} before it opened port {port}, which it should "
        f"read from {config.port_env}.\n{_log_tail(config.log)}",
        next_action=(
            f"Read {config.log} to see why the service stopped, fix that, then run again. "
            f"If the log does not say, check that it reads its port from {config.port_env}."
        ),
    )


def _never_listened(started: _Started) -> ServiceStartError:
    config, port = started.config, started.port
    return ServiceStartError(
        f"The service did not accept a connection on {LOOPBACK}:{port} within "
        f"{config.timeout_s:g} s. It should read its port from {config.port_env} and listen on "
        f"{LOOPBACK}.\n{_log_tail(config.log)}",
        next_action=(
            f"Check that the service reads its port from {config.port_env} and listens on "
            f"{LOOPBACK}, where ARDE connects, so a service on ::1 alone is not found. Read "
            f"{config.log} to see what it did instead, then run again."
        ),
    )


def _died_while_in_use(started: _Started, *, exit_code: int) -> ServiceDiedError:
    config = started.config
    return ServiceDiedError(
        f"The service exited with code {exit_code} while it was in use, after it opened port "
        f"{started.port}.\n{_log_tail(config.log)}",
        next_action=f"Read {config.log} to see why the service stopped, fix that, then run again.",
    )


# This runs while an error is being built, and must never replace it with a worse one.
def _log_tail(log: Path) -> str:
    try:
        with log.open("rb") as file:
            tail = read_tail(file, MESSAGE_TAIL_BYTES).strip()
    except OSError as error:
        return f"{log} could not be read: {describe_os_error(error)}."
    if not tail:
        return f"{log} is empty."
    return f"The end of {log}:\n{bounded_text(tail, MESSAGE_TAIL_BYTES)}"


# The error on its way out is the one that says what went wrong. What the stop finds is attached
# to it as a note, and raised only when the body ended well. The caller passes the cause in
# because sys.exception() would also report an error the caller is handling elsewhere.
def _stop_keeping_the_cause(
    started: _Started, *, cause: BaseException | None, was_in_use: bool
) -> None:
    # Read before the SIGTERM: a service that is already gone did not leave because of it.
    exit_code = started.process.poll() if was_in_use else None
    failed_stop = _failure_of_the_stop(started)
    died = None if exit_code is None else _died_while_in_use(started, exit_code=exit_code)
    first = cause or died or failed_stop
    if first is None:
        return
    for problem in (died, failed_stop):
        if problem is not None and problem is not first:
            first.add_note(_as_note(problem))
    if cause is None:
        raise first


def _failure_of_the_stop(started: _Started) -> Exception | None:
    try:
        _stop(started)
    except Exception as failure:
        return failure
    return None


def _as_note(problem: Exception) -> str:
    if isinstance(problem, ArdeError):
        return f"{problem} {problem.next_action}"
    return f"Stopping the service raised {type(problem).__name__}: {problem}"


def _stop(started: _Started) -> None:
    process = started.process
    try:
        signal_group(process, signal.SIGTERM)
        with contextlib.suppress(subprocess.TimeoutExpired):
            process.wait(timeout=STOP_GRACE_S)
    finally:
        # Also when the leader has gone, because what it started may have outlived it, and also
        # when the wait was interrupted, so that Ctrl-C twice leaves nothing running.
        force_stop(process)
    if process.poll() is None:
        raise ServiceStopError(
            f"The service (process {process.pid}) did not stop after SIGKILL.",
            next_action=f"Stop it by hand with `kill -9 -{process.pid}`, then run again.",
        )
    if not _port_is_released(started.port):
        raise ServiceStopError(
            f"Port {started.port} still accepts connections after the service was stopped, so "
            "something is holding it.",
            next_action=(
                f"Find what listens on {LOOPBACK}:{started.port}, for example with "
                f"`lsof -i :{started.port}`, and stop it, then run again."
            ),
        )


def _port_is_released(port: int) -> bool:
    deadline = time.monotonic() + PORT_RELEASE_WAIT_S
    try:
        while _accepts_connection(port):
            if time.monotonic() >= deadline:
                return False
            time.sleep(POLL_INTERVAL_S)
    except OSError as error:
        raise ServiceStopError(
            f"Cannot test whether port {port} was released: {describe_os_error(error)}.",
            next_action=(
                f"Fix what the message names on this machine, check `lsof -i :{port}` for "
                "anything left running, then run again."
            ),
        ) from error
    return True

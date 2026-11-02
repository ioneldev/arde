"""Runs a service on a free port, with its output in a log, and always stops it.

The binary runs directly, never through a make target, in its own process group, so the stop
reaches what the service started as well. This file knows nothing about the language the service
is written in or the repo it came from.
"""

import contextlib
import os
import signal
import socket
import subprocess
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import BinaryIO

from arde.adapters.child_env import child_environment
from arde.adapters.child_process import describe_os_error, force_stop, signal_group
from arde.application.ports import (
    ServiceConfig,
    ServiceHandle,
    ServiceRunner,
    ServiceStartError,
    ServiceStopError,
)

LOOPBACK = "127.0.0.1"
# How long a service gets to finish what it is doing after SIGTERM, before it is killed.
STOP_GRACE_S = 5
# The log may hold anything the service printed, so only its owner may read it.
LOG_MODE = 0o600
LOG_TAIL_BYTES = 2000
POLL_INTERVAL_S = 0.05
CONNECT_TIMEOUT_S = 1


class LocalServiceRunner(ServiceRunner):
    @contextmanager
    def running(self, config: ServiceConfig) -> Iterator[ServiceHandle]:
        port = _free_port()
        process = _start(config, port=port)
        cause: BaseException | None = None
        try:
            _wait_until_listening(process, config, port=port)
            yield ServiceHandle(base_url=f"http://{LOOPBACK}:{port}", port=port)
        except BaseException as error:
            cause = error
            raise
        finally:
            _stop_keeping_the_cause(process, cause)


# The return type is the static proof that LocalServiceRunner satisfies the port: mypy fails here
# when the port gains a method that LocalServiceRunner lacks, or when a signature drifts.
def local_service_runner() -> ServiceRunner:
    return LocalServiceRunner()


# Another process can take the port between the close and the service's bind. The window is
# short, and a service that loses the race fails to bind and is reported as one that exited.
def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind((LOOPBACK, 0))
        return int(probe.getsockname()[1])


def _start(config: ServiceConfig, *, port: int) -> subprocess.Popen[bytes]:
    environment = {**child_environment(), config.port_env: str(port)}
    with _open_log(config) as log:
        try:
            return subprocess.Popen(
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


# A log left by an earlier run is replaced, so the tail of an error never shows another run.
def _open_log(config: ServiceConfig) -> BinaryIO:
    try:
        log = os.fdopen(os.open(config.log, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, LOG_MODE), "wb")
        # The mode given to open applies only to a file it creates.
        os.fchmod(log.fileno(), LOG_MODE)
    except OSError as error:
        raise ServiceStartError(
            f"Cannot write the service log {config.log}: {describe_os_error(error)}.",
            next_action="Make sure the log's folder exists and can be written to, then run again.",
        ) from error
    return log


def _wait_until_listening(
    process: subprocess.Popen[bytes], config: ServiceConfig, *, port: int
) -> None:
    deadline = time.monotonic() + config.timeout_s
    while True:
        if process.poll() is not None:
            raise _exited_before_listening(config, port=port, exit_code=process.returncode)
        if _accepts_connection(port):
            return
        if time.monotonic() >= deadline:
            raise _never_listened(config, port=port)
        time.sleep(POLL_INTERVAL_S)


# A connection is the whole test, because a service need not have a health route.
def _accepts_connection(port: int) -> bool:
    try:
        socket.create_connection((LOOPBACK, port), timeout=CONNECT_TIMEOUT_S).close()
    except OSError:
        return False
    return True


def _exited_before_listening(
    config: ServiceConfig, *, port: int, exit_code: int
) -> ServiceStartError:
    return ServiceStartError(
        f"The service exited with code {exit_code} before it opened port {port}, which it should "
        f"read from {config.port_env}.\n{_log_tail(config.log)}",
        next_action=(
            f"Read {config.log} to see why the service stopped, fix that, then run again. "
            f"If the log does not say, check that it reads its port from {config.port_env}."
        ),
    )


def _never_listened(config: ServiceConfig, *, port: int) -> ServiceStartError:
    return ServiceStartError(
        f"The service did not open port {port} within {config.timeout_s:g} s. It should read "
        f"its port from {config.port_env}.\n{_log_tail(config.log)}",
        next_action=(
            f"Check that the service reads its port from {config.port_env}, and read "
            f"{config.log} to see what it did instead, then run again."
        ),
    )


def _log_tail(log: Path) -> str:
    with log.open("rb") as file:
        size = file.seek(0, os.SEEK_END)
        file.seek(max(size - LOG_TAIL_BYTES, 0))
        tail = file.read().decode("utf-8", errors="replace").strip()
    return f"The end of {log}:\n{tail}" if tail else f"{log} is empty."


# The error on its way out is the one that says what went wrong. A stop that fails on top of it
# is attached to it as a note, and raised only when the body ended well. The caller passes the
# cause in because sys.exception() would also report an error the caller is handling elsewhere.
def _stop_keeping_the_cause(process: subprocess.Popen[bytes], cause: BaseException | None) -> None:
    try:
        _stop(process)
    except Exception as failure:
        if cause is None:
            raise
        cause.add_note(f"ARDE also failed to stop the service: {failure}")


def _stop(process: subprocess.Popen[bytes]) -> None:
    signal_group(process, signal.SIGTERM)
    try:
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

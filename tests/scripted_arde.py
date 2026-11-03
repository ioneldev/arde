"""The arde command as a process of its own, with a model that answers from a script, so a test can
send it a real signal.

    python tests/scripted_arde.py <seen.json> --repo <path>

Discovery gets the shop's profile. Explore's first request then writes the running service's port
and binary to seen.json, and waits for the signal that stops the run.
"""

import json
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path

# Python puts this script's own folder first on the path, so the test helpers beside it import.
from end_to_end_support import submitting_the_shop_profile

from arde.application.ports import (
    LLMRequest,
    LLMResponse,
    ServiceConfig,
    ServiceHandle,
    ServiceRunner,
    Tracer,
)
from arde.cli import LOCAL_WIRING, run, signals_as_interrupts


class RecordingRunner:
    """The real runner, which notes each service it started."""

    def __init__(self, real: ServiceRunner) -> None:
        self._real = real
        self.started: list[tuple[ServiceConfig, ServiceHandle]] = []

    @contextmanager
    def running(self, config: ServiceConfig) -> Iterator[ServiceHandle]:
        with self._real.running(config) as service:
            self.started.append((config, service))
            yield service


class WaitingModel:
    def __init__(self, runner: RecordingRunner, seen: Path) -> None:
        self._runner = runner
        self._seen = seen

    def complete(self, request: LLMRequest) -> LLMResponse:
        if any(tool.name == "submit_profile" for tool in request.tools):
            return submitting_the_shop_profile()
        config, service = self._runner.started[-1]
        self._seen.write_text(json.dumps({"port": service.port, "binary": str(config.binary)}))
        while True:
            time.sleep(0.1)


def main() -> int:
    seen, argv = Path(sys.argv[1]), sys.argv[2:]
    runner = RecordingRunner(LOCAL_WIRING.service_runner())

    def model(key: object, tracer: Tracer) -> WaitingModel:
        return WaitingModel(runner, seen)

    wiring = replace(LOCAL_WIRING, service_runner=lambda: runner, model_client=model)  # type: ignore[arg-type]
    with signals_as_interrupts():
        return run(argv, wiring, out=sys.stdout, err=sys.stderr)


if __name__ == "__main__":
    sys.exit(main())

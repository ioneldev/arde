"""The command line: uv run arde --repo <url or path> [--branch B] [--json] [--max-usd N].

The one place that wires adapters into the application. It never prompts, so it can run
unattended. Bad input stops it before any work: no key, a cap that is not a positive number, or a
source that cannot be resolved. The source is resolved here, before the run starts, because the
workspace, and with it the run's folder and trace, is named after the repo id it gives.
"""

import argparse
import signal
import sys
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO

from pydantic import SecretStr

from arde.adapters.agent_toolbox import local_toolbox
from arde.adapters.console import Console, ConsoleTracer
from arde.adapters.http_tool import local_service_client
from arde.adapters.language.go.adapter import go_adapter
from arde.adapters.llm.anthropic_client import AnthropicClient, api_key_from_environment
from arde.adapters.models_file import load_models
from arde.adapters.service_runner import local_service_runner
from arde.adapters.source import SourceRequest, git_source_control, resolve_source
from arde.adapters.tracer import JsonlTracer
from arde.adapters.workspace import Workspace, arde_home
from arde.application.agent_loop import AgentDeps
from arde.application.budget import Budget
from arde.application.models_config import Role, RoleConfig
from arde.application.pipeline import RunDeps, RunRequest, run_pipeline
from arde.application.pipeline_deps import PipelineDeps
from arde.application.ports import (
    AgentToolbox,
    LanguageAdapter,
    LLMClient,
    ResolvedSource,
    ServiceClient,
    ServiceRunner,
    SourceControl,
    Tracer,
)
from arde.domain.base import visible_text
from arde.domain.errors import ArdeError
from arde.domain.report import ExitCode, RunReport

DEFAULT_MAX_USD = 10.0
# Inside the run folder, beside the copies and report.json.
TRACE_FOLDER = "trace"
REPORT_FILE = "report.json"
# Where the tools the language adapter builds, such as its helper, are kept between runs.
BIN_FOLDER = "bin"


class MissingApiKey(ArdeError):
    def __init__(self) -> None:
        super().__init__(
            "ANTHROPIC_API_KEY is not set, and every run calls the model.",
            next_action=(
                "Put the key in .env as ANTHROPIC_API_KEY=..., then run "
                "uv run --env-file .env arde --repo <url or path>"
            ),
            exit_code=ExitCode.BAD_USAGE,
        )


class BadCostCap(ArdeError):
    def __init__(self, given: float) -> None:
        super().__init__(
            f"--max-usd must be a positive number of dollars, not {given}.",
            next_action="Run again with --max-usd set to a positive number, such as 10.",
            exit_code=ExitCode.BAD_USAGE,
        )


@dataclass(frozen=True)
class Wiring:
    """What a run is built from. A test replaces a part, such as the model client."""

    # Takes the folder where the adapter keeps the tools it builds.
    language_adapter: Callable[[Path], LanguageAdapter]
    model_client: Callable[[SecretStr, Tracer], LLMClient]
    service_runner: Callable[[], ServiceRunner]
    service_client: Callable[[], ServiceClient]
    toolbox: Callable[[], AgentToolbox]
    source_control: Callable[[], SourceControl]


LOCAL_WIRING = Wiring(
    language_adapter=go_adapter,
    model_client=AnthropicClient.from_api_key,
    service_runner=local_service_runner,
    service_client=local_service_client,
    toolbox=local_toolbox,
    source_control=git_source_control,
)


@dataclass(frozen=True)
class Arguments:
    repo: str
    branch: str | None
    as_json: bool
    max_usd: float


@dataclass(frozen=True)
class _Ready:
    """Everything a run needs that can fail on bad input, made before the run's folder."""

    key: SecretStr
    budget: Budget
    models: dict[Role, RoleConfig]
    adapter: LanguageAdapter
    source: ResolvedSource
    store: Workspace


def main(argv: Sequence[str] | None = None) -> int:
    with signals_as_interrupts():
        return run(argv, LOCAL_WIRING, out=sys.stdout, err=sys.stderr)


@contextmanager
def signals_as_interrupts() -> Iterator[None]:
    """Makes SIGTERM and SIGHUP stop the run as Ctrl-C does, while the body runs.

    Python has no handler for either by default, so the process would end with no finally run,
    and a service it started would go on running. A KeyboardInterrupt runs every finally.
    """
    numbers = (signal.SIGTERM, signal.SIGHUP)
    previous = {number: signal.signal(number, signal.default_int_handler) for number in numbers}
    try:
        yield
    finally:
        for number, handler in previous.items():
            signal.signal(number, handler)


def run(argv: Sequence[str] | None, wiring: Wiring, *, out: TextIO, err: TextIO) -> int:
    try:
        arguments = parse_arguments(argv, out=out, err=err)
    except SystemExit as stop:
        # argparse has already said why, on stderr, or printed the help on stdout.
        return stop.code if isinstance(stop.code, int) else ExitCode.BAD_USAGE
    console = Console(out=out, err=err, as_json=arguments.as_json)
    try:
        return _run(arguments, wiring=wiring, console=console)
    except KeyboardInterrupt as interrupt:
        return console.interrupted(list(getattr(interrupt, "__notes__", ())))


def parse_arguments(
    argv: Sequence[str] | None, *, out: TextIO = sys.stdout, err: TextIO = sys.stderr
) -> Arguments:
    """The command's arguments. Raises SystemExit on --help or a bad one, as argparse does."""
    with redirect_stdout(out), redirect_stderr(err):
        parsed = _parser().parse_args(argv)
    return Arguments(
        repo=parsed.repo, branch=parsed.branch, as_json=parsed.json, max_usd=parsed.max_usd
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="arde",
        description=(
            "Documents an HTTP service: annotates its handlers, generates its OpenAPI file, "
            "writes its docs, and checks that the edits changed no behaviour."
        ),
    )
    parser.add_argument("--repo", required=True, help="a git URL, or the top folder of a repo")
    parser.add_argument("--branch", help="the branch to clone from a URL")
    parser.add_argument("--json", action="store_true", help="print only the report, as JSON")
    parser.add_argument(
        "--max-usd",
        type=float,
        default=DEFAULT_MAX_USD,
        help=f"stop the run once it has spent this much on the model (default {DEFAULT_MAX_USD:g})",
    )
    return parser


def _run(arguments: Arguments, *, wiring: Wiring, console: Console) -> int:
    try:
        ready = _make_ready(arguments, wiring)
        run_dir = ready.store.new_run_dir()
    except ArdeError as error:
        return console.show(_report_before_the_run(error))
    console.progress(f"run folder {run_dir}")
    trace = JsonlTracer(run_dir / TRACE_FOLDER, secrets=(ready.key,))
    tracer = ConsoleTracer(trace, console)
    try:
        client = wiring.model_client(ready.key, tracer)
    except ArdeError as error:
        failed = _report_before_the_run(error, run_dir=run_dir)
        return _publish(trace.redacted_report(failed), console)
    deps = _deps(ready, wiring=wiring, tracer=tracer, client=client)
    report = run_pipeline(RunRequest(source=ready.source, run_dir=run_dir), deps)
    return _publish(trace.redacted_report(report), console)


def _make_ready(arguments: Arguments, wiring: Wiring) -> _Ready:
    key = api_key_from_environment()
    if key is None:
        raise MissingApiKey()
    budget = _budget(arguments.max_usd)
    models = load_models()
    home = arde_home()
    adapter = wiring.language_adapter(home / BIN_FOLDER)
    source = resolve_source(SourceRequest(repo=arguments.repo, branch=arguments.branch), home)
    return _Ready(
        key=key,
        budget=budget,
        models=models,
        adapter=adapter,
        source=source,
        store=Workspace(home, source.repo_id),
    )


# Budget refuses such a cap too, but its message names no flag.
def _budget(max_usd: float) -> Budget:
    if not (0 < max_usd < float("inf")):
        raise BadCostCap(max_usd)
    return Budget(max_usd)


def _deps(ready: _Ready, *, wiring: Wiring, tracer: Tracer, client: LLMClient) -> RunDeps:
    shared = PipelineDeps(
        adapter=ready.adapter,
        agents=AgentDeps(client=client, tracer=tracer, budget=ready.budget),
        models=ready.models,
        store=ready.store,
        service_runner=wiring.service_runner(),
        service_client=wiring.service_client(),
        toolbox=wiring.toolbox(),
    )
    return RunDeps(shared=shared, source_control=wiring.source_control())


def _publish(report: RunReport, console: Console) -> int:
    path = Path(report.run_dir) / REPORT_FILE
    try:
        path.write_text(report.model_dump_json(indent=2) + "\n", encoding="utf-8")
    except OSError as error:
        console.progress(f"cannot write {path}: {error}")
    return console.show(report)


def _report_before_the_run(error: ArdeError, *, run_dir: Path | None = None) -> RunReport:
    return RunReport(
        status="failed",
        exit_code=error.exit_code,
        message=visible_text(str(error)),
        next_action=visible_text(error.next_action),
        findings=(),
        warnings=(),
        exemptions=(),
        change_labels=(),
        cost_usd=0.0,
        duration_s=0.0,
        per_role={},
        changed_paths=(),
        run_dir="" if run_dir is None else str(run_dir),
    )

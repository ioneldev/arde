"""What a person sees: progress on stderr, and the report on stdout, or as JSON alone with --json.

Every text a report holds may come from a repo or a model, so each control character is written
out before it reaches the terminal, where an escape sequence could clear the screen and hide a
failure.
"""

from typing import TextIO

from arde.application.ports import Tracer
from arde.domain.base import visible_text
from arde.domain.report import ExitCode, RunReport

PREFIX = "arde:"
# What the cli exits with when the run was interrupted, as a shell does for SIGINT.
INTERRUPTED_EXIT_CODE = 130


class Console:
    def __init__(self, *, out: TextIO, err: TextIO, as_json: bool) -> None:
        self._out = out
        self._err = err
        self._as_json = as_json

    def progress(self, text: str) -> None:
        self._write(self._err, f"{PREFIX} {text}")

    def show(self, report: RunReport) -> int:
        """Prints the report, and returns the exit code it carries."""
        if self._as_json:
            self._out.write(report.model_dump_json() + "\n")
        else:
            stream = self._out if report.exit_code == ExitCode.OK else self._err
            self._write(stream, "\n".join(_summary(report)))
        self._out.flush()
        return int(report.exit_code)

    def interrupted(self, notes: list[str]) -> int:
        lines = [
            f"{PREFIX} interrupted. The services this run started were stopped, nothing was "
            "committed, and the last good run's state is kept.",
            *(f"  {note}" for note in notes),
        ]
        self._write(self._err, "\n".join(lines))
        return INTERRUPTED_EXIT_CODE

    def _write(self, stream: TextIO, text: str) -> None:
        stream.write(visible_text(text) + "\n")
        stream.flush()


class ConsoleTracer(Tracer):
    """Passes every event on, and shows each step on stderr as it starts and ends."""

    def __init__(self, inner: Tracer, console: Console) -> None:
        self._inner = inner
        self._console = console

    # The inner tracer first, so the trace holds the event even when the terminal is gone.
    def event(self, kind: str, **fields: object) -> None:
        self._inner.event(kind, **fields)
        step = fields.get("step")
        if kind == "step_start":
            self._console.progress(f"{step}...")
        elif kind == "step_end":
            self._console.progress(f"{step} done in {fields.get('duration_s')} s")

    def debug(self, text: str) -> None:
        self._inner.debug(text)


def _summary(report: RunReport) -> list[str]:
    lines = [
        f"{PREFIX} {report.status}. {report.message}",
        f"  next: {report.next_action}",
    ]
    if report.findings:
        lines.append(f"  findings ({len(report.findings)}):")
        for finding in report.findings:
            lines.append(
                f"  - [{finding.check}, {finding.owner}] {finding.subject}: {finding.message}"
            )
            lines.append(f"    next: {finding.next_action}")
    if report.warnings:
        lines.append(f"  warnings ({len(report.warnings)}):")
        lines.extend(f"  - {warning}" for warning in report.warnings)
    # Bad input stops a run before it has a folder, and before anything is spent.
    if report.run_dir:
        lines.append(f"  cost ${report.cost_usd:.4f} in {report.duration_s:.1f} s")
        lines.extend(
            f"    {role}: {totals.calls} calls, ${totals.cost_usd:.4f}, {totals.latency_s:.1f} s"
            for role, totals in report.per_role.items()
        )
        lines.append(f"  run folder: {report.run_dir}")
    return lines

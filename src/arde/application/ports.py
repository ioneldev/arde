"""The interfaces the application needs from the outside world. Adapters implement them."""

from typing import Protocol


class Tracer(Protocol):
    """Writes the run's record: structured events for the trace, free text for the debug log.

    Event kinds: run_start, step_start, step_end, model_call, tool_call, check_result, repair,
    correction, warning and run_end. The tracer stamps each event, so callers pass no time.
    """

    def event(self, kind: str, **fields: object) -> None: ...

    def debug(self, text: str) -> None: ...

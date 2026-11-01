"""A tracer that keeps what it is given, so a test can read back what was logged."""


class RecordingTracer:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, object]]] = []
        self.debug_lines: list[str] = []

    def event(self, kind: str, **fields: object) -> None:
        self.events.append((kind, fields))

    def debug(self, text: str) -> None:
        self.debug_lines.append(text)

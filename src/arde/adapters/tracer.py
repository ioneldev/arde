"""Writes trace.jsonl and debug.log for one run, with every secret replaced by a marker."""

import json
import re
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path

from pydantic import SecretStr

from arde.application.ports import Tracer

REDACTED = "[REDACTED]"
_MATCHES_NOTHING = r"(?!)"


class JsonlTracer(Tracer):
    # SecretStr, because a traceback prints the arguments of every frame it passes through.
    def __init__(self, run_dir: Path, secrets: tuple[SecretStr, ...]) -> None:
        run_dir.mkdir(parents=True, exist_ok=True)
        self._trace_path = run_dir / "trace.jsonl"
        self._debug_path = run_dir / "debug.log"
        self._secret_pattern = _pattern_matching(secrets)

    def event(self, kind: str, **fields: object) -> None:
        if "ts" in fields:
            raise ValueError("the field name 'ts' is written by the tracer: rename it")
        record = {"ts": _now(), "kind": kind, **fields}
        line = json.dumps(self._redacted(record), ensure_ascii=False)
        _append_line(self._trace_path, line)

    def debug(self, text: str) -> None:
        _append_line(self._debug_path, f"{_now()} {self._redact_text(text)}")

    # Walks the values before they become JSON, because JSON escaping would change a secret that
    # holds a quote or a backslash and the plain text would no longer match.
    def _redacted(self, value: object) -> object:
        if isinstance(value, str):
            return self._redact_text(value)
        if isinstance(value, Mapping):
            return {
                self._redact_text(str(key)): self._redacted(item) for key, item in value.items()
            }
        if isinstance(value, list | tuple):
            return [self._redacted(item) for item in value]
        if value is None or isinstance(value, bool | int | float):
            return value
        return self._redact_text(str(value))

    def _redact_text(self, text: str) -> str:
        return self._secret_pattern.sub(REDACTED, text)


# Built in one expression and kept compiled, so no frame holds a plain secret for --showlocals.
# A key read from a file often ends in a newline, which is never part of the key. Stripping makes
# the secret match the text that really appears. An empty secret is dropped, because it would
# match between every character. With none left, the pattern must match nothing. The longest
# secret goes first, so a secret that contains another is replaced whole and leaves no tail behind.
def _pattern_matching(secrets: tuple[SecretStr, ...]) -> re.Pattern[str]:
    return re.compile(
        "|".join(
            map(
                re.escape,
                sorted(
                    {secret.get_secret_value().strip() for secret in secrets} - {""},
                    key=len,
                    reverse=True,
                ),
            )
        )
        or _MATCHES_NOTHING
    )


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


def _append_line(path: Path, line: str) -> None:
    with path.open("a", encoding="utf-8") as file:
        file.write(line + "\n")

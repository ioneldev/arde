"""Writes trace.jsonl and debug.log for one run, with every secret replaced by a marker."""

import json
import math
import re
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, SecretStr

from arde.application.ports import Tracer

REDACTED = "[REDACTED]"
_SUMMARY_FIELD = "summary"
_MAX_SUMMARY_CHARS = 200
_CUT_MARK = " ..."


class JsonlTracer(Tracer):
    # SecretStr, because a traceback prints the arguments of every frame it passes through.
    def __init__(self, run_dir: Path, secrets: tuple[SecretStr, ...]) -> None:
        self._secret_pattern = _pattern_matching(secrets)
        # 0700, because debug.log holds source code. Never an existing folder, because two runs
        # sharing one would merge their traces with nothing to tell them apart.
        run_dir.mkdir(mode=0o700, parents=True, exist_ok=False)
        self._trace_path = run_dir / "trace.jsonl"
        self._debug_path = run_dir / "debug.log"
        # Created now, so a folder that cannot hold a trace fails before anything is paid for.
        _create(self._trace_path)
        _create(self._debug_path)

    def event(self, kind: str, **fields: object) -> None:
        if "ts" in fields:
            raise ValueError("the field name 'ts' is written by the tracer: rename it")
        redacted = self._redacted({"ts": _now(), "kind": kind, **fields})
        # ensure_ascii, because a character such as U+2028 ends a line for some readers, and each
        # event must stay exactly one line. allow_nan off, because NaN is not JSON.
        line = json.dumps(redacted, ensure_ascii=True, allow_nan=False)
        _append_line(self._trace_path, line)

    def debug(self, text: str) -> None:
        _append_line(self._debug_path, f"{_now()} {self._redact_text(text)}")

    # Walks the values before they become JSON, because JSON escaping would change a secret that
    # holds a quote or a backslash and the plain text would no longer match.
    def _redacted(self, value: object) -> object:
        if isinstance(value, str):
            return self._redact_text(value)
        if isinstance(value, BaseModel):
            return self._redacted(value.model_dump(mode="json"))
        if isinstance(value, Mapping):
            return {
                self._redact_text(str(key)): _shortened(key, self._redacted(item))
                for key, item in value.items()
            }
        if isinstance(value, list | tuple):
            return [self._redacted(item) for item in value]
        if isinstance(value, float) and not math.isfinite(value):
            return str(value)
        if value is None or isinstance(value, bool | int | float):
            return value
        return self._redact_text(str(value))

    def _redact_text(self, text: str) -> str:
        return self._secret_pattern.sub(REDACTED, text)


# Cut here, after redaction, because a secret cut in half no longer matches and its head would
# reach the trace.
def _shortened(field: object, value: object) -> object:
    if field != _SUMMARY_FIELD or not isinstance(value, str) or len(value) <= _MAX_SUMMARY_CHARS:
        return value
    return value[: _MAX_SUMMARY_CHARS - len(_CUT_MARK)] + _CUT_MARK


# A trace with nothing to redact is a wiring mistake that would write the key in plain text.
#
# Built in one expression and kept compiled, so no frame holds a plain secret for --showlocals.
# A key read from a file often ends in a newline, which is never part of the key. Stripping makes
# the secret match the text that really appears. An empty secret is dropped, because it would
# match between every character. The longest secret goes first, so a secret that contains another
# is replaced whole and leaves no tail behind.
def _pattern_matching(secrets: tuple[SecretStr, ...]) -> re.Pattern[str]:
    if not any(secret.get_secret_value().strip() for secret in secrets):
        raise ValueError("JsonlTracer needs at least one secret to redact: pass the API key")
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
    )


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


def _create(path: Path) -> None:
    with path.open("a", encoding="utf-8"):
        pass


# backslashreplace, because a file name that is not UTF-8 reaches Python as a lone surrogate, and
# a record of what happened must not be what ends the run.
def _append_line(path: Path, line: str) -> None:
    with path.open("a", encoding="utf-8", errors="backslashreplace") as file:
        file.write(line + "\n")

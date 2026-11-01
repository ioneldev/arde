"""trace.jsonl is the run's evidence and debug.log holds source code: neither may hold a secret."""

import json
from datetime import datetime
from pathlib import Path

import pytest

from arde.adapters.tracer import REDACTED, JsonlTracer

SECRET = "sk-ant-abc"


def trace_lines(run_dir: Path) -> list[dict[str, object]]:
    text = (run_dir / "trace.jsonl").read_text(encoding="utf-8")
    return [json.loads(line) for line in text.splitlines()]


def everything_written(run_dir: Path) -> str:
    trace = (run_dir / "trace.jsonl").read_text(encoding="utf-8")
    return trace + (run_dir / "debug.log").read_text(encoding="utf-8")


def test_trace_lines_are_valid_json_with_timestamp_and_kind(tmp_path: Path) -> None:
    tracer = JsonlTracer(tmp_path, secrets=())

    tracer.event("run_start", commit="abc123")
    tracer.event("step_start", step="discovery")

    first, second = trace_lines(tmp_path)
    assert first["kind"] == "run_start"
    assert first["commit"] == "abc123"
    assert second["kind"] == "step_start"
    assert second["step"] == "discovery"
    assert datetime.fromisoformat(str(first["ts"])).tzinfo is not None


def test_every_event_is_exactly_one_line(tmp_path: Path) -> None:
    tracer = JsonlTracer(tmp_path, secrets=())

    tracer.event("warning", text="first line\nsecond line")

    assert len((tmp_path / "trace.jsonl").read_text(encoding="utf-8").splitlines()) == 1


def test_timestamp_and_kind_come_first_so_the_trace_reads_top_down(tmp_path: Path) -> None:
    JsonlTracer(tmp_path, secrets=()).event("tool_call", name="grep")

    assert list(trace_lines(tmp_path)[0]) == ["ts", "kind", "name"]


def test_a_caller_cannot_overwrite_the_timestamp(tmp_path: Path) -> None:
    tracer = JsonlTracer(tmp_path, secrets=())

    with pytest.raises(ValueError, match="ts"):
        tracer.event("run_start", ts="yesterday")


def test_json_types_survive_and_other_values_are_written_as_text(tmp_path: Path) -> None:
    tracer = JsonlTracer(tmp_path, secrets=())

    tracer.event("model_call", tokens=12, cost=0.5, cached=False, stop=None, file=Path("a/b.go"))

    event = trace_lines(tmp_path)[0]
    assert event["tokens"] == 12
    assert event["cost"] == 0.5
    assert event["cached"] is False
    assert event["stop"] is None
    assert event["file"] == str(Path("a/b.go"))


def test_the_run_folder_is_created_when_missing(tmp_path: Path) -> None:
    run_dir = tmp_path / "runs" / "2026-11-01"

    JsonlTracer(run_dir, secrets=()).event("run_start")

    assert (run_dir / "trace.jsonl").is_file()


def test_debug_text_goes_to_debug_log_with_a_timestamp(tmp_path: Path) -> None:
    tracer = JsonlTracer(tmp_path, secrets=())

    tracer.debug("full prompt\nover two lines")
    tracer.debug("a response")

    log = (tmp_path / "debug.log").read_text(encoding="utf-8")
    assert "full prompt\nover two lines" in log
    assert log.index("full prompt") < log.index("a response")
    assert len(log.splitlines()) == 3


def test_events_do_not_reach_debug_log(tmp_path: Path) -> None:
    tracer = JsonlTracer(tmp_path, secrets=())

    tracer.event("run_start", detail="only in the trace")
    tracer.debug("only in the log")

    assert "only in the trace" not in (tmp_path / "debug.log").read_text(encoding="utf-8")
    assert "only in the log" not in (tmp_path / "trace.jsonl").read_text(encoding="utf-8")


def test_new_events_are_appended_to_what_the_folder_already_holds(tmp_path: Path) -> None:
    JsonlTracer(tmp_path, secrets=()).event("run_start")
    JsonlTracer(tmp_path, secrets=()).event("run_end")

    assert [line["kind"] for line in trace_lines(tmp_path)] == ["run_start", "run_end"]


def test_secrets_are_redacted_everywhere(tmp_path: Path) -> None:
    t = JsonlTracer(tmp_path, secrets=(SECRET,))
    t.event("x", k=SECRET)
    t.debug(SECRET)
    assert SECRET not in everything_written(tmp_path)


def test_a_secret_is_replaced_by_a_marker_inside_longer_text(tmp_path: Path) -> None:
    tracer = JsonlTracer(tmp_path, secrets=(SECRET,))

    tracer.event("tool_call", header=f"x-api-key: {SECRET}; again {SECRET}")

    assert trace_lines(tmp_path)[0]["header"] == f"x-api-key: {REDACTED}; again {REDACTED}"


def test_a_secret_is_redacted_in_nested_values_keys_and_the_kind(tmp_path: Path) -> None:
    tracer = JsonlTracer(tmp_path, secrets=(SECRET,))

    tracer.event(
        f"kind-{SECRET}",
        nested={"deeper": [{"value": SECRET}, (SECRET, 1)], SECRET: "as a key"},
        path=Path(f"/home/{SECRET}"),
    )

    written = (tmp_path / "trace.jsonl").read_text(encoding="utf-8")
    assert SECRET not in written
    event = trace_lines(tmp_path)[0]
    assert event["kind"] == f"kind-{REDACTED}"
    assert event["nested"] == {"deeper": [{"value": REDACTED}, [REDACTED, 1]], REDACTED: "as a key"}


def test_a_secret_holding_json_special_characters_is_redacted(tmp_path: Path) -> None:
    secret = 'pa"ss\\word'
    tracer = JsonlTracer(tmp_path, secrets=(secret,))

    tracer.event("x", k=f"value {secret}")

    assert trace_lines(tmp_path)[0]["k"] == f"value {REDACTED}"


def test_every_secret_is_redacted(tmp_path: Path) -> None:
    tracer = JsonlTracer(tmp_path, secrets=("first-secret", "second-secret"))

    tracer.event("x", k="first-secret and second-secret")

    assert trace_lines(tmp_path)[0]["k"] == f"{REDACTED} and {REDACTED}"


def test_a_secret_that_contains_another_is_redacted_whole(tmp_path: Path) -> None:
    tracer = JsonlTracer(tmp_path, secrets=("sk-ant", "sk-ant-abc"))

    tracer.event("x", k="key sk-ant-abc")
    tracer.debug("key sk-ant-abc")

    assert "-abc" not in everything_written(tmp_path)


def test_an_empty_secret_leaves_the_output_untouched(tmp_path: Path) -> None:
    tracer = JsonlTracer(tmp_path, secrets=("",))

    tracer.event("x", k="plain")
    tracer.debug("plain")

    assert trace_lines(tmp_path)[0]["k"] == "plain"
    assert REDACTED not in everything_written(tmp_path)
    assert "plain" in (tmp_path / "debug.log").read_text(encoding="utf-8")


def test_a_secret_read_from_a_file_with_a_trailing_newline_is_still_redacted(
    tmp_path: Path,
) -> None:
    tracer = JsonlTracer(tmp_path, secrets=(f"{SECRET}\n", "  "))

    tracer.event("x", k=f"key {SECRET}", other="two  spaces")

    event = trace_lines(tmp_path)[0]
    assert event["k"] == f"key {REDACTED}"
    assert event["other"] == "two  spaces"

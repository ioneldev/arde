"""trace.jsonl is the run's evidence and debug.log holds source code: neither may hold a secret."""

import json
import stat
from datetime import datetime
from pathlib import Path

import pytest
from pydantic import BaseModel, SecretStr

from arde.adapters.tracer import REDACTED, JsonlTracer

SECRET = "sk-ant-abc"
SUMMARY_LIMIT = 200


def secret_of(text: str) -> SecretStr:
    return SecretStr(text)


def tracer_in(run_dir: Path, *secrets: str) -> JsonlTracer:
    return JsonlTracer(run_dir, secrets=tuple(map(secret_of, secrets or (SECRET,))))


@pytest.fixture
def run_dir(tmp_path: Path) -> Path:
    return tmp_path / "runs" / "2026-11-01"


def strict_json(text: str) -> dict[str, object]:
    def refuse(constant: str) -> None:
        raise ValueError(f"{constant} is not JSON")

    return json.loads(text, parse_constant=refuse)


def trace_lines(directory: Path) -> list[dict[str, object]]:
    text = (directory / "trace.jsonl").read_text(encoding="utf-8")
    return [strict_json(line) for line in text.splitlines()]


def debug_text(directory: Path) -> str:
    return (directory / "debug.log").read_text(encoding="utf-8")


def everything_written(directory: Path) -> str:
    return (directory / "trace.jsonl").read_text(encoding="utf-8") + debug_text(directory)


def test_trace_lines_are_valid_json_with_timestamp_and_kind(run_dir: Path) -> None:
    tracer = tracer_in(run_dir)

    tracer.event("run_start", commit="abc123")
    tracer.event("step_start", step="discovery")

    first, second = trace_lines(run_dir)
    assert first["kind"] == "run_start"
    assert first["commit"] == "abc123"
    assert second["kind"] == "step_start"
    assert second["step"] == "discovery"
    assert datetime.fromisoformat(str(first["ts"])).tzinfo is not None


def test_every_event_is_exactly_one_line(run_dir: Path) -> None:
    tracer = tracer_in(run_dir)

    tracer.event("warning", text="first line\nsecond line")

    assert len((run_dir / "trace.jsonl").read_text(encoding="utf-8").splitlines()) == 1


@pytest.mark.parametrize("separator", ["\u2028", "\u2029", "\u0085", "\x0b", "\x0c"])
def test_a_character_that_splits_lines_cannot_split_an_event(run_dir: Path, separator: str) -> None:
    tracer = tracer_in(run_dir)

    tracer.event("warning", text=f"before{separator}after")
    tracer.event("warning", text="next")

    assert [line["text"] for line in trace_lines(run_dir)] == [f"before{separator}after", "next"]


def test_timestamp_and_kind_come_first_so_the_trace_reads_top_down(run_dir: Path) -> None:
    tracer_in(run_dir).event("tool_call", name="grep")

    assert list(trace_lines(run_dir)[0]) == ["ts", "kind", "name"]


def test_a_caller_cannot_overwrite_the_timestamp(run_dir: Path) -> None:
    tracer = tracer_in(run_dir)

    with pytest.raises(ValueError, match="ts"):
        tracer.event("run_start", ts="yesterday")


def test_json_types_survive_and_other_values_are_written_as_text(run_dir: Path) -> None:
    tracer = tracer_in(run_dir)

    tracer.event("model_call", tokens=12, cost=0.5, cached=False, stop=None, file=Path("a/b.go"))

    event = trace_lines(run_dir)[0]
    assert event["tokens"] == 12
    assert event["cost"] == 0.5
    assert event["cached"] is False
    assert event["stop"] is None
    assert event["file"] == str(Path("a/b.go"))


@pytest.mark.parametrize(
    ("value", "written"),
    [(float("nan"), "nan"), (float("inf"), "inf"), (float("-inf"), "-inf")],
)
def test_a_float_json_cannot_hold_is_written_as_text(
    run_dir: Path, value: float, written: str
) -> None:
    tracer_in(run_dir).event("warning", value=value, nested={"inner": [value]})

    event = trace_lines(run_dir)[0]
    assert event["value"] == written
    assert event["nested"] == {"inner": [written]}


class Totals(BaseModel):
    calls: int
    path: Path


def test_a_pydantic_model_keeps_its_fields(run_dir: Path) -> None:
    tracer_in(run_dir).event(
        "run_end",
        per_role={"docs": Totals(calls=3, path=Path("a.go"))},
        last=Totals(calls=1, path=Path("b.go")),
    )

    event = trace_lines(run_dir)[0]
    assert event["per_role"] == {"docs": {"calls": 3, "path": "a.go"}}
    assert event["last"] == {"calls": 1, "path": "b.go"}


def test_a_secret_inside_a_pydantic_model_is_redacted(run_dir: Path) -> None:
    class Holder(BaseModel):
        header: str

    tracer_in(run_dir).event("x", holder=Holder(header=f"Bearer {SECRET}"))

    assert trace_lines(run_dir)[0]["holder"] == {"header": f"Bearer {REDACTED}"}


def test_the_run_folder_is_created_when_missing(run_dir: Path) -> None:
    tracer_in(run_dir).event("run_start")

    assert (run_dir / "trace.jsonl").is_file()


def test_both_files_exist_before_anything_is_written(run_dir: Path) -> None:
    tracer_in(run_dir)

    assert (run_dir / "trace.jsonl").read_text(encoding="utf-8") == ""
    assert debug_text(run_dir) == ""


def test_the_run_folder_can_be_read_by_its_owner_alone(run_dir: Path) -> None:
    tracer_in(run_dir)

    assert stat.S_IMODE(run_dir.stat().st_mode) == 0o700


def test_a_second_tracer_cannot_merge_into_an_existing_run(run_dir: Path) -> None:
    tracer_in(run_dir)

    with pytest.raises(FileExistsError):
        tracer_in(run_dir)


@pytest.mark.parametrize("secrets", [(), ("",), ("   \n",), ("", " ")])
def test_a_tracer_with_nothing_to_redact_is_refused(
    run_dir: Path, secrets: tuple[str, ...]
) -> None:
    with pytest.raises(ValueError, match="secret"):
        JsonlTracer(run_dir, secrets=tuple(map(secret_of, secrets)))

    assert not run_dir.exists()


@pytest.mark.parametrize("text", ["caf\udce9.go", "half an emoji \ud83d"])
def test_text_that_is_not_valid_unicode_never_crashes_the_tracer(run_dir: Path, text: str) -> None:
    tracer = tracer_in(run_dir)

    tracer.event("tool_call", summary=text)
    tracer.debug(text)

    assert len(trace_lines(run_dir)) == 1
    assert "\\ud" in debug_text(run_dir)


def test_debug_text_goes_to_debug_log_with_a_timestamp(run_dir: Path) -> None:
    tracer = tracer_in(run_dir)

    tracer.debug("full prompt\nover two lines")
    tracer.debug("a response")

    log = debug_text(run_dir)
    assert "full prompt\nover two lines" in log
    assert log.index("full prompt") < log.index("a response")
    assert len(log.splitlines()) == 3
    stamp = log.splitlines()[0].removesuffix(" full prompt")
    assert datetime.fromisoformat(stamp).tzinfo is not None


def test_events_do_not_reach_debug_log(run_dir: Path) -> None:
    tracer = tracer_in(run_dir)

    tracer.event("run_start", detail="only in the trace")
    tracer.debug("only in the log")

    assert "only in the trace" not in debug_text(run_dir)
    assert "only in the log" not in (run_dir / "trace.jsonl").read_text(encoding="utf-8")


def test_a_long_summary_is_cut_short_in_the_trace_and_kept_whole_nowhere(run_dir: Path) -> None:
    tracer_in(run_dir).event("tool_call", summary="word " * 10_000)

    summary = str(trace_lines(run_dir)[0]["summary"])
    assert summary.startswith("word word")
    assert summary.endswith("...")
    assert len(summary) <= SUMMARY_LIMIT


def test_a_summary_that_fits_is_left_alone(run_dir: Path) -> None:
    tracer_in(run_dir).event("tool_call", summary="x" * SUMMARY_LIMIT)

    assert trace_lines(run_dir)[0]["summary"] == "x" * SUMMARY_LIMIT


def test_text_with_no_spaces_keeps_its_first_characters(run_dir: Path) -> None:
    tracer_in(run_dir).event("tool_call", summary=json.dumps({"key": "v" * 1_000}))

    assert str(trace_lines(run_dir)[0]["summary"]).startswith('{"key": "vvv')


def test_only_the_summary_field_is_cut(run_dir: Path) -> None:
    tracer_in(run_dir).event("warning", text="word " * 100)

    assert len(str(trace_lines(run_dir)[0]["text"])) == 500


# Cut first, a secret that straddles the limit would keep its head and escape the exact match.
def test_a_secret_across_the_cut_leaves_no_part_of_itself(run_dir: Path) -> None:
    secret = "sk-ant-" + "k" * 100
    tracer_in(run_dir, secret).event("tool_call", summary="x" * 150 + secret + "y" * 300)

    written = (run_dir / "trace.jsonl").read_text(encoding="utf-8")
    assert "sk-ant" not in written
    assert "kkk" not in written


def test_a_secret_with_line_breaks_is_redacted_in_a_summary(run_dir: Path) -> None:
    pem = "-----BEGIN KEY-----\nabc\ndef\n-----END KEY-----"
    tracer_in(run_dir, pem).event("tool_call", summary=f"found {pem} in config")

    assert "abc" not in (run_dir / "trace.jsonl").read_text(encoding="utf-8")


def test_secrets_are_redacted_everywhere(run_dir: Path) -> None:
    t = tracer_in(run_dir)
    t.event("x", k=SECRET)
    t.debug(SECRET)
    assert SECRET not in everything_written(run_dir)


# --showlocals prints each frame's arguments, so the plain text must not be one of them.
def test_the_tracer_is_handed_secrets_that_print_masked(run_dir: Path) -> None:
    secrets = (secret_of(SECRET),)

    JsonlTracer(run_dir, secrets=secrets)

    assert SECRET not in repr(secrets)


def test_a_secret_is_replaced_by_a_marker_inside_longer_text(run_dir: Path) -> None:
    tracer = tracer_in(run_dir)

    tracer.event("tool_call", header=f"x-api-key: {SECRET}; again {SECRET}")

    assert trace_lines(run_dir)[0]["header"] == f"x-api-key: {REDACTED}; again {REDACTED}"


def test_a_secret_is_redacted_in_nested_values_keys_and_the_kind(run_dir: Path) -> None:
    tracer = tracer_in(run_dir)

    tracer.event(
        f"kind-{SECRET}",
        nested={"deeper": [{"value": SECRET}, (SECRET, 1)], SECRET: "as a key"},
        path=Path(f"/home/{SECRET}"),
    )

    written = (run_dir / "trace.jsonl").read_text(encoding="utf-8")
    assert SECRET not in written
    event = trace_lines(run_dir)[0]
    assert event["kind"] == f"kind-{REDACTED}"
    assert event["nested"] == {"deeper": [{"value": REDACTED}, [REDACTED, 1]], REDACTED: "as a key"}


def test_a_secret_holding_json_special_characters_is_redacted(run_dir: Path) -> None:
    secret = 'pa"ss\\word'
    tracer = tracer_in(run_dir, secret)

    tracer.event("x", k=f"value {secret}")

    assert trace_lines(run_dir)[0]["k"] == f"value {REDACTED}"


def test_every_secret_is_redacted(run_dir: Path) -> None:
    tracer = tracer_in(run_dir, "first-secret", "second-secret")

    tracer.event("x", k="first-secret and second-secret")

    assert trace_lines(run_dir)[0]["k"] == f"{REDACTED} and {REDACTED}"


def test_a_secret_that_contains_another_is_redacted_whole(run_dir: Path) -> None:
    tracer = tracer_in(run_dir, "sk-ant", "sk-ant-abc")

    tracer.event("x", k="key sk-ant-abc")
    tracer.debug("key sk-ant-abc")

    assert "-abc" not in everything_written(run_dir)


def test_a_blank_secret_beside_a_real_one_is_ignored(run_dir: Path) -> None:
    tracer = tracer_in(run_dir, SECRET, "")

    tracer.event("x", k="plain")

    assert trace_lines(run_dir)[0]["k"] == "plain"


def test_a_secret_read_from_a_file_with_a_trailing_newline_is_still_redacted(
    run_dir: Path,
) -> None:
    tracer = tracer_in(run_dir, f"{SECRET}\n", "  ")

    tracer.event("x", k=f"key {SECRET}", other="two  spaces")

    event = trace_lines(run_dir)[0]
    assert event["k"] == f"key {REDACTED}"
    assert event["other"] == "two  spaces"

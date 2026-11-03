"""Reading go test -json. The events are written out here, so nothing runs Go."""

import json

import pytest

from arde.adapters.language.go.errors import GoCommandError
from arde.adapters.language.go.go_test_events import measured_tests

PACKAGE = "example.com/orders"


def event(action: str, test: str | None = None, *, output: str | None = None) -> str:
    fields: dict[str, str] = {"Action": action, "Package": PACKAGE}
    if test is not None:
        fields["Test"] = test
    if output is not None:
        fields["Output"] = output
    return json.dumps(fields) + "\n"


def stream(*events: str) -> str:
    return "".join(events)


def read(*events: str) -> tuple[int, tuple[str, ...]]:
    measured = measured_tests(stream(*events), tests_passed=True)
    return measured.tests_run, measured.tests_skipped


def test_a_test_that_passed_ran() -> None:
    assert read(event("run", "TestA"), event("pass", "TestA")) == (1, ())


def test_a_test_that_failed_ran() -> None:
    measured = measured_tests(
        stream(event("run", "TestA"), event("fail", "TestA"), event("fail")), tests_passed=False
    )

    assert measured.tests_run == 1


def test_a_test_that_skipped_did_not_run_and_is_named_with_its_package() -> None:
    assert read(event("run", "TestA"), event("skip", "TestA")) == (0, (f"{PACKAGE}.TestA",))


def test_only_a_top_level_test_counts_as_run() -> None:
    events = (event("pass", "TestA/one"), event("pass", "TestA/two"), event("pass", "TestA"))

    assert read(*events) == (1, ())


def test_a_skipped_subtest_is_named_though_its_parent_passed() -> None:
    events = (event("skip", "TestTable/needs_a_database"), event("pass", "TestTable"))

    assert read(*events) == (1, (f"{PACKAGE}.TestTable/needs_a_database",))


# A package's own test files and its external _test package can each hold a test of one name, and
# go test reports both under the package's path, one run after the other.
def test_a_failing_test_keeps_its_text_when_a_test_of_the_same_name_passed() -> None:
    measured = measured_tests(
        stream(
            event("run", "TestTwice"),
            event("output", "TestTwice", output="--- PASS: TestTwice (0.00s)\n"),
            event("pass", "TestTwice"),
            event("run", "TestTwice"),
            event("output", "TestTwice", output="    calc_ext_test.go:26: the second fails\n"),
            event("output", "TestTwice", output="--- FAIL: TestTwice (0.00s)\n"),
            event("fail", "TestTwice"),
            event("fail"),
        ),
        tests_passed=False,
    )

    assert "calc_ext_test.go:26: the second fails" in measured.text
    assert "--- FAIL: TestTwice" in measured.text
    assert "--- PASS: TestTwice" not in measured.text
    assert measured.tests_run == 2


def test_a_package_with_no_test_files_skips_and_is_not_a_skipped_test() -> None:
    assert read(event("start"), event("skip")) == (0, ())


def test_a_package_that_passed_is_not_a_test_that_ran() -> None:
    assert read(event("start"), event("pass")) == (0, ())


def test_a_test_that_started_and_never_ended_did_not_run_to_an_end() -> None:
    measured = measured_tests(stream(event("run", "TestHang"), event("fail")), tests_passed=False)

    assert measured.tests_run == 0


def test_skipped_tests_are_sorted_and_listed_once() -> None:
    other = json.dumps({"Action": "skip", "Package": "example.com/a", "Test": "TestZ"}) + "\n"
    events = (event("skip", "TestB"), other, event("skip", "TestB"), event("skip", "TestA"))

    assert read(*events)[1] == ("example.com/a.TestZ", f"{PACKAGE}.TestA", f"{PACKAGE}.TestB")


def test_a_test_name_shows_no_control_character_and_stays_one_line() -> None:
    [name] = read(event("skip", "TestA/\x1b[2J\nforged"))[1]

    assert "\x1b" not in name
    assert "\n" not in name


def test_an_empty_stream_says_nothing_ran() -> None:
    measured = measured_tests("", tests_passed=True)

    assert (measured.tests_run, measured.tests_skipped, measured.text) == (0, (), "")


def test_fields_it_does_not_read_are_passed_over() -> None:
    line = {"Time": "2026-11-03T00:00:00Z", "Elapsed": 0.1, "OutputType": "frame"}
    with_more = json.dumps({"Action": "pass", "Package": PACKAGE, "Test": "TestA", **line}) + "\n"

    assert read(with_more) == (1, ())


def test_what_a_passing_test_printed_is_left_out_as_plain_go_test_leaves_it_out() -> None:
    measured = measured_tests(
        stream(
            event("output", "TestA", output="chatter\n"),
            event("pass", "TestA"),
            event("output", output="ok  \texample.com/orders\t0.1s\n"),
            event("pass"),
        ),
        tests_passed=True,
    )

    assert measured.text == "ok  \texample.com/orders\t0.1s\n"


def test_what_a_failing_test_printed_is_kept_in_order() -> None:
    measured = measured_tests(
        stream(
            event("output", "TestA", output="--- FAIL: TestA (0.00s)\n"),
            event("output", "TestA", output="    a_test.go:3: boom\n"),
            event("fail", "TestA"),
            event("output", output="FAIL\texample.com/orders\t0.1s\n"),
        ),
        tests_passed=False,
    )

    assert measured.text == (
        "--- FAIL: TestA (0.00s)\n    a_test.go:3: boom\nFAIL\texample.com/orders\t0.1s\n"
    )


def test_a_skip_keeps_its_reason_because_that_says_what_to_set() -> None:
    measured = measured_tests(
        stream(
            event("output", "TestA", output="    a_test.go:3: DATABASE_URL not set\n"),
            event("skip", "TestA"),
        ),
        tests_passed=True,
    )

    assert "DATABASE_URL not set" in measured.text


def test_the_text_of_a_test_that_never_ended_is_kept_because_it_names_the_hang() -> None:
    measured = measured_tests(
        stream(
            event("run", "TestHang"),
            event("output", "TestHang", output="panic: test timed out after 540s\n"),
            event("fail"),
        ),
        tests_passed=False,
    )

    assert "test timed out" in measured.text


def test_a_passing_subtest_is_left_out_of_a_failing_parent_and_the_failing_one_stays() -> None:
    measured = measured_tests(
        stream(
            event("output", "TestA/fine", output="fine\n"),
            event("pass", "TestA/fine"),
            event("output", "TestA/broken", output="broken\n"),
            event("fail", "TestA/broken"),
            event("fail", "TestA"),
        ),
        tests_passed=False,
    )

    assert measured.text == "broken\n"


def test_what_the_build_printed_is_kept_though_it_belongs_to_no_package() -> None:
    build_output = {
        "ImportPath": "example.com/orders [example.com/orders.test]",
        "Action": "build-output",
        "Output": "a_test.go:5:41: cannot use x\n",
    }
    measured = measured_tests(json.dumps(build_output) + "\n", tests_passed=False)

    assert measured.text == "a_test.go:5:41: cannot use x\n"


def test_a_line_that_is_no_event_is_kept_as_text_when_the_tests_failed() -> None:
    measured = measured_tests("plain words from the tool\n" + event("fail"), tests_passed=False)

    assert measured.text == "plain words from the tool\n"


@pytest.mark.parametrize("line", ["plain words", "[1]", "3", '{"Package": "no action"}', "{"])
def test_a_line_that_is_no_event_stops_a_run_that_passed_because_nothing_proves_what_ran(
    line: str,
) -> None:
    with pytest.raises(GoCommandError) as stopped:
        measured_tests(event("pass", "TestA") + line + "\n", tests_passed=True)

    assert stopped.value.next_action


# str.splitlines breaks here too, and go test leaves this character as it is in its JSON.
def test_a_next_line_character_in_a_tests_output_does_not_split_the_event() -> None:
    line = json.dumps(
        {"Action": "output", "Package": PACKAGE, "Test": "TestA", "Output": "a\x85b\n"},
        ensure_ascii=False,
    )

    measured = measured_tests(f"{line}\n{event('fail', 'TestA')}", tests_passed=False)

    assert measured.text == "a\x85b\n"

"""The guard in conftest.py, proved on real pytest runs that fail on purpose with a fake key."""

from pathlib import Path

import pytest

CONFTEST = Path(__file__).with_name("conftest.py")
KEY_VARIABLE = "ANTHROPIC_API_KEY"
# Built, so a failure of these tests prints the expression and not the whole value.
FAKE_KEY = "sk-ant-api03-" + "AbCdEfGh" * 11 + "-Zz"
FRAGMENT_LENGTH = 16
REPORT_MODES = {
    "default": (),
    "showlocals": ("-l",),
    "long traceback": ("--tb=long",),
    "full trace": ("--full-trace",),
    "very verbose": ("-vv",),
    "report passes": ("-rA",),
}

LEAKING_TESTS = """
from dataclasses import dataclass


@dataclass
class HoldsTheKey:
    api_key: str


@dataclass
class CutByRepr:
    before: str
    api_key: str
    after: str = "t" * 400


def check(argument):
    assert argument is None


def test_a_repr_prints_the_key(api_key):
    check(HoldsTheKey(api_key=api_key.get_secret_value()))


def test_a_helper_argument_prints_the_key(api_key):
    check(api_key.get_secret_value())


def test_a_compared_dict_prints_the_key(api_key):
    assert {"authorization": f"Bearer {api_key.get_secret_value()}"} == {}


def test_a_print_puts_the_key_in_captured_output(api_key):
    print("debug:", api_key.get_secret_value())
    assert False


def test_a_shortened_repr_prints_part_of_the_key(api_key):
    check(CutByRepr(before="b" * 70, api_key=api_key.get_secret_value()))


def test_a_clean_failure_keeps_its_message():
    assert 1 == 2, "CLEAN-FAILURE-MESSAGE"
"""


def fragments_of(key: str) -> list[str]:
    return [key[start : start + FRAGMENT_LENGTH] for start in range(len(key) - FRAGMENT_LENGTH + 1)]


@pytest.fixture
def guarded(pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch) -> pytest.Pytester:
    pytester.makeconftest(CONFTEST.read_text(encoding="utf-8"))
    monkeypatch.setenv(KEY_VARIABLE, FAKE_KEY)
    return pytester


def everything_printed(result: pytest.RunResult) -> str:
    return result.stdout.str() + result.stderr.str()


@pytest.mark.parametrize("mode", REPORT_MODES.values(), ids=REPORT_MODES.keys())
def test_no_fragment_of_the_key_survives_a_failing_run(
    guarded: pytest.Pytester, mode: tuple[str, ...]
) -> None:
    guarded.makepyfile(LEAKING_TESTS)

    result = guarded.runpytest_inprocess(*mode)

    result.assert_outcomes(failed=6)
    printed = everything_printed(result)
    assert "[REDACTED]" in printed, "the guard proves nothing unless it had a key to remove"
    assert "CLEAN-FAILURE-MESSAGE" in printed
    assert [fragment for fragment in fragments_of(FAKE_KEY) if fragment in printed] == []


def test_a_key_passed_through_a_fixture_failure_is_scrubbed_too(guarded: pytest.Pytester) -> None:
    guarded.makepyfile(
        """
        import pytest

        @pytest.fixture
        def broken(api_key):
            raise ValueError(f"bad value {api_key.get_secret_value()}")

        def test_never_runs(broken):
            pass
        """
    )

    result = guarded.runpytest_inprocess()

    result.assert_outcomes(errors=1)
    assert [f for f in fragments_of(FAKE_KEY) if f in everything_printed(result)] == []


def test_the_key_leaves_the_environment_before_any_test_runs(guarded: pytest.Pytester) -> None:
    guarded.makepyfile(
        f"""
        import os

        def test_the_environment_holds_no_key():
            assert {KEY_VARIABLE!r} not in os.environ
        """
    )

    guarded.runpytest_inprocess().assert_outcomes(passed=1)


def test_tests_get_the_key_as_a_secret(guarded: pytest.Pytester) -> None:
    guarded.makepyfile(
        """
        from pydantic import SecretStr

        def test_a_secret_str(api_key):
            assert isinstance(api_key, SecretStr)
        """
    )

    guarded.runpytest_inprocess().assert_outcomes(passed=1)


def test_a_test_can_ask_whether_text_holds_the_key(guarded: pytest.Pytester) -> None:
    guarded.makepyfile(
        """
        def test_whole_and_part(api_key, holds_api_key):
            key = api_key.get_secret_value()
            assert holds_api_key(f"header: {key}")
            assert holds_api_key(f"cut: {key[20:60]}...")
            assert not holds_api_key("no key in this text")
        """
    )

    guarded.runpytest_inprocess().assert_outcomes(passed=1)


def test_skips_and_expected_failures_still_report_with_a_key_set(guarded: pytest.Pytester) -> None:
    guarded.makepyfile(
        """
        import pytest

        def test_skipped():
            pytest.skip("not today")

        @pytest.mark.xfail
        def test_expected_failure():
            assert False
        """
    )

    result = guarded.runpytest_inprocess("-rsx")

    result.assert_outcomes(skipped=1, xfailed=1)
    result.stdout.fnmatch_lines(["*not today*"])


def test_a_run_without_a_key_tells_the_user_how_to_get_one(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    pytester.makeconftest(CONFTEST.read_text(encoding="utf-8"))
    monkeypatch.delenv(KEY_VARIABLE, raising=False)
    pytester.makepyfile("def test_needs_the_key(api_key):\n    pass\n")

    result = pytester.runpytest_inprocess()

    result.assert_outcomes(errors=1)
    result.stdout.fnmatch_lines(["*make eval*"])


def test_a_run_without_a_key_leaves_failure_reports_as_pytest_wrote_them(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    pytester.makeconftest(CONFTEST.read_text(encoding="utf-8"))
    monkeypatch.delenv(KEY_VARIABLE, raising=False)
    pytester.makepyfile(test_a="def test_fails():\n    assert 1 == 2, 'plain failure'\n")

    result = pytester.runpytest_inprocess()

    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines(["FAILED *test_fails - AssertionError: plain failure*"])

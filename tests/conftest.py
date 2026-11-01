"""Keeps the API key out of whatever pytest prints, whichever test goes wrong.

The key leaves the environment before any test runs, tests get it masked in a SecretStr, and every
report is scrubbed of any stretch of the key, so a truncated repr is caught too.

pytest -s prints straight to the terminal, past these reports, so never use it with the key set.
"""

import os
import re
from collections.abc import Callable

import pytest
from pydantic import SecretStr

KEY_VARIABLE = "ANTHROPIC_API_KEY"
REDACTED = "[REDACTED]"
# Shorter than any fragment worth having, and longer than the public prefix sk-ant-api03-.
FRAGMENT_LENGTH = 16
GUARD = pytest.StashKey["KeyGuard"]()


class KeyGuard:
    """Holds the key masked, and its fragments only in a compiled pattern, for no repr to print."""

    def __init__(self, key: SecretStr) -> None:
        self.key = key
        self._fragments = _fragment_pattern(key) if key else None

    def holds_key(self, text: str) -> bool:
        return self._fragments is not None and self._fragments.search(text) is not None

    def scrub(self, text: str) -> str:
        return self._fragments.sub(REDACTED, text) if self._fragments else text

    # tryfirst, because the reporters that write files read a report as soon as they are called.
    @pytest.hookimpl(tryfirst=True)
    def pytest_runtest_logreport(self, report: pytest.TestReport) -> None:
        if self._fragments is None:
            return
        report.longrepr = self._scrubbed_failure(report.longrepr)
        report.sections = [(name, self.scrub(text)) for name, text in report.sections]

    # A skip is a tuple, which pytest folds by position, so it has to stay one.
    def _scrubbed_failure(self, longrepr: object) -> object:
        if isinstance(longrepr, tuple):
            return tuple(self.scrub(part) if isinstance(part, str) else part for part in longrepr)
        return None if longrepr is None else self.scrub(str(longrepr))


# Built in one expression, so no frame holds a plain fragment for --showlocals to print.
def _fragment_pattern(key: SecretStr) -> re.Pattern[str]:
    length = min(FRAGMENT_LENGTH, len(key))
    starts = range(len(key) - length + 1)
    return re.compile(
        "(?:"
        + "|".join(re.escape(key.get_secret_value()[start : start + length]) for start in starts)
        + r")+[\w-]*"
    )


def pytest_configure(config: pytest.Config) -> None:
    guard = KeyGuard(SecretStr(os.environ.pop(KEY_VARIABLE, "")))
    config.stash[GUARD] = guard
    config.pluginmanager.register(guard, "key-guard")


@pytest.fixture
def api_key(request: pytest.FixtureRequest) -> SecretStr:
    key = request.config.stash[GUARD].key
    if not key:
        pytest.fail(f"{KEY_VARIABLE} is not set: run these tests with make eval, not pytest")
    return key


@pytest.fixture
def holds_api_key(request: pytest.FixtureRequest) -> Callable[[str], bool]:
    return request.config.stash[GUARD].holds_key

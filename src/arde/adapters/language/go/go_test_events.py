"""What go test -json says ran. An exit code says only that no test failed.

A package whose every test skipped prints "ok", so the plain form cannot tell a pass from nothing
having run. Each line of the stream is one event.
"""

from collections import Counter

from pydantic import BaseModel, Field, ValidationError

from arde.adapters.language.go.errors import GoCommandError
from arde.domain.base import Frozen, printable_name

_TEST_ENDINGS = ("pass", "fail", "skip")


class _Event(BaseModel):
    """One event. A build's events name an import path and no package, and neither name a test."""

    action: str = Field(alias="Action")
    package: str = Field(alias="Package", default="")
    test: str = Field(alias="Test", default="")
    output: str = Field(alias="Output", default="")

    @property
    def test_name(self) -> str:
        return f"{self.package}.{self.test}"

    @property
    def is_top_level(self) -> bool:
        return "/" not in self.test


class MeasuredTests(Frozen):
    # Top-level tests that reached a pass or a fail. A subtest runs inside one of these, and a test
    # cut off by a timeout or an exit has no ending to count.
    tests_run: int
    # Tests and subtests alike, because a table can skip every case and still pass as a whole.
    tests_skipped: tuple[str, ...]
    # What a reader needs, close to what go test prints without -json.
    text: str


def measured_tests(stream: str, *, tests_passed: bool) -> MeasuredTests:
    # On the line break alone: splitlines also breaks on U+0085, which go test leaves in place.
    lines = [line for line in stream.split("\n") if line.strip()]
    events = [_event_from(line, tests_passed=tests_passed) for line in lines]
    endings = [event for event in events if event.test and event.action in _TEST_ENDINGS]
    skipped = {event.test_name for event in endings if event.action == "skip"}
    return MeasuredTests(
        tests_run=sum(1 for e in endings if e.action != "skip" and e.is_top_level),
        tests_skipped=tuple(sorted(printable_name(name) for name in skipped)),
        text="".join(event.output for event in _without_names_that_only_passed(events)),
    )


# One name can run twice in a package, once from its own test files and once from its external
# _test package, and go test names both by the package's path. Run in parallel, their events
# interleave, so no event says which run it belongs to. A name's text is left out only when every
# run of it passed, and kept whole otherwise: a passing run's lines beside a failure cost a little
# room, and a lost failure costs its reason. A skip keeps its text, because its reason says what
# the run was missing, and so does a run that never ended.
def _without_names_that_only_passed(events: list[_Event]) -> list[_Event]:
    only_passed = _names_whose_every_run_passed(events)
    return [event for event in events if not event.test or event.test_name not in only_passed]


def _names_whose_every_run_passed(events: list[_Event]) -> set[str]:
    actions: dict[str, Counter[str]] = {}
    for event in events:
        if event.test:
            actions.setdefault(event.test_name, Counter())[event.action] += 1
    return {
        name
        for name, counted in actions.items()
        if counted["pass"] >= max(counted["run"], 1) and not counted["fail"] and not counted["skip"]
    }


# A failed run may hold the tool's own words. A run that passed may not: every count here rests on
# that run's events, so one line that cannot be read leaves nothing to say what ran.
def _event_from(line: str, *, tests_passed: bool) -> _Event:
    try:
        return _Event.model_validate_json(line)
    except ValidationError as error:
        if tests_passed:
            raise GoCommandError(
                "The tests passed, but go test printed something that is not one of its events, "
                "so what ran cannot be read.",
                next_action="Run go test -json by hand in the repo to see why, then run again.",
            ) from error
        return _Event(Action="output", Output=f"{line}\n")

"""What the application may catch to tell a bad profile from a broken machine."""

import pytest

from arde.adapters.language.go.errors import (
    BadPackageError,
    BadPatternError,
    GoCommandError,
    GoinspectError,
)
from arde.adapters.language.go.inventory_builder import NoRoutesError, RouteNotUnderstoodError
from arde.application.ports import ProfileProblem
from arde.domain.errors import ArdeError
from arde.domain.report import ExitCode


def test_a_profile_problem_asks_for_input_and_names_the_next_action() -> None:
    problem = ProfileProblem("the pattern matched nothing", next_action="Correct the pattern.")

    assert isinstance(problem, ArdeError)
    assert problem.exit_code == ExitCode.DISCOVERY_NEEDS_INPUT
    assert problem.next_action == "Correct the pattern."


@pytest.mark.parametrize(
    "error", [NoRoutesError, RouteNotUnderstoodError, BadPatternError, BadPackageError]
)
def test_each_thing_a_better_profile_could_fix_is_a_profile_problem(
    error: type[ArdeError],
) -> None:
    assert issubclass(error, ProfileProblem)


@pytest.mark.parametrize("error", [GoinspectError, GoCommandError])
def test_a_broken_repo_or_machine_is_not_one_because_the_run_must_stop(
    error: type[ArdeError],
) -> None:
    assert not issubclass(error, ProfileProblem)

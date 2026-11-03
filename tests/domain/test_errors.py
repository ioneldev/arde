"""ArdeError is the one exception the pipeline catches, so it must carry what the CLI prints."""

import pytest

from arde.domain.errors import ArdeError, SourceError
from arde.domain.report import ExitCode


def test_error_exposes_its_next_action_and_exit_code() -> None:
    error = ArdeError(
        "Budget of $10.00 reached",
        next_action="Raise the cap with --max-usd",
        exit_code=ExitCode.BUDGET_EXCEEDED,
    )

    assert error.next_action == "Raise the cap with --max-usd"
    assert error.exit_code is ExitCode.BUDGET_EXCEEDED


def test_error_message_is_readable() -> None:
    error = ArdeError("Budget of $10.00 reached", next_action="n", exit_code=ExitCode.OK)

    assert str(error) == "Budget of $10.00 reached"


def test_a_subclass_is_caught_as_an_arde_error() -> None:
    class DiscoveryStuck(ArdeError):
        pass

    with pytest.raises(ArdeError, match="no main package") as caught:
        raise DiscoveryStuck(
            "no main package",
            next_action="Name it in the override file",
            exit_code=ExitCode.DISCOVERY_NEEDS_INPUT,
        )

    assert caught.value.exit_code is ExitCode.DISCOVERY_NEEDS_INPUT


def test_a_source_error_is_bad_usage_and_names_its_next_action() -> None:
    error = SourceError("No folder at ./nowhere.", next_action="Check the path, then run again.")

    assert isinstance(error, ArdeError)
    assert error.exit_code is ExitCode.BAD_USAGE
    assert error.next_action == "Check the path, then run again."

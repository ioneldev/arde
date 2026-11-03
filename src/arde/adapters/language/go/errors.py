"""The errors of the Go language adapter that other files raise or name."""

from arde.application.ports import ProfileProblem
from arde.domain.errors import ArdeError
from arde.domain.report import ExitCode


class GoCommandError(ArdeError):
    """A Go command could not give the answer a caller needs."""

    def __init__(self, message: str, *, next_action: str) -> None:
        super().__init__(message, next_action=next_action, exit_code=ExitCode.CHECKS_FAILED)


class GoinspectError(GoCommandError):
    """The helper, which is a Go command of ARDE's own, could not be built or could not answer."""


class BadPatternError(ProfileProblem):
    def __init__(self, reason: str) -> None:
        super().__init__(
            f"A pattern of the profile cannot be used: {reason}",
            next_action="Correct that pattern in the profile, then run again.",
        )


class BadPackageError(ProfileProblem):
    def __init__(self, reason: str) -> None:
        super().__init__(
            reason,
            next_action=(
                "Correct main_package in the profile to the folder that holds func main, then run "
                "again."
            ),
        )

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


class RenderError(ArdeError):
    """The renderer cannot write what the facts call for without writing something else too."""

    def __init__(self, message: str, *, next_action: str) -> None:
        super().__init__(message, next_action=next_action, exit_code=ExitCode.CHECKS_FAILED)


class NoMainFunctionError(ProfileProblem):
    def __init__(self, main_file: str) -> None:
        super().__init__(
            f"main_file {main_file} holds no func main, so the API's own annotations have no "
            "place to go.",
            next_action="Set main_file in the profile to the file that holds func main.",
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

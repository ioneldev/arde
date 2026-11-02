"""The error every Go command raises when it cannot give the answer a caller needs."""

from arde.domain.errors import ArdeError
from arde.domain.report import ExitCode


class GoCommandError(ArdeError):
    def __init__(self, message: str, *, next_action: str) -> None:
        super().__init__(message, next_action=next_action, exit_code=ExitCode.CHECKS_FAILED)

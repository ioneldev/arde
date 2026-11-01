"""The one exception the pipeline catches. Every user-facing error names its next action."""

from arde.domain.report import ExitCode


class ArdeError(Exception):
    def __init__(self, message: str, *, next_action: str, exit_code: ExitCode) -> None:
        super().__init__(message)
        self.next_action = next_action
        self.exit_code = exit_code

"""The one exception the pipeline catches. Every user-facing error names its next action."""

from arde.domain.report import ExitCode


class ArdeError(Exception):
    def __init__(self, message: str, *, next_action: str, exit_code: ExitCode) -> None:
        super().__init__(message)
        self.next_action = next_action
        self.exit_code = exit_code


class SourceError(ArdeError):
    """The repo to document cannot be found, read or fetched. It stops a run before any spend."""

    def __init__(self, message: str, *, next_action: str) -> None:
        super().__init__(message, next_action=next_action, exit_code=ExitCode.BAD_USAGE)

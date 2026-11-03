"""The Go tools an image ships already built, named by environment variables.

The container image builds goinspect and swag once, from the same pinned source and modules ARDE
would build them from, so a run on an empty volume needs neither a download nor a build. With the
variables unset, ARDE builds both itself, as it does natively.
"""

import os
from pathlib import Path

from arde.domain.base import printable_name
from arde.domain.errors import ArdeError
from arde.domain.report import ExitCode

GOINSPECT_VARIABLE = "ARDE_GOINSPECT"
SWAG_VARIABLE = "ARDE_SWAG"


class PrebuiltToolMissing(ArdeError):
    def __init__(self, variable: str, named: str) -> None:
        super().__init__(
            f"{variable} names {printable_name(named)}, which is not a file.",
            next_action=(
                f"Set {variable} to the built tool, or unset it so ARDE builds the tool itself, "
                "then run again."
            ),
            exit_code=ExitCode.BAD_USAGE,
        )


# An empty value counts as unset, as it does for every other variable ARDE reads.
def prebuilt_tool(variable: str) -> Path | None:
    named = os.environ.get(variable, "")
    if not named:
        return None
    if not os.path.isfile(named):
        raise PrebuiltToolMissing(variable, named)
    return Path(named)

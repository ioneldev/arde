"""gofmt, which lays out Go source the way every Go repo's format check expects."""

import tempfile
from pathlib import Path

from arde.adapters.child_env import child_environment
from arde.adapters.child_process import ChildCommand, run_child
from arde.adapters.child_text import MESSAGE_TAIL_BYTES, bounded_text
from arde.adapters.language.go.errors import GoCommandError
from arde.adapters.language.go.output_limits import GOFMT_OUTPUT_LIMIT
from arde.adapters.language.go.timeouts import GOFMT_TIMEOUT_S
from arde.domain.base import printable_name


def formatted(source: str, *, shown_as: str) -> str:
    """The source as gofmt lays it out. It must use LF line endings, as gofmt writes them.

    The source goes through a file of its own, because a child process is given no input. shown_as
    names the file in an error.
    """
    with tempfile.TemporaryDirectory(prefix="arde-gofmt-") as folder:
        path = Path(folder) / "source.go"
        path.write_bytes(source.encode("utf-8"))
        command = ChildCommand(
            argv=("gofmt", str(path)),
            cwd=Path(folder),
            timeout_s=GOFMT_TIMEOUT_S,
            output=GOFMT_OUTPUT_LIMIT,
            env=child_environment(),
        )
        outcome = run_child(command)
    if not outcome.succeeded:
        raise GoCommandError(
            f"gofmt cannot lay out {printable_name(shown_as)}: "
            f"{bounded_text(outcome.stderr.strip(), MESSAGE_TAIL_BYTES)}",
            next_action=(
                "Run gofmt on that file by hand. If it lays the file out there, ARDE's renderer "
                "wrote something gofmt cannot read: report it with the file named."
            ),
        )
    return outcome.stdout

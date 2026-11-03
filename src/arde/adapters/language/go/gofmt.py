"""gofmt, which lays out Go source the way every Go repo's format check expects."""

import re
import tempfile
from pathlib import Path

from arde.adapters.child_env import child_environment
from arde.adapters.child_process import ChildCommand, run_child
from arde.adapters.child_text import MESSAGE_TAIL_BYTES, bounded_text
from arde.adapters.language.go.errors import GoCommandError
from arde.adapters.language.go.output_limits import GOFMT_LIST_LIMIT, GOFMT_OUTPUT_LIMIT
from arde.adapters.language.go.timeouts import GOFMT_LIST_TIMEOUT_S, GOFMT_TIMEOUT_S
from arde.domain.base import printable_name

# How gofmt names a file it cannot parse: its path, then the line and column of the error.
_PARSE_ERROR = re.compile(r"(.+?\.go):\d+:\d+: ")


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


def unformatted_files(repo: Path) -> set[str]:
    """Each Go file, from the repo root, that gofmt -l . lists or cannot parse.

    That is how a repo's own format check runs it, so it reaches the folders the build leaves out,
    vendor and testdata among them. A file that does not parse fails such a check too.
    """
    command = ChildCommand(
        argv=("gofmt", "-l", "."),
        cwd=repo,
        timeout_s=GOFMT_LIST_TIMEOUT_S,
        output=GOFMT_LIST_LIMIT,
        env=child_environment(),
    )
    outcome = run_child(command)
    complaints = [line for line in outcome.stderr.splitlines() if line.strip()]
    parse_errors = [_PARSE_ERROR.match(line) for line in complaints]
    # Anything else, such as a file it cannot open, leaves the list short of that file.
    if None in parse_errors or (not outcome.succeeded and not complaints):
        raise _cannot_list(outcome.stderr)
    return set(outcome.stdout.splitlines()) | {found[1] for found in parse_errors if found}


def _cannot_list(stderr: str) -> GoCommandError:
    return GoCommandError(
        "gofmt cannot read every Go file of the repo: "
        f"{bounded_text(stderr.strip(), MESSAGE_TAIL_BYTES)}",
        next_action="Run gofmt -l . in the repo to see why, fix what it names, then run again.",
    )

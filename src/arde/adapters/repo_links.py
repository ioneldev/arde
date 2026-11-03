"""The gate that keeps a repo's links from leading out of it.

A build tool follows links and quotes a file it cannot parse in its errors, so a link can put any
file the user can read into ARDE's output with no repo code running. Every link is judged, because
a list of file names would have to know every carrier. This file knows no language.
"""

import errno
import os
import stat
from pathlib import Path

from arde.domain.base import printable_end_of_path, printable_name
from arde.domain.errors import ArdeError
from arde.domain.report import ExitCode

# A repo can hold any number of links, and a person reads this message.
MOST_LINKS_NAMED = 5


class LinkLeavesRepoError(ArdeError):
    """Names the links and never their targets, because a target's path can say what it holds."""

    def __init__(self, links: list[str]) -> None:
        named = ", ".join(printable_name(link) for link in links[:MOST_LINKS_NAMED])
        super().__init__(
            f"{len(links)} link(s) in the repo lead out of it, such as {named}.",
            next_action=(
                "Remove each link, or replace it with the file it stands for, then run again."
            ),
            exit_code=ExitCode.BAD_USAGE,
        )


class RepoNotReadableError(ArdeError):
    def __init__(self, problem: str, *, next_action: str) -> None:
        super().__init__(problem, next_action=next_action, exit_code=ExitCode.BAD_USAGE)


class FolderNotReadError(Exception):
    """A folder the gate could not list, or a name in it whose kind it could not read."""

    def __init__(self, folder: str, cause: OSError) -> None:
        super().__init__(folder, cause)
        self.folder = folder
        self.cause = cause


def require_no_link_leaves(repo: Path) -> None:
    try:
        links = links_leaving(repo)
    except FolderNotReadError as unread:
        raise _not_readable(unread, root=os.path.realpath(repo)) from unread.cause
    if links:
        raise LinkLeavesRepoError(links)


# Each next action is one a person can follow: a name that went away is a repo still being written,
# a name too long is a repo too deep for where it sits, and anything else is the folder named.
def _not_readable(unread: FolderNotReadError, *, root: str) -> RepoNotReadableError:
    cause = unread.cause
    reason = cause.strerror or str(cause)
    failed = str(cause.filename or unread.folder)
    if cause.errno == errno.ENOENT and failed != root:
        return RepoNotReadableError(
            f"The repo changed while ARDE read it: {printable_end_of_path(failed)} went away.",
            next_action="Run again when nothing is writing to the repo.",
        )
    if cause.errno == errno.ENAMETOOLONG:
        return RepoNotReadableError(
            f"A path in the repo is too long to read: {printable_end_of_path(failed)}.",
            next_action="Move the repo to a shorter path, then run again.",
        )
    folder = printable_end_of_path(unread.folder)
    return RepoNotReadableError(
        f"Cannot read the folder {folder}, so the links in it cannot be checked: {reason}.",
        next_action=f"Make the folder {folder} readable, or remove it, then run again.",
    )


def links_leaving(repo: Path) -> list[str]:
    """The path from the root of every link whose real path is outside the repo's real path.

    .git is walked like any folder: a go.work can name a module inside it. A folder that cannot be
    listed, or a name in it that cannot be read, raises FolderNotReadError.
    """
    root = os.path.realpath(repo)
    found: list[str] = []
    for folder, subfolders, files in os.walk(root, onerror=_stop_the_walk):
        for name in (*subfolders, *files):
            path = os.path.join(folder, name)
            if _is_a_link(path, folder=folder) and not is_inside(os.path.realpath(path), root):
                found.append(os.path.relpath(path, root))
    return sorted(found)


# lstat itself, because os.path.islink answers False for a path it cannot reach, and a folder that
# can be listed but not entered would hide a link that way.
def _is_a_link(path: str, *, folder: str) -> bool:
    try:
        return stat.S_ISLNK(os.lstat(path).st_mode)
    except OSError as error:
        raise FolderNotReadError(folder, error) from error


# A folder that cannot be listed could hide a link, so it must not be passed over.
def _stop_the_walk(error: OSError) -> None:
    raise FolderNotReadError(str(error.filename), error) from error


def is_inside(real_path: str, real_root: str) -> bool:
    return os.path.commonpath((real_path, real_root)) == real_root

"""The gate that keeps a repo's links from leading out of it.

A build tool follows links and quotes a file it cannot parse in its errors, so a link can put any
file the user can read into ARDE's output with no repo code running. Every link is judged, because
a list of file names would have to know every carrier. This file knows no language.
"""

import os
import stat
from pathlib import Path

from arde.adapters.child_process import describe_os_error
from arde.domain.base import printable_name
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
    def __init__(self, error: OSError) -> None:
        super().__init__(
            "Cannot read the whole repo, so its links cannot be checked: "
            f"{printable_name(describe_os_error(error))}.",
            next_action="Make that folder readable, or remove it, then run again.",
            exit_code=ExitCode.BAD_USAGE,
        )


def require_no_link_leaves(repo: Path, *, under: str = "") -> None:
    try:
        links = links_leaving(repo, under=under)
    except OSError as error:
        raise RepoNotReadableError(error) from error
    if links:
        raise LinkLeavesRepoError(links)


def links_leaving(repo: Path, *, under: str = "") -> list[str]:
    """The path from the root of every link whose real path is outside the repo's real path.

    .git is walked like any folder: a go.work can name a module inside it. under names one folder
    of the repo to walk instead of all of it, and each link there is still judged against the
    whole repo.
    """
    root = os.path.realpath(repo)
    top = os.path.join(root, under)
    # os.walk would follow the top folder if it were a link, and walk whatever it leads to.
    if under and _is_a_link(top) and not is_inside(os.path.realpath(top), root):
        return [under]
    found: list[str] = []
    for folder, subfolders, files in os.walk(top, onerror=_stop_the_walk):
        for name in (*subfolders, *files):
            path = os.path.join(folder, name)
            if _is_a_link(path) and not is_inside(os.path.realpath(path), root):
                found.append(os.path.relpath(path, root))
    return sorted(found)


# lstat itself, because os.path.islink answers False for a path it cannot reach, and a folder that
# can be listed but not entered would hide a link that way.
def _is_a_link(path: str) -> bool:
    return stat.S_ISLNK(os.lstat(path).st_mode)


# A folder that cannot be listed could hide a link, so it must not be passed over.
def _stop_the_walk(error: OSError) -> None:
    raise error


def is_inside(real_path: str, real_root: str) -> bool:
    return os.path.commonpath((real_path, real_root)) == real_root

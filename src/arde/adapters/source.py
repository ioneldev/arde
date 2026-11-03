"""Where the code to document comes from, and how it gets into ARDE's own workspace.

A URL is cloned into the workspace. A local repo is read and never written: the files git lists
are copied, so uncommitted and untracked work comes along and ignored files such as .env do not.
A local tree may come from anywhere, such as an unpacked archive, so its .git folder, its index
and its links are all treated as untrusted input. This file knows no language.
"""

import hashlib
import os
import re
import shutil
import stat
import tempfile
import unicodedata
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from arde.adapters.child_env import child_environment
from arde.adapters.child_process import (
    ChildCommand,
    ChildOutcome,
    OutputLimit,
    describe_os_error,
    run_child,
)
from arde.adapters.child_text import MESSAGE_TAIL_BYTES, bounded_text
from arde.adapters.repo_links import require_no_link_leaves
from arde.adapters.workspace import Workspace
from arde.application.ports import ResolvedSource, ResultCommit, SourceControl
from arde.domain.base import Frozen, printable_name
from arde.domain.errors import SourceError

CLONE_TIMEOUT_S = 600
# Listing a remote's branches, or reading a local repo's settings, index or line endings.
GIT_QUERY_TIMEOUT_S = 120

# A file list is no use cut short, and neither is a commit id or a URL.
GIT_REPLY_LIMIT = OutputLimit(max_bytes=64 * 1024 * 1024, when_longer="fail")
# A failing clone says what went wrong at the end.
CLONE_OUTPUT_LIMIT = OutputLimit(max_bytes=1024 * 1024, when_longer="keep_tail")

# Measured: git ls-files runs the command that core.fsmonitor names in the repo's own settings,
# and a local tree brings its settings with it.
_NO_COMMAND_FROM_THE_REPO = ("-c", "core.fsmonitor=false")

# Git would ask for a password on a terminal, and ARDE never prompts.
_GIT_VARIABLES = {"GIT_TERMINAL_PROMPT": "0"}

# Exit codes git documents for these commands.
_LS_REMOTE_FOUND_NOTHING = 2
_NO_SUCH_REMOTE = 2
_NOT_A_COMMIT = 1

# A scheme, as in https:// or file://, or the user@host:path form that git reads as SSH. Anything
# else is a path, so git's other transports, such as ext::, which runs a command, never reach it.
_SCHEME = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*://")
_SSH_SHORT_FORM = re.compile(r"[^\s/:@-][^\s/:@]*@[^\s/:]+:")

# What git ls-files --eol shows for a checkout made with core.autocrlf=true: LF in the index, CRLF
# in the working tree. A container cannot see that setting in the host's own git config.
_LF_IN_INDEX_CRLF_IN_TREE = ("i/lf", "w/crlf")
_AUTOCRLF = ("-c", "core.autocrlf=true")


class SourceRequest(Frozen):
    repo: str
    branch: str | None


def resolve_source(request: SourceRequest, workspace_home: Path) -> ResolvedSource:
    """Finds a local repo, or clones a URL into its workspace under workspace_home."""
    if _is_url(request.repo):
        return _cloned(request, workspace_home)
    return _local(request)


def _is_url(repo: str) -> bool:
    return bool(_SCHEME.match(repo) or _SSH_SHORT_FORM.match(repo))


# URL sources


def _cloned(request: SourceRequest, home: Path) -> ResolvedSource:
    origin = without_credentials(request.repo)
    repo_id = repo_id_from_origin(request.repo)
    if request.branch is not None:
        _require_branch(request.repo, branch=request.branch, origin=origin)
    workspace = Workspace(home, repo_id)
    with workspace.fresh_clone() as folder:
        _clone(request.repo, branch=request.branch, into=folder, origin=origin)
        revision = _head_commit(folder)
    return ResolvedSource(
        kind="url", origin=origin, repo_id=repo_id, root=workspace.clone_dir, revision=revision
    )


# Asked of the remote itself, because the message of a failed clone is translated and cannot be
# told apart from any other failure. It runs in an empty folder, where no repo's settings apply.
def _require_branch(url: str, *, branch: str, origin: str) -> None:
    with tempfile.TemporaryDirectory(prefix="arde-git-") as folder:
        heads = ("ls-remote", "--exit-code", "--heads", "--", url, f"refs/heads/{branch}")
        outcome = _git(heads, cwd=Path(folder))
    if outcome.exit_code == _LS_REMOTE_FOUND_NOTHING:
        raise SourceError(
            f"{origin} has no branch named {printable_name(branch)}.",
            next_action=(
                "Check the name: `git ls-remote --heads <url>` lists the branches. Then run again "
                "with --branch set to one of them, or without --branch for the default branch."
            ),
        )
    if not outcome.succeeded:
        raise _unreachable(origin, outcome)


# core.autocrlf=false, so the clone holds the bytes the repo holds whatever the user's own git
# settings say, and the setting stays in the clone for the commit a later step makes there.
def _clone(url: str, *, branch: str | None, into: Path, origin: str) -> None:
    named_branch = () if branch is None else (f"--branch={branch}",)
    arguments = (
        "clone",
        "--quiet",
        "--config",
        "core.autocrlf=false",
        *named_branch,
        "--",
        url,
        str(into),
    )
    outcome = _git(arguments, cwd=into, timeout_s=CLONE_TIMEOUT_S, output=CLONE_OUTPUT_LIMIT)
    if not outcome.succeeded:
        raise _unreachable(origin, outcome)


def _unreachable(origin: str, outcome: ChildOutcome) -> SourceError:
    return SourceError(
        f"Cannot reach {origin}: {bounded_text(outcome.output.strip(), MESSAGE_TAIL_BYTES)}",
        next_action=(
            "Check the URL, and that `git ls-remote <url>` works in your shell, then run again."
        ),
    )


# Local sources


def _local(request: SourceRequest) -> ResolvedSource:
    if request.branch is not None:
        raise SourceError(
            "--branch names the branch to clone from a URL, and this is a local path.",
            next_action=(
                "A local repo is copied as its working tree stands. Check out the branch there, "
                "then run again without --branch."
            ),
        )
    root = _existing_folder(Path(request.repo))
    _require_no_link_out_before_git_runs(root)
    _require_top_of_a_repo(root)
    origin = _origin_url(root)
    return ResolvedSource(
        kind="local",
        origin=str(root) if origin is None else without_credentials(origin),
        repo_id=_repo_id_of_folder(root) if origin is None else repo_id_from_origin(origin),
        root=root,
        revision=_head_commit(root),
    )


def _existing_folder(path: Path) -> Path:
    if not path.exists():
        raise SourceError(
            f"There is no folder at {_shown(path)}.",
            next_action=(
                "Check the path, then run again. A URL needs a scheme, as in https://, or the "
                "form user@host:path."
            ),
        )
    if not path.is_dir():
        raise SourceError(
            f"{_shown(path)} is a file, not a folder.",
            next_action="Pass the repo's top folder, then run again.",
        )
    return path.resolve()


# Git reads every file in .git, and quotes a line it cannot parse in its error: measured, a
# packed-refs that was a link to a file outside put that file's line in the message. A link in .git
# can also lead to a folder of the repo that holds a link out, so the whole tree is walked, ignored
# files included, before git runs. A .git that is a file, as in a worktree, names a folder
# elsewhere, and that folder is not walked.
def _require_no_link_out_before_git_runs(root: Path) -> None:
    require_no_link_leaves(root)


def _require_top_of_a_repo(root: Path) -> None:
    outcome = _git(("rev-parse", "--show-toplevel"), cwd=root)
    if not outcome.succeeded:
        raise SourceError(
            f"{_shown(root)} is not a git repo.",
            next_action=(
                "Pass the top folder of a git repo. For a plain folder, run `git init` there and "
                "commit, then run again."
            ),
        )
    # The same folder, not the same text: on macOS a path typed in another case still finds it.
    top = Path(outcome.stdout.removesuffix("\n"))
    if not top.samefile(root):
        raise SourceError(
            f"{_shown(root)} is inside the git repo at {_shown(top)}, not at its top.",
            next_action=f"Pass the repo's top folder, {_shown(top)}, then run again.",
        )


def _origin_url(root: Path) -> str | None:
    outcome = _git(("remote", "get-url", "origin"), cwd=root)
    if outcome.exit_code == _NO_SUCH_REMOTE:
        return None
    return _reply(outcome, asked="remote get-url origin", repo=root).strip()


def _head_commit(repo: Path) -> str:
    outcome = _git(("rev-parse", "--verify", "--quiet", "HEAD^{commit}"), cwd=repo)
    if outcome.exit_code == _NOT_A_COMMIT:
        raise SourceError(
            f"{_shown(repo)} has no commit yet.",
            next_action="Commit once, then run again. Each run records the commit it starts from.",
        )
    return _reply(outcome, asked="rev-parse HEAD", repo=repo).strip()


# Copying


def copy_tree(source: ResolvedSource, dest: Path) -> None:
    """Copies the files git lists in source.root into dest, which must not exist yet.

    A link is copied as a link, and the repo-links gate then runs on dest: a copy that followed a
    link would turn a file from outside into a plain file inside the copy, past every later check.
    """
    names = _listed_files(source.root)
    try:
        files, links = _files_and_links(names, root=source.root)
        dest.mkdir(parents=True)
        # Every file before any link, so no file can be written through a link the copy made, even
        # where this disk reads as one path two names that the repo's own disk tells apart.
        for name in files:
            _copy_file(source.root / name, dest / name)
        for name in links:
            _copy_link(source.root / name, dest / name)
    except OSError as error:
        raise SourceError(
            "Cannot copy the repo into ARDE's workspace: "
            f"{printable_name(describe_os_error(error))}.",
            next_action=(
                "Check that you can read that file and write under ARDE_HOME, then run again."
            ),
        ) from error
    require_no_link_leaves(dest)


# A folder git lists, with a slash at the end, is a repo nested inside this one and not part of
# it, and git lists none of its files.
def _listed_files(root: Path) -> list[str]:
    listing = _ask(
        (
            "-c",
            "core.quotePath=true",
            "ls-files",
            "--cached",
            "--others",
            "--exclude-standard",
            "--deduplicate",
        ),
        repo=root,
    )
    names = [_unquoted(line) for line in listing.split("\n") if line]
    for name in names:
        _require_plain_path(name.removesuffix("/"))
    files = [name for name in names if not name.endswith("/")]
    _require_one_name_per_path(files)
    return files


# With core.quotePath, git prints a name that holds a quote, a backslash, a control character or
# any byte past ASCII between double quotes, with C escapes and each such byte as three octal
# digits. So the listing is ASCII, and every name comes back exact, whatever bytes it holds.
def _unquoted(line: str) -> str:
    if not line.startswith('"'):
        return line
    # Python's string escapes include all of git's, and latin-1 turns each code point they give
    # back into the byte it stood for.
    raw = line[1:-1].encode("ascii").decode("unicode_escape").encode("latin-1")
    return os.fsdecode(raw)


# Measured: git ls-files prints whatever an index holds, and an index written by hand can hold
# ../x, an absolute path or .git/config, which git itself refuses to add. Read and written as
# given, such a path takes a file from outside the repo and puts one outside the copy.
def _require_plain_path(name: str) -> None:
    parts = name.split("/")
    if any(part in ("", ".", "..") or part.lower() == ".git" for part in parts):
        raise SourceError(
            f"The repo's index lists {printable_name(name)}, which is not a plain path inside it.",
            next_action=(
                "Git refuses to add such a path, so another tool wrote the index. Rebuild it with "
                "`git reset`, which keeps your files, then run again."
            ),
        )


# A disk that ignores case, as macOS does by default, or Unicode form reads A and a/x as one
# folder, and a/x is then written through the link A into wherever it leads. The repo's own disk
# may tell them apart, so the listing is judged, not either disk.
def _require_one_name_per_path(names: list[str]) -> None:
    first_seen: dict[str, tuple[str, str]] = {}
    for name in names:
        parts = name.split("/")
        for depth in range(1, len(parts) + 1):
            spelling = "/".join(parts[:depth])
            seen, listed = first_seen.setdefault(_folded(spelling), (spelling, name))
            if seen != spelling:
                raise SourceError(
                    f"The repo lists {printable_name(listed)} and {printable_name(name)}, which "
                    "are one path on a disk that ignores case or Unicode form.",
                    next_action="Rename one of them in the repo, then run again.",
                )


# Unicode's caseless match: canonical decomposition, case folding, then one composed form.
def _folded(path: str) -> str:
    return unicodedata.normalize("NFC", unicodedata.normalize("NFD", path).casefold())


def _files_and_links(names: list[str], *, root: Path) -> tuple[list[str], list[str]]:
    """The listed names that are regular files in the working tree, and those that are links.

    A name gone from the working tree is left out, and so is a folder: git lists a submodule as
    one name, and none of its files.
    """
    files: list[str] = []
    links: list[str] = []
    for name in names:
        path = root / name
        if _behind_a_link(root, name) or not os.path.lexists(path):
            continue
        mode = os.lstat(path).st_mode
        if stat.S_ISREG(mode):
            files.append(name)
        elif stat.S_ISLNK(mode):
            links.append(name)
        elif not stat.S_ISDIR(mode):
            # Such as a pipe, which a read would wait on forever.
            raise SourceError(
                f"{printable_name(name)} is neither a file nor a link, so it cannot be copied.",
                next_action="Remove it, or add it to .gitignore, then run again.",
            )
    return files, links


def _copy_file(original: Path, copy: Path) -> None:
    copy.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(original, copy, follow_symlinks=False)


def _copy_link(original: Path, copy: Path) -> None:
    copy.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.symlink(os.readlink(original), copy)
    except OSError as error:
        # os.symlink names the link's target first, and a message never shows where a link leads.
        raise OSError(error.errno, error.strerror, str(copy)) from error


# Git counts a path below a link as gone from the working tree, and so does the copy. Read through
# the link, it would take a file from elsewhere, and written through the copied link, it would
# land outside the copy.
def _behind_a_link(root: Path, name: str) -> bool:
    folder = root
    for part in name.split("/")[:-1]:
        folder = folder / part
        if folder.is_symlink():
            return True
    return False


def ensure_git_eol_config(repo: Path) -> tuple[str, ...]:
    """The git -c arguments under which a CRLF checkout of LF files does not look changed.

    The design measured 16 files that looked changed without them, and none with them.
    """
    listing = _ask(("-c", "core.quotePath=true", "ls-files", "--eol"), repo=repo)
    for line in listing.split("\n"):
        line_endings = tuple(line.partition("\t")[0].split()[:2])
        if line_endings == _LF_IN_INDEX_CRLF_IN_TREE:
            return _AUTOCRLF
    return ()


# Committing a run's result in the workspace clone

# ARDE's own identity, and none of the user's settings that would sign the commit or run a hook,
# so the commit needs nothing from the machine it runs on and runs no program the user set up.
_COMMIT_SETTINGS = (
    "-c",
    "user.name=ARDE",
    "-c",
    "user.email=arde@localhost",
    "-c",
    "commit.gpgsign=false",
    "-c",
    f"core.hooksPath={os.devnull}",
)


def commit_result(commit: ResultCommit) -> str:
    """Commits the paths on a new branch at the clone's current commit, and returns its id."""
    _ask(("checkout", "--quiet", "-b", commit.branch), repo=commit.clone)
    try:
        for path in commit.paths:
            _put_into_clone(path, commit=commit)
    except OSError as error:
        raise SourceError(
            "Cannot write the run's result into the workspace clone: "
            f"{printable_name(describe_os_error(error))}.",
            next_action="Check that you can write under ARDE_HOME, then run again.",
        ) from error
    # --force, because the result is ARDE's own files, which a repo may list in .gitignore.
    if commit.paths:
        _ask(("add", "--all", "--force", "--", *commit.paths), repo=commit.clone)
    message = ("-m", commit.message)
    _ask((*_COMMIT_SETTINGS, "commit", "--quiet", "--allow-empty", *message), repo=commit.clone)
    return _head_commit(commit.clone)


# The clone may hold a link to a folder inside it, and a file written through it would land in
# that folder instead.
def _put_into_clone(path: str, *, commit: ResultCommit) -> None:
    _require_plain_path(path)
    written, target = commit.output / path, commit.clone / path
    if _behind_a_link(commit.clone, path) or target.is_symlink():
        raise SourceError(
            f"{printable_name(path)} is behind a link in the workspace clone, so ARDE writes no "
            "result through it.",
            next_action="Replace the link with the folder or file it stands for, then run again.",
        )
    if not os.path.lexists(written):
        target.unlink(missing_ok=True)
    elif os.path.islink(written):
        _copy_link(written, target)
    else:
        _copy_file(written, target)


class GitSourceControl(SourceControl):
    def copy_source(self, source: ResolvedSource, dest: Path) -> None:
        copy_tree(source, dest)

    def commit_result(self, commit: ResultCommit) -> str:
        return commit_result(commit)


# The return type is the static proof that GitSourceControl satisfies the port.
def git_source_control() -> SourceControl:
    return GitSourceControl()


# Repo ids


def repo_id_from_origin(origin: str) -> str:
    """The same id for every spelling of one remote: over HTTPS or SSH, with or without a user,
    a password, .git or a trailing slash. A fork has another path, so it gets another id."""
    host, path = _host_and_path(origin)
    path = path.strip("/").removesuffix(".git").rstrip("/")
    return _repo_id(name=path.rpartition("/")[2] or host, key=f"{host}/{path}")


def _host_and_path(origin: str) -> tuple[str, str]:
    if _SSH_SHORT_FORM.match(origin):
        user_and_host, _, path = origin.partition(":")
        return user_and_host.rpartition("@")[2].lower(), path
    parts = urlsplit(origin)
    return parts.hostname or "", parts.path


def _repo_id_of_folder(root: Path) -> str:
    return _repo_id(name=root.name, key=str(root))


# A readable name, so a person can find the folder, and a hash, so two repos of one name differ.
# The name keeps no slash and starts with no dot, so it is one plain folder name.
def _repo_id(*, name: str, key: str) -> str:
    readable = re.sub(r"[^A-Za-z0-9._-]+", "-", name).lstrip(".-")[:40] or "repo"
    digest = hashlib.sha256(key.encode("utf-8", errors="surrogateescape")).hexdigest()
    return f"{readable}-{digest[:12]}"


def without_credentials(url: str) -> str:
    """The URL with any user name and password left out, so a trace or a message can show it."""
    if not _SCHEME.match(url):
        return url
    parts = urlsplit(url)
    if "@" not in parts.netloc:
        return url
    return urlunsplit(parts._replace(netloc=parts.netloc.rpartition("@")[2]))


# Running git


def _ask(arguments: tuple[str, ...], *, repo: Path) -> str:
    return _reply(_git(arguments, cwd=repo), asked=" ".join(arguments), repo=repo)


def _reply(outcome: ChildOutcome, *, asked: str, repo: Path) -> str:
    if outcome.succeeded:
        return outcome.stdout
    raise SourceError(
        f"`git {asked}` failed in {_shown(repo)}: "
        f"{bounded_text(outcome.output.strip(), MESSAGE_TAIL_BYTES)}",
        next_action="Run that command by hand in that folder to see why, fix it, then run again.",
    )


def _git(
    arguments: tuple[str, ...],
    *,
    cwd: Path,
    timeout_s: float = GIT_QUERY_TIMEOUT_S,
    output: OutputLimit = GIT_REPLY_LIMIT,
) -> ChildOutcome:
    command = ChildCommand(
        argv=("git", *_NO_COMMAND_FROM_THE_REPO, *arguments),
        cwd=cwd,
        timeout_s=timeout_s,
        output=output,
        env={**child_environment(), **_GIT_VARIABLES},
    )
    return run_child(command)


def _shown(path: Path) -> str:
    return printable_name(str(path))

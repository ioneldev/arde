"""The folder ARDE keeps for one target repo, and the folder each run gets inside it.

    $ARDE_HOME/workspaces/<repo-id>/
      profile.yaml  profile.override.yaml  sections.yaml  render_manifest.yaml
      wiki/
      snapshot -> snapshots/<id>    the tree after the last successful run
      clone -> clones/<id>          the clone of a URL source
      runs/<run-id>/                one run: input/, output/, the trace and the logs

The snapshot and the clone are links, because a link can be replaced in one rename and a folder
that holds files cannot. A run stopped at any instant leaves the last one whole.
"""

import os
import secrets
import shutil
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from arde.adapters.child_process import describe_os_error
from arde.application.ports import RunStore
from arde.domain.base import printable_name
from arde.domain.errors import ArdeError
from arde.domain.report import ExitCode

HOME_VARIABLE = "ARDE_HOME"


# Absolute, because every child process runs in another folder.
def arde_home() -> Path:
    """$ARDE_HOME, or ~/.arde when it is unset or empty."""
    configured = os.environ.get(HOME_VARIABLE)
    return Path(configured).absolute() if configured else Path.home() / ".arde"


class WorkspaceError(ArdeError):
    def __init__(self, error: OSError) -> None:
        super().__init__(
            f"Cannot write to ARDE's workspace: {printable_name(describe_os_error(error))}.",
            next_action=(
                f"Make that folder writable, or set {HOME_VARIABLE} to a folder you can write to, "
                "then run again."
            ),
            exit_code=ExitCode.BAD_USAGE,
        )


class Workspace(RunStore):
    def __init__(self, home: Path, repo_id: str) -> None:
        self.root = home / "workspaces" / repo_id

    @property
    def profile_path(self) -> Path:
        return self.root / "profile.yaml"

    @property
    def override_path(self) -> Path:
        return self.root / "profile.override.yaml"

    @property
    def wiki_dir(self) -> Path:
        return self.root / "wiki"

    @property
    def sections_path(self) -> Path:
        return self.root / "sections.yaml"

    @property
    def manifest_path(self) -> Path:
        return self.root / "render_manifest.yaml"

    @property
    def snapshot_dir(self) -> Path:
        return self.root / "snapshot"

    @property
    def clone_dir(self) -> Path:
        return self.root / "clone"

    def new_run_dir(self) -> Path:
        run_dir = self.root / "runs" / _new_id()
        with _writing():
            run_dir.mkdir(parents=True)
        return run_dir

    def input_copy(self, run_dir: Path) -> Path:
        return run_dir / "input"

    def output_copy(self, run_dir: Path) -> Path:
        return run_dir / "output"

    def has_snapshot(self) -> bool:
        return self.snapshot_dir.is_dir()

    def save_snapshot(self, tree: Path) -> None:
        with _writing():
            version = _new_version(self.root / "snapshots")
            os.rename(tree, version)
            _make_current(self.snapshot_dir, version)

    @contextmanager
    def fresh_clone(self) -> Iterator[Path]:
        """An empty folder to clone into. It becomes clone_dir only when the body succeeds."""
        with _writing():
            folder = _new_version(self.root / "clones")
            folder.mkdir()
        yield folder
        with _writing():
            _make_current(self.clone_dir, folder)


# Sorted by time, and unique when two start in the same second.
def _new_id() -> str:
    return f"{datetime.now(UTC):%Y%m%dT%H%M%SZ}-{secrets.token_hex(3)}"


def _new_version(versions: Path) -> Path:
    versions.mkdir(parents=True, exist_ok=True)
    return versions / _new_id()


# Everything else is removed before the swap and the one before stays, so a removal that fails
# fails the save while the last version is still current, and nothing can fail after the swap.
def _make_current(link: Path, version: Path) -> None:
    current = Path(os.readlink(link)).name if link.is_symlink() else None
    for other in version.parent.iterdir():
        if other.name not in (current, version.name):
            _remove(other)
    staged = version.parent / f".{version.name}.link"
    staged.symlink_to(os.path.relpath(version, link.parent))
    os.replace(staged, link)


def _remove(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink()


@contextmanager
def _writing() -> Iterator[None]:
    try:
        yield
    except OSError as error:
        raise WorkspaceError(error) from error

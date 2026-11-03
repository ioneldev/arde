"""What changed since the last successful run, so a run redoes only the work the change touches.

The snapshot is the output of that run, ARDE's own comments, tags, pages and spec included, so
the comparison passes over what ARDE wrote: a file that differs only in comments counts as
unchanged, the README is compared without the link ARDE added, and a file only the snapshot holds
in the docs folder is ARDE's own. Declarations and routes are compared by the language's own
hashes, which ignore comments.
"""

import difflib
import os
import shutil
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from arde.application.agents.docs_writer import (
    DOCS_FOLDER,
    GENERATED_MARKER,
    SECTION_FILES,
    with_readme_link,
)
from arde.application.ports import LanguageAdapter, ProfileProblem, RenderManifest, RouteHashes
from arde.domain.change import ChangeReport, stale_fact_ids
from arde.domain.facts import Fact, ProbeCitation, Topic, Wiki
from arde.domain.inventory import Inventory
from arde.domain.profile import RepoProfile

# Far more than Explore is shown, which is the first 20,000 characters.
DIFF_LIMIT = 200_000
_README = "readme.md"


@dataclass(frozen=True)
class SnapshotComparison:
    snapshot: Path
    repo: Path
    profile: RepoProfile
    # Read from repo, with the hash of each of its routes.
    inventory: Inventory
    hashes: RouteHashes
    # What the last run saved, whose facts may now be stale.
    wiki: Wiki
    # The comment lines the last run wrote, which the snapshot's side of the diff leaves out.
    manifest: RenderManifest


@dataclass(frozen=True)
class RunChanges:
    stale_ids: tuple[str, ...]
    # None on a first run, when there is nothing to compare with.
    report: ChangeReport | None


NO_CHANGES = RunChanges(stale_ids=(), report=None)


def changes_since_snapshot(comparison: SnapshotComparison, adapter: LanguageAdapter) -> RunChanges:
    """The facts whose links changed since the snapshot, and a report naming what changed."""
    changed_files = _changed_files(comparison, adapter)
    report = ChangeReport(
        changed_files=tuple(changed_files),
        changed_declarations=tuple(
            adapter.changed_declarations(comparison.snapshot, comparison.repo)
        ),
        **_inventory_changes(comparison, adapter),
        changed_probes=(),
        responses_changed=False,
        diff=_diff(changed_files, comparison),
    )
    return RunChanges(stale_ids=stale_fact_ids(comparison.wiki, report), report=report)


def changed_topics(last: Wiki, new: Wiki) -> frozenset[Topic]:
    """The topics of the facts that were added, removed or changed, or whose probe answers anew."""
    last_facts = {fact.id: fact for fact in last.facts}
    new_facts = {fact.id: fact for fact in new.facts}
    last_probes = {(probe.method, probe.path_and_query): probe for probe in last.probes}
    reanswered = {
        (probe.method, probe.path_and_query)
        for probe in new.probes
        if last_probes.get((probe.method, probe.path_and_query)) not in (None, probe)
    }
    changed = [fact for fact in last_facts.values() if new_facts.get(fact.id) != fact]
    changed += [fact for fact in new_facts.values() if last_facts.get(fact.id) != fact]
    changed += [fact for fact in new_facts.values() if _cites_any(fact, reanswered)]
    return frozenset(fact.topic for fact in changed)


def _cites_any(fact: Fact, requests: set[tuple[str, str]]) -> bool:
    return any(
        (evidence.method, evidence.path_and_query) in requests
        for evidence in fact.evidence
        if isinstance(evidence, ProbeCitation)
    )


def restore_last_pages(snapshot: Path, repo: Path) -> None:
    """Copies each page the last run wrote into the repo, where the repo holds no page of that name.

    A clone of a URL never holds ARDE's pages, and without them every section would be written
    again. Only a plain file that starts with ARDE's mark is copied, and never through a link.
    """
    if (repo / DOCS_FOLDER).is_symlink():
        return
    for name in SECTION_FILES.values():
        page, last = repo / DOCS_FOLDER / name, snapshot / DOCS_FOLDER / name
        if os.path.lexists(page) or last.is_symlink() or not _is_arde_page(last):
            continue
        page.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(last, page)


def _is_arde_page(path: Path) -> bool:
    marker = GENERATED_MARKER.encode("utf-8")
    try:
        with path.open("rb") as page:
            return page.read(len(marker)) == marker
    except (FileNotFoundError, IsADirectoryError):
        return False


# Files


def _changed_files(comparison: SnapshotComparison, adapter: LanguageAdapter) -> list[str]:
    old_files, new_files = _files(comparison.snapshot), _files(comparison.repo)
    changed = []
    for name in sorted(old_files | new_files):
        old, new = comparison.snapshot / name, comparison.repo / name
        if name not in new_files:
            if not _is_in_docs_folder(name):
                changed.append(name)
        elif name not in old_files or not _same(old, new, adapter=adapter):
            changed.append(name)
    return changed


def _files(root: Path) -> set[str]:
    found: set[str] = set()
    for folder, subfolders, names in os.walk(root):
        here = Path(folder)
        links = [name for name in subfolders if (here / name).is_symlink()]
        found.update((here / name).relative_to(root).as_posix() for name in [*names, *links])
    return found


def _is_in_docs_folder(name: str) -> bool:
    return name.split("/", 1)[0] == DOCS_FOLDER


def _same(old: Path, new: Path, *, adapter: LanguageAdapter) -> bool:
    if _identical(old, new):
        return True
    if old.is_symlink() or new.is_symlink():
        return False
    if new.name.lower() == _README:
        return with_readme_link(new.read_bytes()) == old.read_bytes()
    return adapter.is_comment_only(old, new)


# Inventory and routes


def _inventory_changes(
    comparison: SnapshotComparison, adapter: LanguageAdapter
) -> dict[str, tuple[str, ...]]:
    try:
        last = adapter.extract_inventory(comparison.snapshot, comparison.profile)
    except ProfileProblem:
        # The profile was found again and no longer reads the last tree, so nothing can be
        # compared, and every route counts as changed.
        last = comparison.inventory.model_copy(update={"routes": ()})
    last_hashes = adapter.route_fingerprints(
        comparison.snapshot, last.routes, main_package=comparison.profile.main_package
    )
    old, new = set(_items(last)), set(_items(comparison.inventory))
    keys = {route.key for route in (*last.routes, *comparison.inventory.routes)}
    return {
        "added_inventory": tuple(sorted(new - old)),
        "removed_inventory": tuple(sorted(old - new)),
        "changed_routes": tuple(
            sorted(
                key
                for key in keys
                if last_hashes.hashes.get(key) is None
                or last_hashes.hashes.get(key) != comparison.hashes.hashes.get(key)
            )
        ),
    }


def _items(inventory: Inventory) -> Iterator[str]:
    yield from (f"route {route.key}" for route in inventory.routes)
    yield from (f"param {param.name}" for param in inventory.params)
    yield from (f"error code {error.code}" for error in inventory.error_codes)
    yield from (f"env var {name}" for name in inventory.env_vars)
    yield from (
        f"field {reply.struct}.{reply.name} in {reply.file}" for reply in inventory.response_fields
    )


# The diff


# The snapshot's side leaves out the lines the last run wrote, so the diff shows the repo's own
# change rather than ARDE's comments going away.
def _diff(changed_files: list[str], comparison: SnapshotComparison) -> str:
    parts: list[str] = []
    size = 0
    for name in changed_files:
        written = set(comparison.manifest.lines.get(name, ()))
        old = [line for line in _lines(comparison.snapshot / name) if line.strip() not in written]
        new = _lines(comparison.repo / name)
        part = "".join(difflib.unified_diff(old, new, fromfile=f"a/{name}", tofile=f"b/{name}"))
        size += len(part)
        if size > DIFF_LIMIT:
            parts.append(f"[ARDE left out the diff of {name} and every file after it]\n")
            break
        parts.append(part)
    return "".join(parts)


# A file that is not text, or not there, has no lines to show.
def _lines(path: Path) -> list[str]:
    try:
        if path.is_symlink() or not path.is_file():
            return []
        data = path.read_bytes()
    except OSError:
        return []
    if b"\0" in data:
        return []
    return data.decode("utf-8", errors="replace").splitlines(keepends=True)


def paths_that_differ(before: Path, after: Path) -> tuple[str, ...]:
    """The files added, removed or changed between two trees, from the root, sorted."""
    old_files, new_files = _files(before), _files(after)
    return tuple(
        sorted(
            name
            for name in old_files | new_files
            if name not in old_files
            or name not in new_files
            or not _identical(before / name, after / name)
        )
    )


def _identical(old: Path, new: Path) -> bool:
    if old.is_symlink() or new.is_symlink():
        return old.is_symlink() and new.is_symlink() and os.readlink(old) == os.readlink(new)
    return old.read_bytes() == new.read_bytes()

"""The Go language adapter: every Go idiom ARDE relies on, behind the LanguageAdapter port."""

import shutil
import tempfile
from collections.abc import Sequence
from pathlib import Path

from arde.adapters.language.go import toolchain
from arde.adapters.language.go.directives import comment_edit_may_change_behaviour, read_source
from arde.adapters.language.go.goinspect_client import GoinspectClient, build_goinspect
from arde.adapters.language.go.inventory_builder import SourceReading, build_inventory
from arde.adapters.language.go.makefile import make_targets_of
from arde.application.ports import CheckResult, LanguageAdapter
from arde.domain.inventory import INLINE_HANDLER, Inventory, Route
from arde.domain.profile import ExtractionPattern, RepoProfile

# Added to the profile's patterns, so a repo that reads its settings with os.Getenv needs none.
DEFAULT_ENV_PATTERN = ExtractionPattern(kind="env", call=r"^os\.Getenv$", args={"name": 0})


class GoAdapter(LanguageAdapter):
    def __init__(self, goinspect: Path) -> None:
        self._goinspect = GoinspectClient(goinspect)

    def detects(self, repo: Path) -> bool:
        return (repo / "go.mod").is_file()

    # The checks on comments come first, because the comparison below ignores every comment.
    def is_comment_only(self, old_file: Path, new_file: Path) -> bool:
        if not (_is_go_source(old_file) and _is_go_source(new_file)):
            return False
        if comment_edit_may_change_behaviour(read_source(old_file), read_source(new_file)):
            return False
        return not self._code_differs(old_file, new_file)

    def _code_differs(self, old_file: Path, new_file: Path) -> bool:
        with tempfile.TemporaryDirectory(prefix="arde-compare-") as folder:
            old_dir, new_dir = Path(folder) / "old", Path(folder) / "new"
            for directory, source in ((old_dir, old_file), (new_dir, new_file)):
                directory.mkdir()
                shutil.copyfile(source, directory / "file.go")
            return bool(self._goinspect.compare(old_dir, new_dir).changed)

    def changed_declarations(self, old_repo: Path, new_repo: Path) -> list[str]:
        old, new = self._declaration_hashes(old_repo), self._declaration_hashes(new_repo)
        return sorted(name for name in old.keys() | new.keys() if old.get(name) != new.get(name))

    def _declaration_hashes(self, repo: Path) -> dict[str, str]:
        declarations = self._goinspect.declarations(repo).declarations
        return {f"{d.file}::{d.kind} {d.name}": d.hash for d in declarations}

    def extract_inventory(self, repo: Path, profile: RepoProfile) -> Inventory:
        patterns = (DEFAULT_ENV_PATTERN, *profile.patterns)
        reading = SourceReading(
            matches=self._goinspect.matches(repo, patterns).matches,
            fields=self._goinspect.declarations(repo).fields,
            make_targets=make_targets_of(repo),
        )
        return build_inventory(reading)

    def route_fingerprints(self, repo: Path, routes: Sequence[Route]) -> dict[str, str]:
        roots = _one_root_per_key(routes)
        reply = self._goinspect.fingerprints(repo, sorted(set(roots.values())))
        return {
            key: reply.fingerprints[root]
            for key, root in roots.items()
            if root in reply.fingerprints
        }

    def run_checks(self, repo: Path, profile: RepoProfile) -> CheckResult:
        return toolchain.run_checks(repo, profile)

    def build_service(self, repo: Path, profile: RepoProfile) -> Path:
        return toolchain.build_service(repo, profile)


# The return type is the static proof that GoAdapter satisfies the port: mypy fails here when
# the port gains a method that GoAdapter lacks, or when a signature drifts.
def go_adapter(bin_dir: Path) -> LanguageAdapter:
    return GoAdapter(build_goinspect(bin_dir))


# An added or removed file reaches this call too, and so may a folder or a template.
def _is_go_source(path: Path) -> bool:
    return path.suffix == ".go" and path.is_file()


# A handler reads s.quote or handlers.Quote, and the call graph starts from the function's own name.
def _root_of(route: Route) -> str:
    return route.handler.rsplit(".", 1)[-1]


def _route_key(route: Route) -> str:
    return f"{route.method} {route.path}"


# Two routes can share a key: "GET /{$}" and "GET /" both read as "GET /", and one path can sit on
# two muxes. A hash of one handler would then stand for both, and an edit to the other would not
# move it. A key with more than one handler, an inline one included, is left out like any route
# with no hash, and the caller counts a missing route as changed.
def _one_root_per_key(routes: Sequence[Route]) -> dict[str, str]:
    roots_by_key: dict[str, set[str]] = {}
    for route in routes:
        roots_by_key.setdefault(_route_key(route), set()).add(_root_of(route))
    return {
        key: next(iter(roots))
        for key, roots in roots_by_key.items()
        if len(roots) == 1 and INLINE_HANDLER not in roots
    }

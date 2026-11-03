"""The Go language adapter: every Go idiom ARDE relies on, behind the LanguageAdapter port."""

import hashlib
import shutil
import tempfile
from collections.abc import Iterable, Mapping, Sequence
from contextlib import AbstractContextManager
from pathlib import Path

from arde.adapters.language.go import gofmt, toolchain
from arde.adapters.language.go.directives import files_differ_only_in_ordinary_comments
from arde.adapters.language.go.discovery_guidance import DISCOVERY_GUIDANCE
from arde.adapters.language.go.edit_checks import edit_findings
from arde.adapters.language.go.goinspect_client import (
    GoinspectClient,
    PatternName,
    build_goinspect,
)
from arde.adapters.language.go.inventory_builder import SourceReading, build_inventory
from arde.adapters.language.go.makefile import make_targets_of
from arde.adapters.language.go.quotes import quote_content
from arde.adapters.language.go.renderer import render
from arde.adapters.language.go.swag import build_swag, run_swag
from arde.adapters.language.go.versions import tool_versions
from arde.adapters.repo_links import require_no_link_leaves
from arde.application.ports import (
    CheckResult,
    EditCheckRequest,
    LanguageAdapter,
    QuoteContent,
    RenderOutcome,
    RenderRequest,
    RouteHashes,
    SourceQuote,
    UnhashedReason,
)
from arde.domain.findings import Finding
from arde.domain.inventory import INLINE_HANDLER, Inventory, Route
from arde.domain.profile import ExtractionPattern, RepoProfile

# Added to the profile's patterns, so a repo that reads its settings from os needs none. A service
# with no variable to read has no use for it, so it never counts as a pattern that matched nothing.
DEFAULT_ENV_PATTERN = ExtractionPattern(
    kind="env", call=r"^os\.(Getenv|LookupEnv)$", args={"name": 0}
)


def patterns_of(profile: RepoProfile) -> tuple[ExtractionPattern, ...]:
    return (*profile.patterns, DEFAULT_ENV_PATTERN)


class GoAdapter(LanguageAdapter):
    # Keyword-only, because the two paths have one type and a swap would pass every type check.
    def __init__(self, goinspect: Path, *, swag: Path) -> None:
        self._goinspect_binary = goinspect
        self._goinspect = GoinspectClient(goinspect)
        self._swag = swag

    def detects(self, repo: Path) -> bool:
        require_no_link_leaves(repo)
        return (repo / "go.mod").is_file()

    def discovery_guidance(self) -> str:
        return DISCOVERY_GUIDANCE

    # The tags ARDE adds are `oneOf` and `binding:"optional"`, and an ordinary comment is a whole
    # line that starts with `//` and a space and is no directive. Everything else counts as real: a
    # block comment, a trailing comment, a blank line, a directive, and any comment edit in a file
    # with C source or a test with an example. The text rule comes first and the syntax trees
    # second. The rule cannot see that a line which looks like a comment is inside a raw string, and
    # the trees cannot see a comment at all.
    def is_comment_only(self, old_file: Path, new_file: Path) -> bool:
        try:
            if not (_is_go_source(old_file) and _is_go_source(new_file)):
                return False
            return files_differ_only_in_ordinary_comments(
                old_file, new_file
            ) and not self._code_differs(old_file, new_file)
        except OSError:
            # A file that cannot be reached or read cannot be shown to differ only in comments.
            return False

    def _code_differs(self, old_file: Path, new_file: Path) -> bool:
        with tempfile.TemporaryDirectory(prefix="arde-compare-") as folder:
            old_dir, new_dir = Path(folder) / "old", Path(folder) / "new"
            for directory, source in ((old_dir, old_file), (new_dir, new_file)):
                directory.mkdir()
                shutil.copyfile(source, directory / "file.go")
            return bool(self._goinspect.compare(old_dir, new_dir).changed)

    def changed_declarations(self, old_repo: Path, new_repo: Path) -> list[str]:
        require_no_link_leaves(old_repo)
        require_no_link_leaves(new_repo)
        old, new = self._declaration_hashes(old_repo), self._declaration_hashes(new_repo)
        return sorted(name for name in old.keys() | new.keys() if old.get(name) != new.get(name))

    def _declaration_hashes(self, repo: Path) -> dict[str, str]:
        declarations = self._goinspect.declarations(repo).declarations
        return {declaration.id: declaration.hash for declaration in declarations}

    def extract_inventory(self, repo: Path, profile: RepoProfile) -> Inventory:
        require_no_link_leaves(repo)
        patterns = patterns_of(profile)
        declared = self._goinspect.declarations(repo)
        matched = self._goinspect.matches(repo, patterns)
        reading = SourceReading(
            main_package=profile.main_package,
            service=self._goinspect.routes(repo, patterns, main_package=profile.main_package),
            matches=matched.matches,
            unmatched_patterns=_of_the_profile(matched.unmatched_patterns),
            declarations=declared.declarations,
            fields=declared.fields,
            make_targets=make_targets_of(repo),
        )
        return build_inventory(reading)

    def route_fingerprints(
        self, repo: Path, routes: Sequence[Route], *, main_package: str
    ) -> RouteHashes:
        require_no_link_leaves(repo)
        hashed, unhashed = _hashed_route_per_key(routes)
        every_root = sorted({root for route in hashed.values() for root in route.roots})
        reply = self._goinspect.fingerprints(repo, every_root, main_package=main_package)
        hashes: dict[str, str] = {}
        for key, route in hashed.items():
            missing = [root for root in route.roots if root not in reply.fingerprints]
            if not missing:
                hashes[key] = _fingerprint_of(route, reply.fingerprints)
            elif any(root in reply.without_body for root in missing):
                unhashed[key] = "no body"
            else:
                unhashed[key] = "no function"
        return RouteHashes(hashes=hashes, unhashed=unhashed)

    def render_annotations(self, request: RenderRequest) -> RenderOutcome:
        require_no_link_leaves(request.repo)
        return render(request, self._goinspect.declarations(request.repo))

    def run_checks(self, repo: Path, profile: RepoProfile) -> CheckResult:
        require_no_link_leaves(repo)
        return toolchain.run_checks(repo, profile)

    # A plain method around the context manager, so the gate runs at this call and not at "with".
    def built_service(self, repo: Path, profile: RepoProfile) -> AbstractContextManager[Path]:
        require_no_link_leaves(repo)
        return toolchain.built_service(repo, profile)

    # The gate also keeps swag from writing the spec through a link to a file outside the repo.
    def generate_spec(self, repo: Path, profile: RepoProfile) -> Path:
        require_no_link_leaves(repo)
        return run_swag(repo, profile, swag=self._swag)

    def format_issues(self, repo: Path) -> set[str]:
        require_no_link_leaves(repo)
        return gofmt.unformatted_files(repo)

    # Both gates first: goinspect follows a link, and so does reading a file to compare its lines.
    def check_agent_edits(self, request: EditCheckRequest) -> list[Finding]:
        require_no_link_leaves(request.before)
        require_no_link_leaves(request.after)
        return edit_findings(request, self._goinspect.compare(request.before, request.after))

    def quote_content(self, quote: SourceQuote) -> QuoteContent:
        return quote_content(quote)

    def tool_versions(self) -> dict[str, str]:
        return tool_versions(goinspect=self._goinspect_binary, swag=self._swag)


# The return type is the static proof that GoAdapter satisfies the port: mypy fails here when
# the port gains a method that GoAdapter lacks, or when a signature drifts.
def go_adapter(bin_dir: Path) -> LanguageAdapter:
    return GoAdapter(build_goinspect(bin_dir), swag=build_swag(bin_dir))


def _of_the_profile(unmatched: Iterable[PatternName]) -> tuple[PatternName, ...]:
    default = (DEFAULT_ENV_PATTERN.kind, DEFAULT_ENV_PATTERN.call)
    return tuple(pattern for pattern in unmatched if (pattern.kind, pattern.call) != default)


# An added or removed file reaches this call too, and so may a folder or a template.
def _is_go_source(path: Path) -> bool:
    return path.suffix == ".go" and path.is_file()


# Two routes can share a key: "GET /{$}" and "GET /" both read as "GET /", and one path can sit on
# two muxes. A hash of one handler would then stand for both, and an edit to the other would not
# move it. Only the functions and the handler's constants decide, so one function under two names
# still counts as one.
def _hashed_route_per_key(
    routes: Sequence[Route],
) -> tuple[dict[str, Route], dict[str, UnhashedReason]]:
    routes_by_key: dict[str, list[Route]] = {}
    for route in routes:
        routes_by_key.setdefault(route.key, []).append(route)
    hashed: dict[str, Route] = {}
    unhashed: dict[str, UnhashedReason] = {}
    for key, sharing in routes_by_key.items():
        route = sharing[0]
        if len({_what_is_hashed(other) for other in sharing}) > 1:
            unhashed[key] = "shared key"
        elif route.roots:
            hashed[key] = route
        else:
            unhashed[key] = "inline" if route.handler == INLINE_HANDLER else "no function"
    return hashed, unhashed


def _what_is_hashed(route: Route) -> tuple[str, tuple[str, ...], str]:
    return (route.handler_id or route.handler, route.roots, route.constants_hash)


# A route with one root and no constant keeps that root's own fingerprint, so a route whose
# handler is one function's name hashes as it always has. Otherwise the roots' ids and fingerprints
# and the hash of the handler's constants are hashed together.
def _fingerprint_of(route: Route, fingerprints: Mapping[str, str]) -> str:
    if len(route.roots) == 1 and not route.constants_hash:
        return fingerprints[route.roots[0]]
    combined = hashlib.sha256()
    for root in sorted(route.roots):
        combined.update(f"{root}\x00{fingerprints[root]}\x00".encode())
    if route.constants_hash:
        combined.update(f"constants\x00{route.constants_hash}\x00".encode())
    return combined.hexdigest()

"""The Go helper behind a typed client: build it once, call its commands, read its replies."""

import json
import tempfile
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import ValidationError

from arde.adapters.child_process import (
    ChildCommand,
    ChildOutcome,
    describe_os_error,
    run_child,
)
from arde.adapters.child_text import MESSAGE_TAIL_BYTES, bounded_text
from arde.adapters.language.go.errors import BadPackageError, BadPatternError, GoinspectError
from arde.adapters.language.go.go_environment import go_environment
from arde.adapters.language.go.output_limits import CHECK_OUTPUT_LIMIT, GOINSPECT_REPLY_LIMIT
from arde.adapters.language.go.timeouts import GO_BUILD_TIMEOUT_S, GOINSPECT_TIMEOUT_S
from arde.domain.base import Frozen, describe_problems
from arde.domain.errors import ArdeError
from arde.domain.profile import ExtractionPattern

# The helper's source ships in this repo, next to the package.
GOINSPECT_SOURCE = Path(__file__).resolve().parents[5] / "tools" / "goinspect"
HELPER_NAME = "goinspect"
# What the helper prints in place of an expression past its size limit.
TOO_LONG_TO_PRINT = "(too long to print)"
# How the helper says the profile is to blame. The exit code alone cannot say so, because the Go
# runtime exits 2 on a panic too, and a crash must never reach a model as its own mistake.
EXIT_PROFILE_PROBLEM = 2
BAD_PATTERNS_MARK = "goinspect: bad patterns: "
BAD_MAIN_PACKAGE_MARK = "goinspect: bad main_package: "


class CompareReport(Frozen):
    changed: tuple[str, ...]
    added: tuple[str, ...]
    removed: tuple[str, ...]


class Declaration(Frozen):
    file: str
    kind: str
    name: str
    hash: str
    line: int

    @property
    def id(self) -> str:
        return f"{self.file}::{self.kind} {self.name}"


class StructField(Frozen):
    file: str
    package: str
    struct: str
    name: str
    line: int
    tag: str
    # The value of the tag's json key as Go reads it, or None when it has none.
    json_tag: str | None
    go_type: str
    embedded: bool


class DeclarationReport(Frozen):
    declarations: tuple[Declaration, ...]
    fields: tuple[StructField, ...]


class CallMatch(Frozen):
    kind: str
    callee: str
    file: str
    line: int
    # The text of each argument as written.
    args: dict[str, str]
    # The value of each argument that is one string literal, decoded by the Go compiler's own rules.
    literals: dict[str, str]


class PatternName(Frozen):
    kind: str
    call: str


class MatchReport(Frozen):
    matches: tuple[CallMatch, ...]
    # The patterns that matched no call anywhere in the tree.
    unmatched_patterns: tuple[PatternName, ...]


class RouteSite(CallMatch):
    """A call that registers a route. In literals, the path is the pattern the call registers,
    when the compiler knows it: a wrapper's call holds the path the wrapper builds from it."""

    # The function the route's docs belong on and where it is declared, or None when none fits.
    handler_id: str | None
    handler_file: str | None
    handler_line: int | None
    # Every function of the module the route's fingerprint covers: the ones the handler names, and
    # the ones a middleware variable it calls was filled with. Empty when there is no handler_id,
    # except for a call through such a variable.
    roots: tuple[str, ...]
    # A hash of what the handler holds: the functions it names, the variables it reads and the
    # constants it passes. Two sites with one anchor and two identities register two handlers.
    handler_identity: str
    # A hash of the constants the handler holds, such as the 5 in paged(5, h), or "" for none.
    constants_hash: str


class SourcePlace(Frozen):
    file: str
    line: int


class WrapperCall(SourcePlace):
    kind: str


# Why goinspect cannot read the route a call registers. The first three are about a function of the
# service that a route pattern matches: a site inside it builds its path from more than its
# parameters, or hands its path to another such function, or a call to it registers nothing. The
# next two are about a function that registers routes, such a function or one of net/http: it is
# used other than in a direct call, or an interface its type satisfies is called. The last is a
# mux handed to such a function of the service that does more with it than register it at its path.
UnreadProblem = Literal[
    "builds its path",
    "hands on its path",
    "registers nothing",
    "method is not a constant",
    "method is empty",
    "used as a value",
    "called through an interface",
    "mounts a mux",
]


class UnreadSite(SourcePlace):
    problem: UnreadProblem
    # Where the function around the site writes a parameter the site registers, when that is why.
    written_at: SourcePlace | None


class RouteReport(Frozen):
    # Folders of the packages that make up the service, from the repo root. The root reads ".".
    package_folders: tuple[str, ...]
    # The files of those packages that this machine's build compiles.
    build_files: tuple[str, ...]
    sites: tuple[RouteSite, ...]
    # Calls to http.StripPrefix around anything but a file server.
    stripped_prefixes: tuple[SourcePlace, ...]
    unread_sites: tuple[UnreadSite, ...]
    wrapper_calls: tuple[WrapperCall, ...]


class FingerprintReport(Frozen):
    fingerprints: dict[str, str]
    reach: dict[str, tuple[str, ...]]
    # The ids that name no function of the service.
    unresolved: tuple[str, ...]
    # The ids that reach code with no body to hash, such as another module under the same path.
    without_body: tuple[str, ...]


# Whether this process has built the helper into a folder already. The Go build cache makes a
# rebuild in the next process cheap, so a helper left by an older ARDE is never trusted.
_built: set[Path] = set()


def build_goinspect(bin_dir: Path) -> Path:
    # Resolved here: the build runs in another folder, where a relative path would land.
    binary = bin_dir.resolve() / HELPER_NAME
    if binary in _built and binary.is_file():
        return binary
    _make_the_folder(bin_dir)
    command = ChildCommand(
        argv=("go", "build", "-buildvcs=false", "-o", str(binary), "."),
        cwd=GOINSPECT_SOURCE,
        timeout_s=GO_BUILD_TIMEOUT_S,
        output=CHECK_OUTPUT_LIMIT,
        env=go_environment(GOINSPECT_SOURCE),
    )
    outcome = run_child(command)
    if not outcome.succeeded:
        raise GoinspectError(
            f"Cannot build {HELPER_NAME} from {GOINSPECT_SOURCE}: "
            f"{bounded_text(outcome.output.strip(), MESSAGE_TAIL_BYTES)}",
            next_action=(
                f"Check that Go is installed and that {GOINSPECT_SOURCE} builds on its own, "
                "then run again."
            ),
        )
    _built.add(binary)
    return binary


def _make_the_folder(bin_dir: Path) -> None:
    try:
        bin_dir.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise GoinspectError(
            f"Cannot make a folder for {HELPER_NAME}: {describe_os_error(error)}.",
            next_action="Fix that path or its permissions, then run again.",
        ) from error


class GoinspectClient:
    # Resolved here: every call runs in the folder being inspected, where a relative path could
    # find a helper that the repo planted.
    def __init__(self, binary: Path) -> None:
        self._binary = binary.resolve()

    def compare(self, old_dir: Path, new_dir: Path) -> CompareReport:
        old, new = old_dir.resolve(), new_dir.resolve()
        arguments = ("compare", "--old", str(old), "--new", str(new))
        return self._ask(_Question(arguments, CompareReport, cwd=old))

    def declarations(self, repo: Path) -> DeclarationReport:
        resolved = repo.resolve()
        return self._ask(
            _Question(("declarations", str(resolved)), DeclarationReport, cwd=resolved)
        )

    def matches(self, repo: Path, patterns: Sequence[ExtractionPattern]) -> MatchReport:
        resolved = repo.resolve()
        with _json_file(_as_plain_data(patterns)) as patterns_file:
            arguments = ("match", "--patterns", str(patterns_file), str(resolved))
            return self._ask(_Question(arguments, MatchReport, cwd=resolved))

    def routes(
        self, repo: Path, patterns: Sequence[ExtractionPattern], *, main_package: str
    ) -> RouteReport:
        resolved = repo.resolve()
        with _json_file(_as_plain_data(patterns)) as patterns_file:
            arguments = (
                "routes",
                "--patterns",
                str(patterns_file),
                "--main",
                main_package,
                str(resolved),
            )
            return self._ask(_Question(arguments, RouteReport, cwd=resolved))

    # A file, because an id can hold a comma: (*example.com/x.pair[K, V]).serve.
    def fingerprints(
        self, repo: Path, roots: Sequence[str], *, main_package: str
    ) -> FingerprintReport:
        if not roots:
            return FingerprintReport(fingerprints={}, reach={}, unresolved=(), without_body=())
        resolved = repo.resolve()
        with _json_file(list(roots)) as roots_file:
            arguments = (
                "fingerprints",
                "--roots",
                str(roots_file),
                "--main",
                main_package,
                str(resolved),
            )
            return self._ask(_Question(arguments, FingerprintReport, cwd=resolved))

    def _ask[T: Frozen](self, question: "_Question[T]") -> T:
        command = ChildCommand(
            argv=(str(self._binary), *question.arguments),
            cwd=question.cwd,
            timeout_s=GOINSPECT_TIMEOUT_S,
            output=GOINSPECT_REPLY_LIMIT,
            env=go_environment(question.cwd),
        )
        outcome = run_child(command)
        if not outcome.succeeded:
            raise _failure_of(question, outcome)
        return _read_reply(question, outcome)


# goinspect writes a reason for every failure of its own, so silence means it was stopped from
# outside. Reasons vary too much for one guess at the cause to help.
def _failure_of[T: Frozen](question: "_Question[T]", outcome: ChildOutcome) -> ArdeError:
    reason = bounded_text(outcome.stderr.strip(), MESSAGE_TAIL_BYTES)
    if outcome.exit_code == EXIT_PROFILE_PROBLEM:
        if outcome.stderr.startswith(BAD_PATTERNS_MARK):
            return BadPatternError(reason.removeprefix(BAD_PATTERNS_MARK))
        if outcome.stderr.startswith(BAD_MAIN_PACKAGE_MARK):
            return BadPackageError(f"main_package {reason.removeprefix(BAD_MAIN_PACKAGE_MARK)}")
    if not reason:
        return GoinspectError(
            f"goinspect {question.name} stopped with exit {outcome.exit_code} and gave no reason.",
            next_action=(
                "Something stopped it from outside, such as the machine running out of memory. "
                "Fix that and run again, and report the problem if it stays."
            ),
        )
    return GoinspectError(
        f"goinspect {question.name} failed with exit {outcome.exit_code}: {reason}",
        next_action="Fix what the message names, then run again.",
    )


def _as_plain_data(patterns: Sequence[ExtractionPattern]) -> list[dict[str, object]]:
    return [pattern.model_dump() for pattern in patterns]


@contextmanager
def _json_file(content: object) -> Iterator[Path]:
    with tempfile.TemporaryDirectory(prefix="arde-goinspect-") as folder:
        path = Path(folder) / "input.json"
        path.write_text(json.dumps(content), encoding="utf-8")
        yield path


# What was asked of goinspect, and the model its answer must fit.
@dataclass(frozen=True)
class _Question[T: Frozen]:
    arguments: tuple[str, ...]
    reply_type: type[T]
    cwd: Path

    @property
    def name(self) -> str:
        return self.arguments[0]


def _read_reply[T: Frozen](question: _Question[T], outcome: ChildOutcome) -> T:
    try:
        return question.reply_type.model_validate_json(outcome.stdout)
    except ValidationError as error:
        raise GoinspectError(
            f"goinspect {question.name} printed a reply ARDE cannot read: "
            f"{describe_problems(error, whole='the reply')}",
            next_action=(
                "This means ARDE and goinspect are out of step. Run ARDE again, which rebuilds "
                "goinspect, and report the problem if it stays."
            ),
        ) from error

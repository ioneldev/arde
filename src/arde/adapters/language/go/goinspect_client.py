"""The Go helper behind a typed client: build it once, call its commands, read its replies."""

import json
import tempfile
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from arde.adapters.child_process import (
    ChildCommand,
    ChildOutcome,
    describe_os_error,
    run_child,
)
from arde.adapters.child_text import MESSAGE_TAIL_BYTES, bounded_text
from arde.adapters.language.go.go_environment import go_environment
from arde.adapters.language.go.output_limits import CHECK_OUTPUT_LIMIT, GOINSPECT_REPLY_LIMIT
from arde.adapters.language.go.timeouts import GO_BUILD_TIMEOUT_S, GOINSPECT_TIMEOUT_S
from arde.domain.base import Frozen, describe_problems
from arde.domain.errors import ArdeError
from arde.domain.profile import ExtractionPattern
from arde.domain.report import ExitCode

# The helper's source ships in this repo, next to the package.
GOINSPECT_SOURCE = Path(__file__).resolve().parents[5] / "tools" / "goinspect"
HELPER_NAME = "goinspect"
# What the helper prints in place of an expression past its size limit.
TOO_LONG_TO_PRINT = "(too long to print)"


class GoinspectError(ArdeError):
    def __init__(self, problem: str, *, next_action: str) -> None:
        super().__init__(problem, next_action=next_action, exit_code=ExitCode.CHECKS_FAILED)


class CompareReply(Frozen):
    changed: tuple[str, ...]
    added: tuple[str, ...]
    removed: tuple[str, ...]


class DeclarationReply(Frozen):
    file: str
    kind: str
    name: str
    hash: str
    line: int

    @property
    def id(self) -> str:
        return f"{self.file}::{self.kind} {self.name}"


class FieldReply(Frozen):
    file: str
    struct: str
    name: str
    line: int
    tag: str
    go_type: str
    embedded: bool


class DeclarationsReply(Frozen):
    declarations: tuple[DeclarationReply, ...]
    fields: tuple[FieldReply, ...]


class CallMatch(Frozen):
    kind: str
    call: str
    file: str
    line: int
    # The text of each argument as written.
    args: dict[str, str]
    # The value of each argument that is one string literal, decoded by the Go compiler's own rules.
    literals: dict[str, str]


class UnmatchedPattern(Frozen):
    kind: str
    call: str


class MatchReply(Frozen):
    matches: tuple[CallMatch, ...]
    # The patterns that matched no call anywhere in the tree.
    unmatched_patterns: tuple[UnmatchedPattern, ...]


class RouteSite(CallMatch):
    """A call that registers a route. In literals, the path is any value the compiler knows."""

    # The exact function behind the handler and where it is declared, or None when it has none.
    handler_id: str | None
    handler_file: str | None
    handler_line: int | None


class SourcePlace(Frozen):
    file: str
    line: int


class WrapperCall(SourcePlace):
    kind: str


class RoutesReply(Frozen):
    # Folders of the packages that make up the service, from the repo root. The root reads ".".
    package_folders: tuple[str, ...]
    # The files of those packages that this machine's build compiles.
    build_files: tuple[str, ...]
    sites: tuple[RouteSite, ...]
    stripped_prefixes: tuple[SourcePlace, ...]
    wrapper_calls: tuple[WrapperCall, ...]


class FingerprintsReply(Frozen):
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

    def compare(self, old_dir: Path, new_dir: Path) -> CompareReply:
        old, new = old_dir.resolve(), new_dir.resolve()
        arguments = ("compare", "--old", str(old), "--new", str(new))
        return self._ask(_Question(arguments, CompareReply, cwd=old))

    def declarations(self, root: Path) -> DeclarationsReply:
        resolved = root.resolve()
        return self._ask(
            _Question(("declarations", str(resolved)), DeclarationsReply, cwd=resolved)
        )

    def matches(self, root: Path, patterns: Sequence[ExtractionPattern]) -> MatchReply:
        resolved = root.resolve()
        with _json_file(_as_plain_data(patterns)) as patterns_file:
            arguments = ("match", "--patterns", str(patterns_file), str(resolved))
            return self._ask(_Question(arguments, MatchReply, cwd=resolved))

    def routes(
        self, root: Path, patterns: Sequence[ExtractionPattern], *, main_package: str
    ) -> RoutesReply:
        resolved = root.resolve()
        with _json_file(_as_plain_data(patterns)) as patterns_file:
            arguments = (
                "routes",
                "--patterns",
                str(patterns_file),
                "--main",
                main_package,
                str(resolved),
            )
            return self._ask(_Question(arguments, RoutesReply, cwd=resolved))

    # A file, because an id can hold a comma: (*example.com/x.pair[K, V]).serve.
    def fingerprints(
        self, root: Path, roots: Sequence[str], *, main_package: str
    ) -> FingerprintsReply:
        if not roots:
            return FingerprintsReply(fingerprints={}, reach={}, unresolved=(), without_body=())
        resolved = root.resolve()
        with _json_file(list(roots)) as roots_file:
            arguments = (
                "fingerprints",
                "--roots",
                str(roots_file),
                "--main",
                main_package,
                str(resolved),
            )
            return self._ask(_Question(arguments, FingerprintsReply, cwd=resolved))

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
            raise GoinspectError(
                f"goinspect {question.name} failed with exit {outcome.exit_code}: "
                f"{bounded_text(outcome.stderr.strip(), MESSAGE_TAIL_BYTES)}",
                next_action=(
                    "Fix what the message names, usually a Go file that does not parse, "
                    "and run again."
                ),
            )
        return _read_reply(question, outcome)


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

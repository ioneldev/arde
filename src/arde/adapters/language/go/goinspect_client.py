"""The Go helper behind a typed client: build it once, call its four commands, read its replies."""

import json
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from arde.adapters.child_process import ChildCommand, ChildOutcome, run_child
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


class MatchReply(Frozen):
    matches: tuple[CallMatch, ...]


class FingerprintsReply(Frozen):
    fingerprints: dict[str, str]
    reach: dict[str, tuple[str, ...]]
    unresolved: tuple[str, ...]


# Whether this process has built the helper into a folder already. The Go build cache makes a
# rebuild in the next process cheap, so a helper left by an older ARDE is never trusted.
_built: set[Path] = set()


def build_goinspect(bin_dir: Path) -> Path:
    # Resolved here: the build runs in another folder, where a relative path would land.
    binary = bin_dir.resolve() / HELPER_NAME
    if binary in _built and binary.is_file():
        return binary
    bin_dir.mkdir(parents=True, exist_ok=True)
    command = ChildCommand(
        argv=("go", "build", "-buildvcs=false", "-o", str(binary), "."),
        cwd=GOINSPECT_SOURCE,
        timeout_s=GO_BUILD_TIMEOUT_S,
        output=CHECK_OUTPUT_LIMIT,
        env=go_environment(),
    )
    outcome = run_child(command)
    if not outcome.succeeded:
        raise GoinspectError(
            f"Cannot build {HELPER_NAME} from {GOINSPECT_SOURCE}: {outcome.output.strip()}",
            next_action=(
                f"Check that Go is installed and that {GOINSPECT_SOURCE} builds on its own, "
                "then run again."
            ),
        )
    _built.add(binary)
    return binary


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
        with tempfile.TemporaryDirectory(prefix="arde-patterns-") as folder:
            patterns_file = Path(folder) / "patterns.json"
            patterns_file.write_text(
                json.dumps([pattern.model_dump() for pattern in patterns]), encoding="utf-8"
            )
            arguments = ("match", "--patterns", str(patterns_file), str(resolved))
            return self._ask(_Question(arguments, MatchReply, cwd=resolved))

    def fingerprints(self, root: Path, roots: Sequence[str]) -> FingerprintsReply:
        if not roots:
            return FingerprintsReply(fingerprints={}, reach={}, unresolved=())
        resolved = root.resolve()
        arguments = ("fingerprints", "--roots", ",".join(roots), str(resolved))
        return self._ask(_Question(arguments, FingerprintsReply, cwd=resolved))

    def _ask[T: Frozen](self, question: "_Question[T]") -> T:
        command = ChildCommand(
            argv=(str(self._binary), *question.arguments),
            cwd=question.cwd,
            timeout_s=GOINSPECT_TIMEOUT_S,
            output=GOINSPECT_REPLY_LIMIT,
            env=go_environment(),
        )
        outcome = run_child(command)
        if not outcome.succeeded:
            raise GoinspectError(
                f"goinspect {question.name} failed with exit {outcome.exit_code}: "
                f"{outcome.stderr.strip()}",
                next_action=(
                    "Fix what the message names, usually a Go file that does not parse, "
                    "and run again."
                ),
            )
        return _read_reply(question, outcome)


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

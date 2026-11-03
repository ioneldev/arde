"""Spec generation: the pinned swag reads the repo's annotations and writes its OpenAPI file."""

from pathlib import Path

from arde.adapters.child_process import ChildCommand, ChildOutcome, describe_os_error, run_child
from arde.adapters.child_text import MESSAGE_TAIL_BYTES, bounded_text
from arde.adapters.language.go.errors import GoCommandError
from arde.adapters.language.go.go_environment import go_environment
from arde.adapters.language.go.output_limits import CHECK_OUTPUT_LIMIT
from arde.adapters.language.go.timeouts import GO_BUILD_TIMEOUT_S, SWAG_TIMEOUT_S
from arde.domain.profile import RepoProfile

# The module that pins swag ships in this repo, next to the package. Its go.sum holds the hash of
# every module the build needs, so a module that changed upstream is refused.
SWAG_SOURCE = Path(__file__).resolve().parents[5] / "tools" / "swag"
SWAG_PACKAGE = "github.com/swaggo/swag/v2/cmd/swag"
SWAG_NAME = "swag"
# From the repo root, where swag runs.
SPEC_FOLDER = "docs"
SPEC_JSON = "swagger.json"

# Whether this process has built swag into a folder already. The Go build cache makes a rebuild in
# the next process cheap, so a binary left by an older ARDE is never trusted.
_built: set[Path] = set()


def build_swag(bin_dir: Path) -> Path:
    # Resolved here: the build runs in another folder, where a relative path would land.
    binary = bin_dir.resolve() / SWAG_NAME
    if binary in _built and binary.is_file():
        return binary
    _make_the_folder(bin_dir)
    command = ChildCommand(
        argv=("go", "build", "-buildvcs=false", "-o", str(binary), SWAG_PACKAGE),
        cwd=SWAG_SOURCE,
        timeout_s=GO_BUILD_TIMEOUT_S,
        output=CHECK_OUTPUT_LIMIT,
        env=go_environment(SWAG_SOURCE),
    )
    outcome = run_child(command)
    if not outcome.succeeded:
        raise GoCommandError(
            f"Cannot build {SWAG_NAME} from {SWAG_SOURCE}: {_end_of(outcome)}",
            next_action=(
                "Check that Go is installed and, on the first build, that the Go module proxy can "
                "be reached, then run again."
            ),
        )
    _built.add(binary)
    return binary


def _make_the_folder(bin_dir: Path) -> None:
    try:
        bin_dir.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise GoCommandError(
            f"Cannot make a folder for {SWAG_NAME}: {describe_os_error(error)}.",
            next_action="Fix that path or its permissions, then run again.",
        ) from error


# --v3.1, because Swagger 2.0 cannot say that a field holds one of several structs. --ot json,yaml,
# because the default also writes a docs.go that imports swag, which the repo does not require,
# so the repo's own build would fail. Under --requiredByDefault every field is required unless its
# tags say otherwise, and swag v2 reads omitempty as no such tag.
def run_swag(repo: Path, profile: RepoProfile, *, swag: Path) -> Path:
    command = ChildCommand(
        argv=(
            # Resolved here: swag runs in the repo, where a relative path could find a program the
            # repo planted.
            str(swag.resolve()),
            "init",
            "--v3.1",
            "-g",
            profile.main_file,
            "--requiredByDefault",
            "--ot",
            "json,yaml",
            "-o",
            SPEC_FOLDER,
        ),
        cwd=repo,
        timeout_s=SWAG_TIMEOUT_S,
        output=CHECK_OUTPUT_LIMIT,
        # swag runs go list itself, so it needs what every Go command gets.
        env=go_environment(repo),
    )
    outcome = run_child(command)
    # The exit code alone decides, because swag warns on a run that went well, such as one whose
    # repo root holds no package.
    if not outcome.succeeded:
        raise GoCommandError(
            f"{SWAG_NAME} could not generate the spec, and exited {outcome.exit_code}: "
            f"{_end_of(outcome) or 'it printed nothing'}",
            next_action=(
                "If the line it names came from the repo, fix it there and run again. If ARDE "
                "wrote it, report the problem to the ARDE maintainers."
            ),
        )
    return repo / SPEC_FOLDER / SPEC_JSON


def _end_of(outcome: ChildOutcome) -> str:
    return bounded_text(outcome.output.strip(), MESSAGE_TAIL_BYTES)

"""The Go toolchain: building the service, and the repo's own build, vet and test."""

import os
import re
import shlex
import shutil
import tempfile
from pathlib import Path

from arde.adapters.child_process import ChildCommand, ChildOutcome, run_child
from arde.adapters.language.go.output_limits import CHECK_OUTPUT_LIMIT
from arde.adapters.language.go.timeouts import (
    EXTRA_TEST_COMMAND_TIMEOUT_S,
    GO_BUILD_TIMEOUT_S,
    GO_LIST_TIMEOUT_S,
    GO_TEST_TIMEOUT_S,
    GO_VET_TIMEOUT_S,
)
from arde.application.ports import CheckResult
from arde.domain.errors import ArdeError
from arde.domain.profile import RepoProfile
from arde.domain.report import ExitCode

SERVICE_BINARY_NAME = "service"

# Every name that "go test -list" prints. Its other lines start with ok, ? or FAIL.
_LISTED_TEST = re.compile(r"(Test|Benchmark|Fuzz|Example)\w*")
# One absolute path per line. A test file behind a build tag is listed here with the others.
_IGNORED_FILES_TEMPLATE = '{{range .IgnoredGoFiles}}{{$.Dir}}/{{.}}{{"\\n"}}{{end}}'


class GoCommandError(ArdeError):
    def __init__(self, message: str, *, next_action: str) -> None:
        super().__init__(message, next_action=next_action, exit_code=ExitCode.CHECKS_FAILED)


class BadTestCommandError(ArdeError):
    def __init__(self, command: str) -> None:
        super().__init__(
            f"extra_test_commands holds {command!r}, which is not a command.",
            next_action="Fix that entry in the profile override file, then run again.",
            exit_code=ExitCode.BAD_USAGE,
        )


def build_service(repo: Path, profile: RepoProfile) -> Path:
    folder = Path(tempfile.mkdtemp(prefix="arde-service-"))
    try:
        _build_into(folder / SERVICE_BINARY_NAME, repo, profile)
    except BaseException:
        shutil.rmtree(folder, ignore_errors=True)
        raise
    return folder / SERVICE_BINARY_NAME


# The package comes after "--" so that no text from the profile can be read as a flag, and
# -buildvcs=false because the copy may carry a .git folder the build cannot read.
def _build_into(binary: Path, repo: Path, profile: RepoProfile) -> None:
    arguments = ("build", "-buildvcs=false", "-o", str(binary), "--", profile.main_package)
    outcome = _go(repo, arguments, GO_BUILD_TIMEOUT_S)
    if not outcome.succeeded:
        raise GoCommandError(
            f"The service does not build: {outcome.output.strip()}",
            next_action=(
                "Fix the build errors, or correct main_package in the profile if it names the "
                "wrong package, then run again."
            ),
        )


def run_checks(repo: Path, profile: RepoProfile) -> CheckResult:
    build = _go(repo, ("build", "-buildvcs=false", "./..."), GO_BUILD_TIMEOUT_S)
    vet = _go(repo, ("vet", "./..."), GO_VET_TIMEOUT_S)
    test = _go(repo, ("test", "./..."), GO_TEST_TIMEOUT_S)
    extras = [(command, _run_extra(repo, command)) for command in profile.extra_test_commands]
    sections = [("build", build), ("vet", vet), ("test", test)]
    sections += [(f"extra: {command}", outcome) for command, outcome in extras]
    return CheckResult(
        build_ok=build.succeeded,
        vet_ok=vet.succeeded,
        test_ok=test.succeeded and all(outcome.succeeded for _, outcome in extras),
        output="\n".join(_section(title, outcome) for title, outcome in sections),
        test_count=_count_tests(repo),
        ignored_files=_ignored_files(repo),
    )


def _section(title: str, outcome: ChildOutcome) -> str:
    return f"== {title} (exit {outcome.exit_code}) ==\n{outcome.output.strip()}\n"


def _count_tests(repo: Path) -> int:
    listing = _go(repo, ("test", "-list", ".*", "./..."), GO_TEST_TIMEOUT_S)
    return sum(1 for line in listing.stdout.splitlines() if _LISTED_TEST.fullmatch(line))


def _ignored_files(repo: Path) -> tuple[str, ...]:
    arguments = ("list", "-e", "-f", _IGNORED_FILES_TEMPLATE, "./...")
    listing = _go(repo, arguments, GO_LIST_TIMEOUT_S)
    if not listing.succeeded:
        raise GoCommandError(
            f"Cannot list the files the build leaves out: {listing.output.strip()}",
            next_action="Run the same list command by hand in the repo to see why, then run again.",
        )
    root = os.path.realpath(repo)
    paths = (os.path.relpath(os.path.realpath(line), root) for line in listing.stdout.splitlines())
    return tuple(sorted(Path(path).as_posix() for path in paths))


# No shell: the words are split and run as they are, so a command in the profile cannot chain
# another with && or ;.
def _run_extra(repo: Path, command: str) -> ChildOutcome:
    try:
        words = shlex.split(command)
    except ValueError as error:
        raise BadTestCommandError(command) from error
    if not words:
        raise BadTestCommandError(command)
    extra = ChildCommand(
        argv=tuple(words),
        cwd=repo,
        timeout_s=EXTRA_TEST_COMMAND_TIMEOUT_S,
        output=CHECK_OUTPUT_LIMIT,
    )
    return run_child(extra)


def _go(repo: Path, arguments: tuple[str, ...], timeout_s: float) -> ChildOutcome:
    command = ChildCommand(
        argv=("go", *arguments), cwd=repo, timeout_s=timeout_s, output=CHECK_OUTPUT_LIMIT
    )
    return run_child(command)

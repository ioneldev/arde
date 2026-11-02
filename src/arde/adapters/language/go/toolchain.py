"""The Go toolchain: building the service, and the repo's own build, vet and test."""

import json
import os
import re
import shlex
import shutil
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import NamedTuple

from pydantic import BaseModel, Field

from arde.adapters.child_process import ChildCommand, ChildOutcome, run_child
from arde.adapters.child_text import MESSAGE_TAIL_BYTES, bounded_text, visible_text
from arde.adapters.language.go.errors import GoCommandError
from arde.adapters.language.go.go_environment import go_environment
from arde.adapters.language.go.output_limits import CHECK_OUTPUT_LIMIT
from arde.adapters.language.go.timeouts import (
    EXTRA_TEST_COMMAND_TIMEOUT_S,
    GO_BUILD_TIMEOUT_S,
    GO_LIST_TIMEOUT_S,
    GO_TEST_TIMEOUT_S,
    GO_VET_TIMEOUT_S,
)
from arde.adapters.repo_links import is_inside
from arde.application.ports import CheckResult
from arde.domain.errors import ArdeError
from arde.domain.profile import RepoProfile
from arde.domain.report import ExitCode

SERVICE_BINARY_NAME = "service"
# A repo of thousands of folders would overrun the length of one command line.
FOLDERS_PER_LIST = 1000

# Every name that "go test -list" prints. Its other lines start with ok, ? or FAIL.
_LISTED_TEST = re.compile(r"(Test|Benchmark|Fuzz|Example)\w*")


class GoStep(NamedTuple):
    arguments: tuple[str, ...]
    timeout_s: float


class _ListedPackage(BaseModel):
    """One package from go list -json, which leaves out a field with nothing in it."""

    folder: str = Field(alias="Dir")
    # A test file behind a build tag is listed here with the others.
    left_out: tuple[str, ...] = Field(alias="IgnoredGoFiles", default=())


class BadTestCommandError(ArdeError):
    def __init__(self, command: str) -> None:
        super().__init__(
            f"extra_test_commands holds {command!r}, which is not a command.",
            next_action="Fix that entry in the profile override file, then run again.",
            exit_code=ExitCode.BAD_USAGE,
        )


def build_service(repo: Path, profile: RepoProfile) -> Path:
    _require_one_main_package(repo, profile.main_package)
    folder = Path(tempfile.mkdtemp(prefix="arde-service-"))
    try:
        _build_into(repo, profile, binary=folder / SERVICE_BINARY_NAME)
    except BaseException:
        shutil.rmtree(folder, ignore_errors=True)
        raise
    return folder / SERVICE_BINARY_NAME


# Building any other package writes an archive that looks like a binary and cannot be run, and the
# caller finds out far from the cause.
def _require_one_main_package(repo: Path, main_package: str) -> None:
    arguments = ("list", "-buildvcs=false", "-f", "{{.Name}}", "--", main_package)
    listing = _go(repo, GoStep(arguments, GO_LIST_TIMEOUT_S))
    if not listing.succeeded:
        raise GoCommandError(
            f"main_package {main_package} cannot be loaded: {_end_of(listing)}",
            next_action="Correct main_package in the profile to name a package, then run again.",
        )
    names = listing.stdout.split()
    if names != ["main"]:
        raise GoCommandError(
            f"main_package {main_package} {_what_it_names(names)}, "
            "and a service is one package main.",
            next_action="Correct main_package in the profile to the package that holds func main.",
        )


def _what_it_names(names: list[str]) -> str:
    if not names:
        return "names no package"
    if len(names) == 1:
        return f"is package {names[0]}"
    return f"names {len(names)} packages ({bounded_text(', '.join(names), MESSAGE_TAIL_BYTES)})"


# The package comes after "--" so that no text from the profile can be read as a flag, and
# -buildvcs=false because the copy may carry a .git folder the build cannot read.
def _build_into(repo: Path, profile: RepoProfile, *, binary: Path) -> None:
    arguments = ("build", "-buildvcs=false", "-o", str(binary), "--", profile.main_package)
    outcome = _go(repo, GoStep(arguments, GO_BUILD_TIMEOUT_S))
    if not outcome.succeeded:
        raise GoCommandError(
            f"The service does not build: {_end_of(outcome)}",
            next_action=(
                "Fix the build errors, or correct main_package in the profile if it names the "
                "wrong package, then run again."
            ),
        )


def run_checks(repo: Path, profile: RepoProfile) -> CheckResult:
    # Split first, so that a typo in the override file does not wait for build, vet and test.
    extra_commands = [(command, _split_command(command)) for command in profile.extra_test_commands]
    # -o /dev/null: a repo of one main package would otherwise get its binary written into it.
    build = _go(
        repo, GoStep(("build", "-buildvcs=false", "-o", os.devnull, "./..."), GO_BUILD_TIMEOUT_S)
    )
    vet = _go(repo, GoStep(("vet", "./..."), GO_VET_TIMEOUT_S))
    test = _go(repo, GoStep(("test", "./..."), GO_TEST_TIMEOUT_S))
    extras = [(command, _run_extra(repo, words)) for command, words in extra_commands]
    listing = _go(repo, GoStep(("test", "-list", ".*", "./..."), GO_TEST_TIMEOUT_S))
    sections = [("build", build), ("vet", vet), ("test", test)]
    sections += [(f"extra: {command}", outcome) for command, outcome in extras]
    return CheckResult(
        build_ok=build.succeeded,
        vet_ok=vet.succeeded,
        test_ok=test.succeeded and all(outcome.succeeded for _, outcome in extras),
        output="\n".join(_section(title, outcome) for title, outcome in sections),
        test_count=_count_listed_tests(listing, tests_passed=test.succeeded),
        ignored_files=_ignored_files(repo),
    )


# All of it, not the end alone: the output is already limited, and the repair loop reads it. Code
# decides pass or fail from the booleans and never from this text, so a forged header changes
# nothing.
def _section(title: str, outcome: ChildOutcome) -> str:
    return f"== {title} (exit {outcome.exit_code}) ==\n{visible_text(outcome.output.strip())}\n"


def _end_of(outcome: ChildOutcome) -> str:
    return bounded_text(outcome.output.strip(), MESSAGE_TAIL_BYTES)


# Zero must mean zero tests. A listing that failed after the tests passed would read as zero, and
# so would one that was cut short, so both stop the run.
def _count_listed_tests(listing: ChildOutcome, *, tests_passed: bool) -> int:
    _require_whole(listing, "the list of tests")
    if tests_passed and not listing.succeeded:
        raise GoCommandError(
            f"The tests passed, but listing them failed, so they cannot be counted: "
            f"{_end_of(listing)}",
            next_action="Run the list command by hand in the repo to see why, then run again.",
        )
    return sum(1 for line in listing.stdout.splitlines() if _LISTED_TEST.fullmatch(line))


def _require_whole(listing: ChildOutcome, what: str) -> None:
    if listing.output_was_cut:
        raise GoCommandError(
            f"{what} is too long to read in full.",
            next_action="Split the repo's packages, or report this to the ARDE maintainers.",
        )


def _ignored_files(repo: Path) -> tuple[str, ...]:
    folders = _folders_with_go_files(repo)
    root = os.path.realpath(repo)
    found: set[str] = set()
    for start in range(0, len(folders), FOLDERS_PER_LIST):
        chunk = folders[start : start + FOLDERS_PER_LIST]
        for package in _packages_in(_list_ignored_files(repo, chunk)):
            found.update(_left_out_files_inside(package, root))
    return tuple(sorted(found))


# A file keeps its own name, and only its real path decides whether it counts as inside.
def _left_out_files_inside(package: _ListedPackage, root: str) -> Iterator[str]:
    folder = os.path.realpath(package.folder)
    for name in package.left_out:
        path = os.path.join(folder, name)
        if is_inside(os.path.realpath(path), root):
            yield Path(os.path.relpath(path, root)).as_posix()


# go list -json prints one object after another, with nothing around them. A bad object raises a
# ValueError, from the decoder or from the model.
def _packages_in(stream: str) -> list[_ListedPackage]:
    decoder = json.JSONDecoder()
    packages: list[_ListedPackage] = []
    rest = stream.strip()
    try:
        while rest:
            package, end = decoder.raw_decode(rest)
            packages.append(_ListedPackage.model_validate(package))
            rest = rest[end:].lstrip()
    except ValueError as error:
        raise GoCommandError(
            "go list printed something other than the packages that were asked for.",
            next_action="Run the same list command by hand in the repo to see why, then run again.",
        ) from error
    return packages


def _list_ignored_files(repo: Path, folders: list[str]) -> str:
    arguments = ("list", "-e", "-buildvcs=false", "-json=Dir,IgnoredGoFiles", *folders)
    listing = _go(repo, GoStep(arguments, GO_LIST_TIMEOUT_S))
    _require_whole(listing, "the list of files the build leaves out")
    if not listing.succeeded:
        raise GoCommandError(
            f"Cannot list the files the build leaves out: {_end_of(listing)}",
            next_action="Run the same list command by hand in the repo to see why, then run again.",
        )
    return listing.stdout


# Named one by one, because "./..." leaves out a package whose every file is behind a build tag,
# and its tests are the ones that nothing else shows. The folders the Go tool passes over, and
# those of another module, are left out as ./... leaves them out.
def _folders_with_go_files(repo: Path) -> list[str]:
    folders: list[str] = []
    for current, subfolders, files in os.walk(repo):
        here = Path(current)
        subfolders[:] = sorted(name for name in subfolders if not _is_passed_over(here / name))
        if any(name.endswith(".go") for name in files):
            folders.append("./" + here.relative_to(repo).as_posix() if here != repo else ".")
    return folders


def _is_passed_over(folder: Path) -> bool:
    return (
        folder.name in ("vendor", "testdata")
        or folder.name.startswith(("_", "."))
        or os.path.isfile(folder / "go.mod")
    )


# No shell: the words are split and run as they are, so a command in the profile cannot chain
# another with && or ;.
def _split_command(command: str) -> tuple[str, ...]:
    try:
        words = shlex.split(command)
    except ValueError as error:
        raise BadTestCommandError(command) from error
    if not words:
        raise BadTestCommandError(command)
    return tuple(words)


def _run_extra(repo: Path, words: tuple[str, ...]) -> ChildOutcome:
    extra = ChildCommand(
        argv=words,
        cwd=repo,
        timeout_s=EXTRA_TEST_COMMAND_TIMEOUT_S,
        output=CHECK_OUTPUT_LIMIT,
        env=go_environment(repo),
    )
    return run_child(extra)


def _go(repo: Path, step: GoStep) -> ChildOutcome:
    command = ChildCommand(
        argv=("go", *step.arguments),
        cwd=repo,
        timeout_s=step.timeout_s,
        output=CHECK_OUTPUT_LIMIT,
        env=go_environment(repo),
    )
    return run_child(command)

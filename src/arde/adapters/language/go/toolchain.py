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

from arde.adapters.child_process import ChildCommand, ChildOutcome, OutputLimit, run_child
from arde.adapters.child_text import (
    MESSAGE_TAIL_BYTES,
    bounded_text,
    visible_text,
    with_kept_tail,
)
from arde.adapters.language.go.errors import GoCommandError
from arde.adapters.language.go.go_environment import go_environment
from arde.adapters.language.go.go_test_events import MeasuredTests, measured_tests
from arde.adapters.language.go.output_limits import CHECK_OUTPUT_LIMIT, TEST_EVENTS_LIMIT
from arde.adapters.language.go.timeouts import (
    EXTRA_TEST_COMMAND_TIMEOUT_S,
    GO_BUILD_TIMEOUT_S,
    GO_LIST_TIMEOUT_S,
    GO_TEST_BINARY_TIMEOUT_S,
    GO_TEST_TIMEOUT_S,
    GO_VET_TIMEOUT_S,
)
from arde.adapters.repo_links import is_inside
from arde.application.ports import CheckResult
from arde.domain.base import printable_name
from arde.domain.errors import ArdeError
from arde.domain.profile import RepoProfile
from arde.domain.report import ExitCode

SERVICE_BINARY_NAME = "service"
# A repo of thousands of folders would overrun the length of one command line.
FOLDERS_PER_LIST = 1000
# Go exits 0 on this line, with nothing built. Only the whole line counts, because a compiler
# warning quotes source and a repo chooses that text.
_NOTHING_TO_BUILD = 'go: warning: "./..." matched no packages'

# Every name that "go test -list" prints. Its other lines start with ok, ? or FAIL.
_LISTED_TEST = re.compile(r"(Test|Benchmark|Fuzz|Example)\w*")


class GoStep(NamedTuple):
    arguments: tuple[str, ...]
    timeout_s: float
    output: OutputLimit = CHECK_OUTPUT_LIMIT


class _GoFolders(NamedTuple):
    # The folders ./... reaches that hold Go files, as arguments to a go command.
    packages: list[str]
    # The folders where ./... stops at another module's go.mod, from the repo root.
    nested_modules: list[str]


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
    build_ok = _built_something(build)
    vet = _go(repo, GoStep(("vet", "./..."), GO_VET_TIMEOUT_S))
    test = _run_the_tests(repo)
    measured = measured_tests(test.stdout, tests_passed=test.succeeded)
    extras = [(command, _run_extra(repo, words)) for command, words in extra_commands]
    listing = _go(repo, GoStep(("test", "-list", ".*", "./..."), GO_TEST_TIMEOUT_S))
    folders = _go_folders(repo)
    sections = [("build", build), ("vet", vet), ("test", _as_text(test, measured))]
    sections += [(f"extra: {command}", outcome) for command, outcome in extras]
    return CheckResult(
        build_ok=build_ok,
        vet_ok=vet.succeeded,
        test_ok=test.succeeded and all(outcome.succeeded for _, outcome in extras),
        output="\n".join(_section(title, outcome) for title, outcome in sections),
        tests_run=measured.tests_run,
        tests_skipped=measured.tests_skipped,
        tests_listed=_count_listed_tests(listing, tests_passed=test.succeeded),
        ignored_files=_ignored_files(repo, folders.packages, build_passed=build_ok),
        unchecked_modules=tuple(printable_name(folder) for folder in folders.nested_modules),
    )


def _built_something(build: ChildOutcome) -> bool:
    return build.succeeded and _NOTHING_TO_BUILD not in build.stderr.splitlines()


# -count=1 because a cached pass can stand for code outside the repo that has since changed.
def _run_the_tests(repo: Path) -> ChildOutcome:
    timeout = f"{GO_TEST_BINARY_TIMEOUT_S}s"
    arguments = ("test", "-json", "-count=1", "-timeout", timeout, "./...")
    test = _go(repo, GoStep(arguments, GO_TEST_TIMEOUT_S, output=TEST_EVENTS_LIMIT))
    if test.output_was_cut:
        raise GoCommandError(
            "The tests printed too much to read in full, so what ran cannot be counted. The end "
            f"of what they printed, which names the last test: {_end_of(test)}",
            next_action="Find the test that prints so much, quiet it, then run again.",
        )
    return test


# The events are read and gone, so the section holds their text, limited as any other check's.
def _as_text(test: ChildOutcome, measured: MeasuredTests) -> ChildOutcome:
    limit = CHECK_OUTPUT_LIMIT.max_bytes
    return ChildOutcome(
        exit_code=test.exit_code,
        stdout=with_kept_tail(measured.text, limit),
        stderr=with_kept_tail(test.stderr, limit),
    )


# All of it, not the end alone: the output is already limited, and the repair loop reads it. Code
# decides pass or fail from the booleans and never from this text, so a forged header changes
# nothing.
def _section(title: str, outcome: ChildOutcome) -> str:
    return f"== {title} (exit {outcome.exit_code}) ==\n{visible_text(outcome.output.strip())}\n"


def _end_of(outcome: ChildOutcome) -> str:
    return bounded_text(outcome.output.strip(), MESSAGE_TAIL_BYTES)


# Zero must mean zero tests. A listing that failed after the tests passed would read as zero, and
# so would one that was cut short, so both stop the run. One that failed with the tests holds only
# the packages that compiled, so it gives no count at all.
def _count_listed_tests(listing: ChildOutcome, *, tests_passed: bool) -> int | None:
    _require_whole(listing, "the list of tests")
    if listing.succeeded:
        return sum(1 for line in listing.stdout.splitlines() if _LISTED_TEST.fullmatch(line))
    if not tests_passed:
        return None
    raise GoCommandError(
        f"The tests passed, but listing them failed, so they cannot be counted: {_end_of(listing)}",
        next_action="Run the list command by hand in the repo to see why, then run again.",
    )


def _require_whole(listing: ChildOutcome, what: str) -> None:
    if listing.output_was_cut:
        raise GoCommandError(
            f"{what} is too long to read in full.",
            next_action="Split the repo's packages, or report this to the ARDE maintainers.",
        )


# A repo that does not load fails this list too, and the build has already said why in the result.
def _ignored_files(repo: Path, folders: list[str], *, build_passed: bool) -> tuple[str, ...]:
    try:
        return _files_left_out_of(repo, folders)
    except GoCommandError:
        if build_passed:
            raise
        return ()


def _files_left_out_of(repo: Path, folders: list[str]) -> tuple[str, ...]:
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
def _go_folders(repo: Path) -> _GoFolders:
    found = _GoFolders(packages=[], nested_modules=[])
    for current, subfolders, files in os.walk(repo):
        here = Path(current)
        reached = [name for name in sorted(subfolders) if not _is_passed_over(name)]
        modules = [name for name in reached if os.path.isfile(here / name / "go.mod")]
        subfolders[:] = [name for name in reached if name not in modules]
        found.nested_modules.extend((here / name).relative_to(repo).as_posix() for name in modules)
        if any(name.endswith(".go") for name in files):
            found.packages.append("./" + here.relative_to(repo).as_posix() if here != repo else ".")
    return found


def _is_passed_over(folder_name: str) -> bool:
    return folder_name in ("vendor", "testdata") or folder_name.startswith(("_", "."))


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
        output=step.output,
        env=go_environment(repo),
    )
    return run_child(command)

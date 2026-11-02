"""The checks and the build around the Go toolchain, with the toolchain played by a stand-in."""

from collections.abc import Callable
from pathlib import Path

import pytest
from shop_support import shop_profile

from arde.adapters.child_process import ChildCommand, ChildOutcome
from arde.adapters.language.go import toolchain
from arde.adapters.language.go.errors import GoCommandError
from arde.adapters.language.go.toolchain import BadTestCommandError, build_service, run_checks

LISTING = (
    "TestAdd\nTestSubtract\nExampleAdd\n"
    "ok  \texample.com/p\t0.2s\n?   \texample.com/q\t[no test files]\n"
)


class StandInGo:
    """Answers each go command the way the test says, and records what it was asked."""

    def __init__(self, **answers: ChildOutcome | Callable[[tuple[str, ...]], ChildOutcome]) -> None:
        self.answers = answers
        self.asked: list[tuple[str, ...]] = []
        self.environments: list[dict[str, str]] = []

    def __call__(self, command: ChildCommand) -> ChildOutcome:
        self.asked.append(command.argv)
        self.environments.append(command.env)
        answer = self.answers.get(
            self._which(command.argv), ChildOutcome(exit_code=0, stdout="", stderr="")
        )
        return answer(command.argv) if callable(answer) else answer

    @staticmethod
    def _which(argv: tuple[str, ...]) -> str:
        if argv[1:3] == ("test", "-list"):
            return "listing"
        if argv[1] == "list" and "{{.Name}}" in argv:
            return "package_name"
        if argv[1] == "list":
            return "ignored_files"
        return argv[1]


def outcome(exit_code: int = 0, stdout: str = "", *, cut: bool = False) -> ChildOutcome:
    return ChildOutcome(exit_code=exit_code, stdout=stdout, stderr="", output_was_cut=cut)


GO_ENVIRONMENT = {"PATH": "/usr/bin", "GOPRIVATE": "corp.example/*", "GOENV": "off"}


# The real environment is read from the toolchain, which these tests do without.
@pytest.fixture(autouse=True)
def a_known_go_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(toolchain, "go_environment", lambda: dict(GO_ENVIRONMENT))


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    return tmp_path


def use(monkeypatch: pytest.MonkeyPatch, go: StandInGo) -> StandInGo:
    monkeypatch.setattr(toolchain, "run_child", go)
    return go


def test_the_tests_the_toolchain_lists_are_counted(
    monkeypatch: pytest.MonkeyPatch, repo: Path
) -> None:
    use(monkeypatch, StandInGo(listing=outcome(stdout=LISTING)))

    assert run_checks(repo, shop_profile()).test_count == 3


def test_a_listing_that_failed_after_the_tests_passed_is_an_error_and_not_a_count_of_zero(
    monkeypatch: pytest.MonkeyPatch, repo: Path
) -> None:
    use(monkeypatch, StandInGo(test=outcome(0), listing=outcome(3)))

    with pytest.raises(GoCommandError) as failed:
        run_checks(repo, shop_profile())

    assert "list" in str(failed.value)
    assert failed.value.next_action


def test_a_listing_that_failed_along_with_the_tests_is_a_count_of_zero_and_a_failed_test(
    monkeypatch: pytest.MonkeyPatch, repo: Path
) -> None:
    use(monkeypatch, StandInGo(test=outcome(1), listing=outcome(1)))

    result = run_checks(repo, shop_profile())

    assert (result.test_ok, result.test_count) == (False, 0)


def test_a_listing_that_was_cut_is_an_error_because_a_count_of_the_rest_would_be_too_low(
    monkeypatch: pytest.MonkeyPatch, repo: Path
) -> None:
    use(monkeypatch, StandInGo(listing=outcome(stdout=LISTING, cut=True)))

    with pytest.raises(GoCommandError, match="too long"):
        run_checks(repo, shop_profile())


def test_the_files_the_build_leaves_out_are_listed_from_a_list_that_was_not_cut(
    monkeypatch: pytest.MonkeyPatch, repo: Path
) -> None:
    (repo / "main.go").write_text("package main\n", encoding="utf-8")
    use(monkeypatch, StandInGo(ignored_files=outcome(stdout="", cut=True)))

    with pytest.raises(GoCommandError, match="too long"):
        run_checks(repo, shop_profile())


def test_the_build_check_writes_no_binary_anywhere(
    monkeypatch: pytest.MonkeyPatch, repo: Path
) -> None:
    go = use(monkeypatch, StandInGo())

    run_checks(repo, shop_profile())

    [build] = [argv for argv in go.asked if argv[1] == "build"]
    assert build == ("go", "build", "-buildvcs=false", "-o", "/dev/null", "./...")


@pytest.mark.parametrize("package_name", ["main\n", "main\r\n"])
def test_a_package_named_main_is_built(
    monkeypatch: pytest.MonkeyPatch, repo: Path, package_name: str
) -> None:
    use(monkeypatch, StandInGo(package_name=outcome(stdout=package_name)))

    binary = build_service(repo, shop_profile(main_package="./cmd/app"))

    assert binary.name == "service"
    binary.parent.rmdir()


@pytest.mark.parametrize(
    ("answer", "main_package"),
    [
        ("pricing\n", "./pricing"),
        ("pricing\n", "example.com/shop/pricing"),
        ("pricing\n", "./pricing/"),
        ("pricing\n", ".//pricing"),
        ("main\npricing\nweb\n", "./..."),
        ("", "./empty"),
    ],
)
def test_a_package_that_is_not_main_stops_the_build_and_names_main_package(
    monkeypatch: pytest.MonkeyPatch, repo: Path, answer: str, main_package: str
) -> None:
    go = use(monkeypatch, StandInGo(package_name=outcome(stdout=answer)))

    with pytest.raises(GoCommandError) as refused:
        build_service(repo, shop_profile(main_package=main_package))

    assert f"main_package {main_package}" in str(refused.value)
    assert "main_package" in refused.value.next_action
    assert not [argv for argv in go.asked if argv[1] == "build"], "nothing was built"


def test_a_package_that_cannot_be_listed_stops_the_build_with_the_toolchains_words(
    monkeypatch: pytest.MonkeyPatch, repo: Path
) -> None:
    failed_list = ChildOutcome(exit_code=1, stdout="", stderr="no required module provides ./x\n")
    use(monkeypatch, StandInGo(package_name=failed_list))

    with pytest.raises(GoCommandError, match="no required module provides"):
        build_service(repo, shop_profile(main_package="./x"))


def test_the_package_is_listed_after_a_double_dash_like_it_is_built(
    monkeypatch: pytest.MonkeyPatch, repo: Path
) -> None:
    go = use(monkeypatch, StandInGo(package_name=outcome(stdout="main\n")))

    build_service(repo, shop_profile(main_package="./cmd/app")).parent.rmdir()

    [listed] = [argv for argv in go.asked if "{{.Name}}" in argv]
    assert listed[-2:] == ("--", "./cmd/app")


@pytest.mark.parametrize("bad_command", ["echo 'unbalanced", "", "   "])
def test_an_extra_command_that_is_not_a_command_stops_the_run_before_any_command_runs(
    monkeypatch: pytest.MonkeyPatch, repo: Path, bad_command: str
) -> None:
    go = use(monkeypatch, StandInGo())
    profile = shop_profile(extra_test_commands=("touch fine", bad_command))

    with pytest.raises(BadTestCommandError):
        run_checks(repo, profile)

    assert go.asked == [], "a typo in the override file must not wait for build, vet and test"


def test_every_go_command_and_every_extra_command_runs_with_the_go_environment(
    monkeypatch: pytest.MonkeyPatch, repo: Path
) -> None:
    go = use(monkeypatch, StandInGo(package_name=outcome(stdout="main\n")))
    (repo / "main.go").write_text("package main\n", encoding="utf-8")

    run_checks(repo, shop_profile(extra_test_commands=("make smoke",)))
    build_service(repo, shop_profile()).parent.rmdir()

    assert len(go.asked) >= 8
    assert go.environments == [GO_ENVIRONMENT] * len(go.asked)
    assert ("make", "smoke") in go.asked


def test_the_files_the_build_leaves_out_are_asked_for_in_chunks_of_a_thousand_folders(
    monkeypatch: pytest.MonkeyPatch, repo: Path
) -> None:
    for number in range(2500):
        folder = repo / f"p{number:04d}"
        folder.mkdir()
        (folder / "a.go").write_text("package p\n", encoding="utf-8")

    def hidden_file_of_the_first_folder(argv: tuple[str, ...]) -> ChildOutcome:
        return outcome(stdout=f"{repo.resolve()}/{argv[5][2:]}/hidden_test.go\n")

    go = use(monkeypatch, StandInGo(ignored_files=hidden_file_of_the_first_folder))

    result = run_checks(repo, shop_profile())

    listings = [argv for argv in go.asked if argv[1:3] == ("list", "-e")]
    assert [len(argv) - 5 for argv in listings] == [1000, 1000, 500]
    assert result.ignored_files == (
        "p0000/hidden_test.go",
        "p1000/hidden_test.go",
        "p2000/hidden_test.go",
    )


def test_a_failure_in_one_chunk_stops_the_run_with_the_toolchains_words(
    monkeypatch: pytest.MonkeyPatch, repo: Path
) -> None:
    for number in range(1500):
        folder = repo / f"p{number:04d}"
        folder.mkdir()
        (folder / "a.go").write_text("package p\n", encoding="utf-8")
    failing = ChildOutcome(exit_code=1, stdout="", stderr="go: broken\n")
    use(monkeypatch, StandInGo(ignored_files=failing))

    with pytest.raises(GoCommandError, match="go: broken"):
        run_checks(repo, shop_profile())

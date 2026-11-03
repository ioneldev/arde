"""The helper and swag an image ships built, named by ARDE_GOINSPECT and ARDE_SWAG.

Stand-in programs play both tools, so no Go is needed.
"""

import stat
from pathlib import Path

import pytest
from known_environment import known_go_environment
from shop_support import shop_profile

from arde.adapters.child_process import ChildCommand, ChildOutcome
from arde.adapters.language.go import goinspect_client, swag
from arde.adapters.language.go.adapter import go_adapter
from arde.adapters.language.go.prebuilt import GOINSPECT_VARIABLE, SWAG_VARIABLE
from arde.domain.errors import ArdeError
from arde.domain.report import ExitCode

# Records that it ran, and answers as an empty declarations reply would.
STAND_IN = """#!/bin/sh
printf '%s\\n' "$@" > "$0.args"
printf '{"declarations": [], "fields": []}'
"""


def a_stand_in(path: Path) -> Path:
    path.write_text(STAND_IN, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


def ran(stand_in: Path) -> bool:
    return Path(f"{stand_in}.args").is_file()


@pytest.fixture(autouse=True)
def a_known_go_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(goinspect_client, "go_environment", known_go_environment)
    monkeypatch.setattr(swag, "go_environment", known_go_environment)


@pytest.fixture
def unbuildable(tmp_path: Path) -> Path:
    """A folder for built tools that cannot be made, so a build stops at once instead of running."""
    (tmp_path / "a-file").write_text("not a folder", encoding="utf-8")
    return tmp_path / "a-file" / "bin"


def test_the_tools_the_variables_name_are_used_and_none_is_built(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, unbuildable: Path
) -> None:
    helper = a_stand_in(tmp_path / "goinspect")
    swag_stand_in = a_stand_in(tmp_path / "swag")
    monkeypatch.setenv(GOINSPECT_VARIABLE, str(helper))
    monkeypatch.setenv(SWAG_VARIABLE, str(swag_stand_in))
    repo = tmp_path / "repo"
    repo.mkdir()

    adapter = go_adapter(unbuildable)
    adapter.changed_declarations(repo, repo)
    adapter.generate_spec(repo, shop_profile())

    assert (ran(helper), ran(swag_stand_in)) == (True, True)


@pytest.mark.parametrize("variable", [GOINSPECT_VARIABLE, SWAG_VARIABLE])
def test_a_variable_that_names_no_file_stops_with_its_name_and_a_next_action(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, unbuildable: Path, variable: str
) -> None:
    for name in (GOINSPECT_VARIABLE, SWAG_VARIABLE):
        monkeypatch.setenv(name, str(a_stand_in(tmp_path / name)))
    monkeypatch.setenv(variable, str(tmp_path / "missing"))

    with pytest.raises(ArdeError) as stopped:
        go_adapter(unbuildable)

    assert variable in str(stopped.value)
    assert str(tmp_path / "missing") in str(stopped.value)
    assert variable in stopped.value.next_action
    assert stopped.value.exit_code == ExitCode.BAD_USAGE


class CountingBuild:
    """Plays go build: writes the binary it is asked for."""

    def __init__(self) -> None:
        self.outputs: list[Path] = []

    def __call__(self, command: ChildCommand) -> ChildOutcome:
        output = Path(command.argv[command.argv.index("-o") + 1])
        output.write_text("binary", encoding="utf-8")
        self.outputs.append(output)
        return ChildOutcome(exit_code=0, stdout="", stderr="")


# An empty value counts as unset, as it does for every other variable ARDE reads.
@pytest.mark.parametrize("value", [None, ""])
def test_with_no_tool_named_both_are_built_into_the_folder(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, value: str | None
) -> None:
    build = CountingBuild()
    for module in (goinspect_client, swag):
        monkeypatch.setattr(module, "run_child", build)
        monkeypatch.setattr(module, "_built", set())
    for name in (GOINSPECT_VARIABLE, SWAG_VARIABLE):
        if value is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, value)

    go_adapter(tmp_path / "bin")

    assert sorted(path.name for path in build.outputs) == ["goinspect", "swag"]
    assert {path.parent for path in build.outputs} == {(tmp_path / "bin").resolve()}

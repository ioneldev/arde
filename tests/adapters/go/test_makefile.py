"""Make targets come from a regex over the top-level Makefile, and a repo without one has none."""

import os
from pathlib import Path

import pytest

from arde.adapters.language.go.makefile import (
    MAKEFILE_BYTES_LIMIT,
    MakefileNotReadableError,
    make_targets,
    make_targets_of,
)
from arde.domain.errors import ArdeError
from arde.domain.report import ExitCode


def test_a_target_is_a_name_before_a_colon() -> None:
    text = "serve:\n\tgo run .\n\nunit: lint\n\tgo test ./...\n"

    assert make_targets(text) == ("serve", "unit")


def test_targets_are_sorted_and_listed_once() -> None:
    assert make_targets("b:\n\tx\na:\n\tx\nb:\n\ty\n") == ("a", "b")


def test_several_targets_on_one_line_are_all_listed() -> None:
    assert make_targets("first second: shared\n\tx\n") == ("first", "second")


@pytest.mark.parametrize("name", ["docker-build", "build.linux", "run_all", "ci/test", "9lives"])
def test_a_target_name_may_hold_dash_dot_underscore_and_slash(name: str) -> None:
    assert make_targets(f"{name}:\n\tx\n") == (name,)


def test_a_double_colon_rule_is_a_target() -> None:
    assert make_targets("all::\n\tx\n") == ("all",)


@pytest.mark.parametrize(
    "line",
    [
        ".PHONY: serve unit",
        ".DEFAULT_GOAL := serve",
        "BIN := bin/app",
        "BIN ::= bin/app",
        "URL = http://example.com",
        "PORT ?= 8080",
        "export A := b:c",
        "\trecipe line: with a colon",
        "%.o: %.c",
        "$(BIN): main.go",
        "# comment: not a target",
        "ifeq ($(OS),Windows_NT)",
        "include other.mk",
        "",
    ],
)
def test_a_line_that_is_not_a_rule_adds_no_target(line: str) -> None:
    assert make_targets(f"{line}\n") == ()


def test_a_target_specific_variable_still_names_its_target() -> None:
    assert make_targets("debug: FLAGS = -g\n") == ("debug",)


def test_a_repo_with_no_makefile_has_no_targets(tmp_path: Path) -> None:
    assert make_targets_of(tmp_path) == ()


def test_the_makefile_in_the_repo_root_is_read(tmp_path: Path) -> None:
    (tmp_path / "Makefile").write_text("serve:\n\tx\n", encoding="utf-8")

    assert make_targets_of(tmp_path) == ("serve",)


def test_the_name_make_looks_for_first_wins(tmp_path: Path) -> None:
    (tmp_path / "GNUmakefile").write_text("preferred:\n\tx\n", encoding="utf-8")
    (tmp_path / "Makefile").write_text("ignored:\n\tx\n", encoding="utf-8")

    assert make_targets_of(tmp_path) == ("preferred",)


def test_a_makefile_that_is_not_utf8_is_still_read(tmp_path: Path) -> None:
    (tmp_path / "Makefile").write_bytes(b"# caf\xe9\nserve:\n\tx\n")

    assert make_targets_of(tmp_path) == ("serve",)


def test_a_directory_named_makefile_is_not_a_makefile(tmp_path: Path) -> None:
    (tmp_path / "Makefile").mkdir()

    assert make_targets_of(tmp_path) == ()


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads every file")
def test_a_makefile_that_cannot_be_read_stops_with_a_next_action_and_no_raw_error(
    tmp_path: Path,
) -> None:
    makefile = tmp_path / "Makefile"
    makefile.write_text("serve:\n\tx\n", encoding="utf-8")
    makefile.chmod(0o000)
    try:
        with pytest.raises(MakefileNotReadableError) as stopped:
            make_targets_of(tmp_path)
    finally:
        makefile.chmod(0o644)

    assert isinstance(stopped.value, ArdeError)
    assert stopped.value.exit_code == ExitCode.BAD_USAGE
    assert stopped.value.next_action
    # A case-insensitive disk finds this file under the name make tries first.
    assert "makefile" in str(stopped.value).lower()


def test_a_makefile_past_the_limit_is_refused_before_it_is_read(tmp_path: Path) -> None:
    with (tmp_path / "Makefile").open("wb") as makefile:
        makefile.truncate(MAKEFILE_BYTES_LIMIT + 1)

    with pytest.raises(MakefileNotReadableError) as stopped:
        make_targets_of(tmp_path)

    assert str(MAKEFILE_BYTES_LIMIT + 1) in str(stopped.value)


def test_a_makefile_of_exactly_the_limit_is_read(tmp_path: Path) -> None:
    rule = b"serve:\n\tx\n"
    padding = b"#" * (MAKEFILE_BYTES_LIMIT - len(rule) - 1) + b"\n"
    (tmp_path / "Makefile").write_bytes(padding + rule)

    assert make_targets_of(tmp_path) == ("serve",)

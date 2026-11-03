"""Which patterns ARDE asks goinspect to apply, and what Discovery is told. Nothing here runs Go."""

import re
from pathlib import Path

import pytest

from arde.adapters.language.go.adapter import (
    DEFAULT_ENV_PATTERN,
    DEFAULT_PARAM_PATTERNS,
    GoAdapter,
    patterns_of,
)
from arde.domain.profile import REQUIRED_ARGUMENTS, ExtractionPattern, RepoProfile


def test_the_default_env_pattern_reads_both_ways_of_asking_for_a_variable() -> None:
    assert DEFAULT_ENV_PATTERN.call == r"^os\.(Getenv|LookupEnv)$"


def test_the_default_param_patterns_read_each_request_value_by_its_name() -> None:
    assert [(p.kind, p.call, p.args) for p in DEFAULT_PARAM_PATTERNS] == [
        ("param", r"^[A-Za-z_]\w*\.URL\.Query\(\)\.Get$", {"name": 0}),
        ("param", r"^[A-Za-z_]\w*\.(PathValue|FormValue|PostFormValue)$", {"name": 0}),
        ("param", r"^[A-Za-z_]\w*\.Header\.Get$", {"name": 0}),
    ]


@pytest.mark.parametrize(
    ("callee", "read"),
    [
        ("r.URL.Query().Get", True),
        ("req.URL.Query().Get", True),
        ("r.PathValue", True),
        ("request.FormValue", True),
        ("r.PostFormValue", True),
        ("r.Header.Get", True),
        ("w.Header().Get", False),
        ("query.Get", False),
        ("s.r.URL.Query().Get", False),
    ],
)
def test_the_default_param_patterns_read_a_request_whatever_it_is_called(
    callee: str, read: bool
) -> None:
    assert any(re.search(p.call, callee) for p in DEFAULT_PARAM_PATTERNS) is read


def test_the_default_patterns_go_after_the_profiles_own() -> None:
    own = ExtractionPattern(kind="env", call="^setting$", args={"name": 0})
    profile = RepoProfile(main_package=".", main_file="main.go", port_env="PORT", patterns=(own,))

    assert patterns_of(profile) == (own, *DEFAULT_PARAM_PATTERNS, DEFAULT_ENV_PATTERN)


def guidance() -> str:
    return GoAdapter(Path("unused"), swag=Path("unused")).discovery_guidance()


@pytest.mark.parametrize("kind", sorted(REQUIRED_ARGUMENTS))
def test_the_guidance_says_how_to_write_each_kind_of_pattern_with_its_arguments(kind: str) -> None:
    assert kind in guidance()
    for argument in REQUIRED_ARGUMENTS[kind]:
        assert f"`{argument}`" in guidance()


@pytest.mark.parametrize("field", ["main_package", "main_file", "port_env", "patterns"])
def test_the_guidance_covers_what_discovery_must_set(field: str) -> None:
    assert field in guidance()


def test_the_guidance_says_what_is_read_with_no_pattern() -> None:
    assert "net/http" in guidance()
    assert "os.Getenv" in guidance()
    for call in ("URL.Query().Get", "PathValue", "FormValue", "PostFormValue", "Header.Get"):
        assert call in guidance()


# The prompt is hashed into the trace, so its text must not change from one run to the next.
def test_the_guidance_is_the_same_text_every_time() -> None:
    assert guidance() == GoAdapter(Path("other"), swag=Path("other")).discovery_guidance()

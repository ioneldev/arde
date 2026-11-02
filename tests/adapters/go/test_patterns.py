"""Which patterns ARDE asks goinspect to apply, and what Discovery is told. Nothing here runs Go."""

from pathlib import Path

import pytest

from arde.adapters.language.go.adapter import DEFAULT_ENV_PATTERN, GoAdapter, patterns_of
from arde.domain.profile import REQUIRED_ARGUMENTS, ExtractionPattern, RepoProfile


def test_the_default_env_pattern_reads_both_ways_of_asking_for_a_variable() -> None:
    assert DEFAULT_ENV_PATTERN.call == r"^os\.(Getenv|LookupEnv)$"


def test_the_default_env_pattern_goes_last() -> None:
    own = ExtractionPattern(kind="env", call="^setting$", args={"name": 0})
    profile = RepoProfile(main_package=".", main_file="main.go", port_env="PORT", patterns=(own,))

    assert patterns_of(profile) == (own, DEFAULT_ENV_PATTERN)


def guidance() -> str:
    return GoAdapter(Path("unused")).discovery_guidance()


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


# The prompt is hashed into the trace, so its text must not change from one run to the next.
def test_the_guidance_is_the_same_text_every_time() -> None:
    assert guidance() == GoAdapter(Path("other")).discovery_guidance()

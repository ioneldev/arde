"""Which patterns ARDE asks goinspect to apply. Nothing here runs Go."""

from arde.adapters.language.go.adapter import DEFAULT_ENV_PATTERN, patterns_of
from arde.domain.profile import ExtractionPattern, RepoProfile


def test_the_default_env_pattern_reads_both_ways_of_asking_for_a_variable() -> None:
    assert DEFAULT_ENV_PATTERN.call == r"^os\.(Getenv|LookupEnv)$"


def test_the_default_env_pattern_goes_last() -> None:
    own = ExtractionPattern(kind="env", call="^setting$", args={"name": 0})
    profile = RepoProfile(main_package=".", main_file="main.go", port_env="PORT", patterns=(own,))

    assert patterns_of(profile) == (own, DEFAULT_ENV_PATTERN)

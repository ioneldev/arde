"""What a child process may inherit. An allow-list, because a deny-list forgets the next secret."""

import pytest

from arde.adapters.child_env import ALLOWED_VARIABLES, child_environment

SECRETS_AND_RISKS = {
    "ANTHROPIC_API_KEY": "not-a-real-key",
    "ANTHROPIC_BASE_URL": "https://example.invalid",
    "AWS_SECRET_ACCESS_KEY": "not-a-real-secret",
    "GITHUB_TOKEN": "not-a-real-token",
    "ARDE_HOME": "/somewhere",
    "GOFLAGS": "-toolexec=/tmp/run-me",
}


@pytest.fixture
def clean_environment(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    for name in (*ALLOWED_VARIABLES, *SECRETS_AND_RISKS):
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


def test_the_allow_list_is_exactly_what_the_toolchain_needs() -> None:
    assert set(ALLOWED_VARIABLES) == {
        "PATH",
        "HOME",
        "TMPDIR",
        "LANG",
        "LC_ALL",
        "GOPATH",
        "GOCACHE",
        "GOMODCACHE",
        "GOROOT",
        "GOPROXY",
    }


def test_a_child_gets_the_allowed_variables_that_are_set(
    clean_environment: pytest.MonkeyPatch,
) -> None:
    clean_environment.setenv("PATH", "/usr/bin")
    clean_environment.setenv("GOPATH", "/work/go")

    assert child_environment() == {"PATH": "/usr/bin", "GOPATH": "/work/go", "GOENV": "off"}


def test_a_variable_that_is_not_set_is_not_invented(
    clean_environment: pytest.MonkeyPatch,
) -> None:
    assert child_environment() == {"GOENV": "off"}


@pytest.mark.parametrize("name", SECRETS_AND_RISKS)
def test_nothing_outside_the_allow_list_reaches_a_child(
    clean_environment: pytest.MonkeyPatch, name: str
) -> None:
    clean_environment.setenv("PATH", "/usr/bin")
    clean_environment.setenv(name, SECRETS_AND_RISKS[name])

    assert name not in child_environment()


def test_goflags_stays_out_because_it_can_name_a_program_to_run(
    clean_environment: pytest.MonkeyPatch,
) -> None:
    clean_environment.setenv("GOFLAGS", SECRETS_AND_RISKS["GOFLAGS"])

    assert "GOFLAGS" not in ALLOWED_VARIABLES
    assert "GOFLAGS" not in child_environment()


def test_the_go_env_file_is_switched_off_because_it_can_carry_goflags(
    clean_environment: pytest.MonkeyPatch,
) -> None:
    assert child_environment()["GOENV"] == "off"


def test_the_parents_choice_of_env_file_does_not_reach_a_child(
    clean_environment: pytest.MonkeyPatch,
) -> None:
    clean_environment.setenv("GOENV", "/somewhere/with/flags")

    assert child_environment()["GOENV"] == "off"

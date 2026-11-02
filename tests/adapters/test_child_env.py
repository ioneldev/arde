"""What a child process may inherit. An allow-list, because a deny-list forgets the next secret.

This is the generic list, which a service started for the judge gets too. The toolchain's own
variables are in the Go adapter, and a service must get none of them.
"""

import pytest

from arde.adapters.child_env import ALLOWED_VARIABLES, child_environment

SECRETS_AND_RISKS = {
    "ANTHROPIC_API_KEY": "not-a-real-key",
    "ANTHROPIC_BASE_URL": "https://example.invalid",
    "AWS_SECRET_ACCESS_KEY": "not-a-real-secret",
    "GITHUB_TOKEN": "not-a-real-token",
    "ARDE_HOME": "/somewhere",
}

TOOLCHAIN_VARIABLES = (
    "GOPATH",
    "GOCACHE",
    "GOMODCACHE",
    "GOROOT",
    "GOPROXY",
    "GOPRIVATE",
    "GONOPROXY",
    "GONOSUMDB",
    "GOSUMDB",
    "GOINSECURE",
    "GOTOOLCHAIN",
    "GOFLAGS",
    "GOENV",
)


@pytest.fixture
def clean_environment(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    for name in (*ALLOWED_VARIABLES, *SECRETS_AND_RISKS, *TOOLCHAIN_VARIABLES):
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


def test_the_allow_list_is_only_what_any_program_needs() -> None:
    assert set(ALLOWED_VARIABLES) == {"PATH", "HOME", "TMPDIR", "LANG", "LC_ALL"}


def test_a_child_gets_the_allowed_variables_that_are_set(
    clean_environment: pytest.MonkeyPatch,
) -> None:
    clean_environment.setenv("PATH", "/usr/bin")
    clean_environment.setenv("LANG", "C.UTF-8")

    assert child_environment() == {"PATH": "/usr/bin", "LANG": "C.UTF-8"}


def test_a_variable_that_is_not_set_is_not_invented(
    clean_environment: pytest.MonkeyPatch,
) -> None:
    assert child_environment() == {}


@pytest.mark.parametrize("name", SECRETS_AND_RISKS)
def test_nothing_outside_the_allow_list_reaches_a_child(
    clean_environment: pytest.MonkeyPatch, name: str
) -> None:
    clean_environment.setenv("PATH", "/usr/bin")
    clean_environment.setenv(name, SECRETS_AND_RISKS[name])

    assert name not in child_environment()


@pytest.mark.parametrize("name", TOOLCHAIN_VARIABLES)
def test_a_service_style_child_gets_no_toolchain_variable(
    clean_environment: pytest.MonkeyPatch, name: str
) -> None:
    for each in TOOLCHAIN_VARIABLES:
        clean_environment.setenv(each, "set-by-the-test")

    environment = child_environment()

    assert name not in environment
    assert not [variable for variable in environment if variable.startswith("GO")]

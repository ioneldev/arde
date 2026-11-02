"""The environment of every Go command: the generic list, the toolchain's variables, three settings.

The three settings are GOPRIVATE, GONOPROXY and GONOSUMDB. A user may have stored them with
"go env -w", and without them a private module is fetched through the public proxy and checked
against the public checksum database.
"""

import os
import subprocess
from pathlib import Path

import pytest

from arde.adapters.child_process import ChildCommand, ChildOutcome, run_child
from arde.adapters.language.go import go_environment
from arde.adapters.language.go.errors import GoCommandError
from arde.adapters.language.go.go_environment import (
    TOOLCHAIN_VARIABLES,
    USER_SETTINGS,
)
from arde.adapters.language.go.go_environment import (
    go_environment as read_go_environment,
)
from arde.adapters.language.go.output_limits import CHECK_OUTPUT_LIMIT

REAL_PATH = os.environ["PATH"]

EVERY_VARIABLE_OF_THE_GENERIC_LIST_AND_THE_TOOLCHAIN = (
    "PATH",
    "HOME",
    "TMPDIR",
    "LANG",
    "LC_ALL",
    "GOFLAGS",
    "GOENV",
    *TOOLCHAIN_VARIABLES,
)


class StandInGoEnv:
    """Plays "go env -json" with the settings it was given, and records how it was asked."""

    def __init__(self, settings: dict[str, str] | None = None, *, exit_code: int = 0) -> None:
        self.settings = settings or {}
        self.exit_code = exit_code
        self.stdout: str | None = None
        self.asked: list[ChildCommand] = []
        self.folder_contents: list[list[str]] = []

    def __call__(self, command: ChildCommand) -> ChildOutcome:
        self.asked.append(command)
        self.folder_contents.append(sorted(path.name for path in command.cwd.iterdir()))
        reply = self.stdout
        if reply is None:
            reply = "{" + ", ".join(f'"{k}": "{v}"' for k, v in self.settings.items()) + "}"
        return ChildOutcome(exit_code=self.exit_code, stdout=reply, stderr="go: oops\n")


@pytest.fixture(autouse=True)
def read_the_user_file_afresh() -> None:
    go_environment._user_settings.cache_clear()


@pytest.fixture
def clean_environment(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    for name in EVERY_VARIABLE_OF_THE_GENERIC_LIST_AND_THE_TOOLCHAIN:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("PATH", "/usr/bin")
    return monkeypatch


def stand_in(monkeypatch: pytest.MonkeyPatch, **kwargs: object) -> StandInGoEnv:
    go = StandInGoEnv(**kwargs)  # type: ignore[arg-type]
    monkeypatch.setattr(go_environment, "run_child", go)
    return go


@pytest.mark.parametrize("name", TOOLCHAIN_VARIABLES)
def test_a_toolchain_variable_that_is_exported_reaches_a_command(
    clean_environment: pytest.MonkeyPatch, name: str
) -> None:
    stand_in(clean_environment)
    clean_environment.setenv(name, "exported-value")

    assert read_go_environment()[name] == "exported-value"


def test_an_exported_goprivate_reaches_a_command_with_the_generic_variables(
    clean_environment: pytest.MonkeyPatch,
) -> None:
    stand_in(clean_environment)
    clean_environment.setenv("GOPRIVATE", "corp.example/*")

    assert read_go_environment() == {
        "PATH": "/usr/bin",
        "GOPRIVATE": "corp.example/*",
        "GOENV": "off",
    }


def test_goflags_stays_out_because_it_can_name_a_program_to_run(
    clean_environment: pytest.MonkeyPatch,
) -> None:
    stand_in(clean_environment)
    clean_environment.setenv("GOFLAGS", "-toolexec=/tmp/run-me")

    assert "GOFLAGS" not in TOOLCHAIN_VARIABLES
    assert "GOFLAGS" not in read_go_environment()


def test_the_go_env_file_is_switched_off_whatever_the_parent_says(
    clean_environment: pytest.MonkeyPatch,
) -> None:
    stand_in(clean_environment)
    clean_environment.setenv("GOENV", "/somewhere/with/flags")

    assert read_go_environment()["GOENV"] == "off"


def test_nothing_is_invented_for_a_variable_that_is_not_set(
    clean_environment: pytest.MonkeyPatch,
) -> None:
    stand_in(clean_environment)

    assert read_go_environment() == {"PATH": "/usr/bin", "GOENV": "off"}


@pytest.mark.parametrize("name", USER_SETTINGS)
def test_a_setting_stored_in_the_users_go_env_file_reaches_a_command(
    clean_environment: pytest.MonkeyPatch, name: str
) -> None:
    stand_in(clean_environment, settings={name: "corp.example/*"})

    assert read_go_environment()[name] == "corp.example/*"


def test_the_three_settings_are_exactly_the_ones_that_keep_private_paths_private() -> None:
    assert USER_SETTINGS == ("GOPRIVATE", "GONOPROXY", "GONOSUMDB")


def test_a_value_in_the_environment_beats_the_file(
    clean_environment: pytest.MonkeyPatch,
) -> None:
    stand_in(clean_environment, settings={"GOPRIVATE": "from-the-file"})
    clean_environment.setenv("GOPRIVATE", "from-the-environment")

    assert read_go_environment()["GOPRIVATE"] == "from-the-environment"


def test_an_empty_value_in_the_file_adds_nothing(clean_environment: pytest.MonkeyPatch) -> None:
    stand_in(clean_environment, settings={"GOPRIVATE": "", "GONOPROXY": ""})

    assert "GOPRIVATE" not in read_go_environment()


def test_settings_that_could_redirect_downloads_are_never_taken_from_the_file(
    clean_environment: pytest.MonkeyPatch,
) -> None:
    stand_in(
        clean_environment,
        settings={
            "GOPRIVATE": "corp.example/*",
            "GOPROXY": "http://evil.invalid",
            "GOSUMDB": "off",
            "GOTOOLCHAIN": "go9.9",
            "GOFLAGS": "-toolexec=/tmp/run-me",
            "GOINSECURE": "evil.invalid",
        },
    )

    environment = read_go_environment()

    assert environment["GOPRIVATE"] == "corp.example/*"
    for name in ("GOPROXY", "GOSUMDB", "GOTOOLCHAIN", "GOFLAGS", "GOINSECURE"):
        assert name not in environment


def test_the_file_is_read_once_per_process(clean_environment: pytest.MonkeyPatch) -> None:
    go = stand_in(clean_environment, settings={"GOPRIVATE": "corp.example/*"})

    for _ in range(5):
        read_go_environment()

    assert len(go.asked) == 1


def test_the_file_is_asked_for_by_go_env_from_a_folder_that_belongs_to_no_repo(
    clean_environment: pytest.MonkeyPatch,
) -> None:
    clean_environment.setenv("GOFLAGS", "-toolexec=/tmp/run-me")
    clean_environment.setenv("GOPRIVATE", "exported")
    go = stand_in(clean_environment)

    read_go_environment()

    [asked] = go.asked
    assert asked.argv[:3] == ("go", "env", "-json")
    assert set(asked.argv[3:]) == set(USER_SETTINGS)
    assert go.folder_contents == [[]], "an empty folder, so no go.mod can reach the command"
    assert not asked.cwd.exists(), "and it is gone afterwards"
    assert asked.timeout_s > 0
    assert "GOENV" not in asked.env, "or the command would read nothing"
    assert "GOFLAGS" not in asked.env
    assert "GOPRIVATE" not in asked.env


@pytest.mark.parametrize(
    "stdout",
    ["", "not json", "[]", '{"GOPRIVATE": 3}', '{"GOPRIVATE": null}'],
)
def test_a_reply_that_cannot_be_read_stops_the_run(
    clean_environment: pytest.MonkeyPatch, stdout: str
) -> None:
    go = stand_in(clean_environment)
    go.stdout = stdout

    with pytest.raises(GoCommandError) as stopped:
        read_go_environment()

    assert "go env" in str(stopped.value)
    assert stopped.value.next_action


def test_a_failing_go_env_stops_the_run_and_does_not_go_on_without_the_settings(
    clean_environment: pytest.MonkeyPatch,
) -> None:
    stand_in(clean_environment, exit_code=2)

    with pytest.raises(GoCommandError) as stopped:
        read_go_environment()

    assert "go: oops" in str(stopped.value)
    assert "private" in stopped.value.next_action


def test_a_failure_is_not_remembered(clean_environment: pytest.MonkeyPatch) -> None:
    stand_in(clean_environment, exit_code=2)
    with pytest.raises(GoCommandError):
        read_go_environment()
    stand_in(clean_environment, settings={"GOPRIVATE": "corp.example/*"})

    assert read_go_environment()["GOPRIVATE"] == "corp.example/*"


# Against the real toolchain. The go env file lives under HOME, so HOME is pointed at a folder
# that holds one.


@pytest.fixture
def home_with_a_go_env_file(clean_environment: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    clean_environment.setenv("HOME", str(home))
    clean_environment.setenv("PATH", REAL_PATH)
    clean_environment.delenv("XDG_CONFIG_HOME", raising=False)
    plain_go = {"HOME": str(home), "PATH": REAL_PATH}
    where = subprocess.run(
        ["go", "env", "GOENV"], env=plain_go, capture_output=True, text=True, check=True
    ).stdout.strip()
    env_file = Path(where)
    env_file.parent.mkdir(parents=True, exist_ok=True)
    env_file.write_text(
        "GOPRIVATE=corp.example/*\n"
        "GOFLAGS=-mod=readonly\n"
        "GOPROXY=http://evil.invalid\n"
        "GOSUMDB=off\n",
        encoding="utf-8",
    )
    return home


def ask_go(arguments: tuple[str, ...], cwd: Path) -> dict[str, str]:
    command = ChildCommand(
        argv=("go", "env", *arguments),
        cwd=cwd,
        timeout_s=60,
        output=CHECK_OUTPUT_LIMIT,
        env=read_go_environment(),
    )
    values = run_child(command).stdout.splitlines()
    return dict(zip(arguments, values, strict=True))


@pytest.mark.integration
def test_a_private_path_in_the_users_go_env_file_reaches_a_command_and_nothing_else_there_does(
    home_with_a_go_env_file: Path, tmp_path: Path
) -> None:
    control = subprocess.run(
        ["go", "env", "GOFLAGS", "GOPROXY"],
        env={"HOME": str(home_with_a_go_env_file), "PATH": REAL_PATH},
        capture_output=True,
        text=True,
        check=True,
    ).stdout.splitlines()
    assert control == ["-mod=readonly", "http://evil.invalid"], "the control must read the file"

    seen = ask_go(("GOPRIVATE", "GOFLAGS", "GOPROXY", "GOSUMDB"), tmp_path)

    assert seen["GOPRIVATE"] == "corp.example/*"
    assert seen["GOFLAGS"] == ""
    assert "evil.invalid" not in seen["GOPROXY"]
    assert seen["GOSUMDB"] != "off"


@pytest.mark.integration
def test_a_value_exported_in_the_environment_beats_the_users_go_env_file(
    home_with_a_go_env_file: Path,
    clean_environment: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    clean_environment.setenv("GOPRIVATE", "exported.example/*")

    assert ask_go(("GOPRIVATE",), tmp_path)["GOPRIVATE"] == "exported.example/*"

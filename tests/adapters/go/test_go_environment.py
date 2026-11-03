"""The environment of every Go command: the generic list, the toolchain's variables, three settings.

The three settings are GOPRIVATE, GONOPROXY and GONOSUMDB. A user may have stored them with
"go env -w", and without them a private module is fetched through the public proxy and checked
against the public checksum database. Go itself decides which value wins, so the answer of
"go env" is used as it stands.
"""

import json
import os
import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest

from arde.adapters.child_env import ALLOWED_VARIABLES, child_environment
from arde.adapters.child_process import ChildCommand, ChildOutcome, run_child
from arde.adapters.child_text import MESSAGE_TAIL_BYTES
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
    *ALLOWED_VARIABLES,
    "XDG_CONFIG_HOME",
    "GOFLAGS",
    "GOENV",
    *TOOLCHAIN_VARIABLES,
    *USER_SETTINGS,
)


class StandInGoEnv:
    """Plays "go env -json" the way Go answers it, and records how it was asked.

    A value in the environment of the command wins over the file. GONOPROXY and GONOSUMDB, when
    neither holds them, are the resolved GOPRIVATE, as Go derives them. A reply may also carry
    names that were not asked for, to show that they are not used.
    """

    def __init__(
        self,
        file: dict[str, str] | None = None,
        *,
        extras: dict[str, str] | None = None,
        exit_code: int = 0,
    ) -> None:
        self.file = file or {}
        self.extras = extras or {}
        self.exit_code = exit_code
        self.stdout: str | None = None
        self.stderr = "go: oops\n"
        self.asked: list[ChildCommand] = []
        self.folder_contents: list[list[str]] = []

    def __call__(self, command: ChildCommand) -> ChildOutcome:
        self.asked.append(command)
        self.folder_contents.append(sorted(path.name for path in command.cwd.iterdir()))
        reply = self.stdout if self.stdout is not None else json.dumps(self._answer(command))
        return ChildOutcome(exit_code=self.exit_code, stdout=reply, stderr=self.stderr)

    def _answer(self, command: ChildCommand) -> dict[str, str]:
        def known(name: str) -> str:
            return command.env.get(name) or self.file.get(name, "")

        private = known("GOPRIVATE")
        resolved = {
            "GOPRIVATE": private,
            "GONOPROXY": known("GONOPROXY") or private,
            "GONOSUMDB": known("GONOSUMDB") or private,
        }
        return {**self.extras, **{name: resolved[name] for name in command.argv[3:]}}


# On the way out too: the cache lasts as long as the process, so a stand-in's values would otherwise
# reach every Go command of the tests that run after this file.
@pytest.fixture(autouse=True)
def read_the_user_file_afresh() -> Iterator[None]:
    go_environment._user_settings.cache_clear()
    yield
    go_environment._user_settings.cache_clear()


@pytest.fixture
def clean_environment(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    for name in EVERY_VARIABLE_OF_THE_GENERIC_LIST_AND_THE_TOOLCHAIN:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("PATH", "/usr/bin")
    return monkeypatch


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    folder = tmp_path / "repo"
    folder.mkdir()
    return folder


def stand_in(monkeypatch: pytest.MonkeyPatch, **kwargs: object) -> StandInGoEnv:
    go = StandInGoEnv(**kwargs)  # type: ignore[arg-type]
    monkeypatch.setattr(go_environment, "run_child", go)
    return go


@pytest.mark.parametrize("name", (*TOOLCHAIN_VARIABLES, *USER_SETTINGS))
def test_a_toolchain_variable_that_is_exported_reaches_a_command(
    clean_environment: pytest.MonkeyPatch, repo: Path, name: str
) -> None:
    stand_in(clean_environment)
    clean_environment.setenv(name, "exported-value")

    assert read_go_environment(repo)[name] == "exported-value"


def test_an_exported_goprivate_reaches_a_command_with_the_generic_variables(
    clean_environment: pytest.MonkeyPatch,
    repo: Path,
) -> None:
    stand_in(clean_environment)
    clean_environment.setenv("GOPRIVATE", "corp.example/*")

    assert read_go_environment(repo) == {
        "PATH": "/usr/bin",
        "GOPRIVATE": "corp.example/*",
        "GONOPROXY": "corp.example/*",
        "GONOSUMDB": "corp.example/*",
        "GOENV": "off",
        "GOWORK": "off",
        "GOTOOLCHAIN": "local",
    }


def test_goflags_stays_out_because_it_can_name_a_program_to_run(
    clean_environment: pytest.MonkeyPatch,
    repo: Path,
) -> None:
    stand_in(clean_environment)
    clean_environment.setenv("GOFLAGS", "-toolexec=/tmp/run-me")

    assert "GOFLAGS" not in TOOLCHAIN_VARIABLES
    assert "GOFLAGS" not in read_go_environment(repo)


def test_the_go_env_file_is_switched_off_for_every_command_whatever_the_parent_says(
    clean_environment: pytest.MonkeyPatch,
    repo: Path,
) -> None:
    stand_in(clean_environment)
    clean_environment.setenv("GOENV", "/somewhere/with/flags")

    assert read_go_environment(repo)["GOENV"] == "off"


def test_nothing_is_invented_for_a_variable_that_is_not_set(
    clean_environment: pytest.MonkeyPatch,
    repo: Path,
) -> None:
    stand_in(clean_environment)

    assert read_go_environment(repo) == {
        "PATH": "/usr/bin",
        "GOENV": "off",
        "GOWORK": "off",
        "GOTOOLCHAIN": "local",
    }


@pytest.mark.parametrize("name", USER_SETTINGS)
def test_a_setting_stored_in_the_users_go_env_file_reaches_a_command(
    clean_environment: pytest.MonkeyPatch,
    name: str,
    repo: Path,
) -> None:
    stand_in(clean_environment, file={name: "corp.example/*"})

    assert read_go_environment(repo)[name] == "corp.example/*"


def test_the_three_settings_are_exactly_the_ones_that_keep_private_paths_private() -> None:
    assert USER_SETTINGS == ("GOPRIVATE", "GONOPROXY", "GONOSUMDB")


def test_what_goes_to_a_command_is_the_answer_of_go_env_for_the_three_settings(
    clean_environment: pytest.MonkeyPatch,
    repo: Path,
) -> None:
    stand_in(clean_environment, file={"GOPRIVATE": "a", "GONOPROXY": "c"})
    clean_environment.setenv("GOPRIVATE", "b")

    environment = read_go_environment(repo)

    assert (environment["GOPRIVATE"], environment["GONOPROXY"], environment["GONOSUMDB"]) == (
        "b",
        "c",
        "b",
    )


def test_an_empty_answer_adds_nothing(clean_environment: pytest.MonkeyPatch, repo: Path) -> None:
    stand_in(clean_environment)

    assert not [name for name in read_go_environment(repo) if name in USER_SETTINGS]


def test_settings_that_could_redirect_downloads_are_never_taken_from_the_file(
    clean_environment: pytest.MonkeyPatch,
    repo: Path,
) -> None:
    stand_in(
        clean_environment,
        file={"GOPRIVATE": "corp.example/*"},
        extras={
            "GOPROXY": "http://evil.invalid",
            "GOSUMDB": "off",
            "GOTOOLCHAIN": "go9.9",
            "GOFLAGS": "-toolexec=/tmp/run-me",
            "GOINSECURE": "evil.invalid",
        },
    )

    environment = read_go_environment(repo)

    assert environment["GOPRIVATE"] == "corp.example/*"
    assert environment["GOTOOLCHAIN"] == "local"
    for name in ("GOPROXY", "GOSUMDB", "GOFLAGS", "GOINSECURE"):
        assert name not in environment


def test_the_file_is_read_once_per_process(
    clean_environment: pytest.MonkeyPatch, repo: Path
) -> None:
    go = stand_in(clean_environment, file={"GOPRIVATE": "corp.example/*"})

    for _ in range(5):
        read_go_environment(repo)

    assert len(go.asked) == 1


def test_go_env_is_asked_from_a_folder_that_belongs_to_no_repo(
    clean_environment: pytest.MonkeyPatch,
    repo: Path,
) -> None:
    go = stand_in(clean_environment)

    read_go_environment(repo)

    [asked] = go.asked
    assert asked.argv[:3] == ("go", "env", "-json")
    assert set(asked.argv[3:]) == set(USER_SETTINGS)
    assert go.folder_contents == [[]], "an empty folder, so no go.mod can reach the command"
    assert not asked.cwd.exists(), "and it is gone afterwards"
    assert asked.timeout_s > 0


def test_the_probe_gets_the_exported_settings_so_that_go_resolves_them(
    clean_environment: pytest.MonkeyPatch,
    repo: Path,
) -> None:
    clean_environment.setenv("GOPRIVATE", "b")
    clean_environment.setenv("GONOPROXY", "c")
    clean_environment.setenv("GONOSUMDB", "d")
    go = stand_in(clean_environment)

    read_go_environment(repo)

    [asked] = go.asked
    assert (asked.env["GOPRIVATE"], asked.env["GONOPROXY"], asked.env["GONOSUMDB"]) == (
        "b",
        "c",
        "d",
    )
    assert asked.env["PATH"] == "/usr/bin"


def test_the_probe_alone_gets_the_users_config_folder_and_env_file_so_it_looks_where_go_does(
    clean_environment: pytest.MonkeyPatch,
    repo: Path,
) -> None:
    clean_environment.setenv("XDG_CONFIG_HOME", "/home/user/.config-elsewhere")
    clean_environment.setenv("GOENV", "/home/user/go-env-file")
    go = stand_in(clean_environment)

    environment = read_go_environment(repo)

    [asked] = go.asked
    assert asked.env["XDG_CONFIG_HOME"] == "/home/user/.config-elsewhere"
    assert asked.env["GOENV"] == "/home/user/go-env-file"
    assert "XDG_CONFIG_HOME" not in environment, "no normal child gets it"
    assert environment["GOENV"] == "off"
    assert "XDG_CONFIG_HOME" not in child_environment()


def test_the_probe_does_not_get_what_could_change_the_answer_or_run_a_program(
    clean_environment: pytest.MonkeyPatch,
    repo: Path,
) -> None:
    clean_environment.setenv("GOFLAGS", "-toolexec=/tmp/run-me")
    clean_environment.setenv("GOPROXY", "http://elsewhere.invalid")
    go = stand_in(clean_environment)

    read_go_environment(repo)

    [asked] = go.asked
    for name in ("GOFLAGS", "GOPROXY"):
        assert name not in asked.env


def test_the_probe_runs_with_the_local_toolchain_whatever_is_exported(
    clean_environment: pytest.MonkeyPatch,
    repo: Path,
) -> None:
    clean_environment.setenv("GOTOOLCHAIN", "go9.9")
    go = stand_in(clean_environment)

    environment = read_go_environment(repo)

    [asked] = go.asked
    assert asked.env["GOTOOLCHAIN"] == "local"
    assert environment["GOTOOLCHAIN"] == "go9.9", "a normal command still gets what is exported"


@pytest.mark.parametrize(
    "stdout",
    ["", "not json", "[]", '{"GOPRIVATE": 3}', '{"GOPRIVATE": null}'],
)
def test_a_reply_that_cannot_be_read_stops_the_run(
    clean_environment: pytest.MonkeyPatch,
    stdout: str,
    repo: Path,
) -> None:
    go = stand_in(clean_environment)
    go.stdout = stdout

    with pytest.raises(GoCommandError) as stopped:
        read_go_environment(repo)

    assert "go env" in str(stopped.value)
    assert stopped.value.next_action


@pytest.mark.parametrize("missing", USER_SETTINGS)
def test_a_reply_that_lacks_a_requested_setting_stops_the_run_and_is_not_read_as_empty(
    clean_environment: pytest.MonkeyPatch,
    missing: str,
    repo: Path,
) -> None:
    go = stand_in(clean_environment)
    go.stdout = json.dumps({name: "x" for name in USER_SETTINGS if name != missing})

    with pytest.raises(GoCommandError) as stopped:
        read_go_environment(repo)

    assert missing in str(stopped.value)
    assert "private" in stopped.value.next_action


def test_a_failing_go_env_stops_the_run_and_does_not_go_on_without_the_settings(
    clean_environment: pytest.MonkeyPatch,
    repo: Path,
) -> None:
    stand_in(clean_environment, exit_code=2)

    with pytest.raises(GoCommandError) as stopped:
        read_go_environment(repo)

    assert "go: oops" in str(stopped.value)
    assert "private" in stopped.value.next_action


def test_a_failing_go_env_gives_a_short_clean_message(
    clean_environment: pytest.MonkeyPatch, repo: Path
) -> None:
    go = stand_in(clean_environment, exit_code=2)
    go.stderr = "\x1b[2J" + "noise\n" * 5000 + "the real reason\n"

    with pytest.raises(GoCommandError) as stopped:
        read_go_environment(repo)

    assert "the real reason" in str(stopped.value)
    assert "\x1b" not in str(stopped.value)
    assert len(str(stopped.value)) < 2 * MESSAGE_TAIL_BYTES


def test_a_failure_is_not_remembered(clean_environment: pytest.MonkeyPatch, repo: Path) -> None:
    stand_in(clean_environment, exit_code=2)
    with pytest.raises(GoCommandError):
        read_go_environment(repo)
    stand_in(clean_environment, file={"GOPRIVATE": "corp.example/*"})

    assert read_go_environment(repo)["GOPRIVATE"] == "corp.example/*"


def test_a_repo_cannot_choose_a_toolchain_unless_the_user_allows_it(
    clean_environment: pytest.MonkeyPatch, repo: Path
) -> None:
    stand_in(clean_environment)

    assert read_go_environment(repo)["GOTOOLCHAIN"] == "local"


def test_a_toolchain_setting_the_user_exports_wins_over_the_default(
    clean_environment: pytest.MonkeyPatch, repo: Path
) -> None:
    stand_in(clean_environment)
    clean_environment.setenv("GOTOOLCHAIN", "auto")

    assert read_go_environment(repo)["GOTOOLCHAIN"] == "auto"


# Go reads an empty value as unset, which means auto, so passing it on would undo the default.
def test_an_empty_export_is_no_choice_and_keeps_the_default(
    clean_environment: pytest.MonkeyPatch, repo: Path
) -> None:
    stand_in(clean_environment)
    clean_environment.setenv("GOTOOLCHAIN", "")

    assert read_go_environment(repo)["GOTOOLCHAIN"] == "local"


def test_a_repo_with_no_go_work_of_its_own_gets_no_workspace(
    clean_environment: pytest.MonkeyPatch, repo: Path
) -> None:
    stand_in(clean_environment)

    assert read_go_environment(repo)["GOWORK"] == "off"


def test_a_repos_own_go_work_is_named_whole_because_go_refuses_a_relative_path(
    clean_environment: pytest.MonkeyPatch, repo: Path
) -> None:
    stand_in(clean_environment)
    (repo / "go.work").write_text("go 1.27\n", encoding="utf-8")
    clean_environment.chdir(repo.parent)

    assert read_go_environment(Path("repo"))["GOWORK"] == str(repo.resolve() / "go.work")


def test_a_folder_named_go_work_is_no_workspace(
    clean_environment: pytest.MonkeyPatch, repo: Path
) -> None:
    stand_in(clean_environment)
    (repo / "go.work").mkdir()

    assert read_go_environment(repo)["GOWORK"] == "off"


def test_a_go_work_above_the_repo_is_no_workspace(
    clean_environment: pytest.MonkeyPatch, repo: Path
) -> None:
    stand_in(clean_environment)
    (repo.parent / "go.work").write_text("go 1.27\n", encoding="utf-8")

    assert read_go_environment(repo)["GOWORK"] == "off"


def test_an_exported_gowork_cannot_name_another_workspace(
    clean_environment: pytest.MonkeyPatch, repo: Path
) -> None:
    stand_in(clean_environment)
    clean_environment.setenv("GOWORK", "/elsewhere/go.work")

    assert read_go_environment(repo)["GOWORK"] == "off"


def test_each_repo_gets_its_own_answer_in_one_process(
    clean_environment: pytest.MonkeyPatch, repo: Path, tmp_path: Path
) -> None:
    stand_in(clean_environment)
    other = tmp_path / "other"
    other.mkdir()
    (other / "go.work").write_text("go 1.27\n", encoding="utf-8")

    assert read_go_environment(repo)["GOWORK"] == "off"
    assert read_go_environment(other)["GOWORK"] == str(other.resolve() / "go.work")


# Against the real toolchain, with the file named through GOENV, which works on every platform.


@pytest.fixture
def go_env_file(clean_environment: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    clean_environment.setenv("HOME", str(home))
    clean_environment.setenv("PATH", REAL_PATH)
    file = tmp_path / "go-env-file"
    clean_environment.setenv("GOENV", str(file))
    return file


def write_the_file(file: Path, **settings: str) -> None:
    file.write_text("".join(f"{name}={value}\n" for name, value in settings.items()), "utf-8")


def plain_go_env(names: tuple[str, ...], file: Path, **exported: str) -> dict[str, str]:
    """What go itself answers for the user, with the file and the exported variables."""
    environment = {"HOME": os.environ["HOME"], "PATH": REAL_PATH, "GOENV": str(file), **exported}
    answer = subprocess.run(
        ["go", "env", *names], env=environment, capture_output=True, text=True, check=True
    )
    return dict(zip(names, answer.stdout.splitlines(), strict=True))


def ask_go(names: tuple[str, ...], cwd: Path) -> dict[str, str]:
    command = ChildCommand(
        argv=("go", "env", *names),
        cwd=cwd,
        timeout_s=60,
        output=CHECK_OUTPUT_LIMIT,
        env=read_go_environment(cwd),
    )
    values = run_child(command).stdout.splitlines()
    return dict(zip(names, values, strict=True))


@pytest.mark.integration
def test_a_private_path_in_the_users_go_env_file_reaches_a_command_and_nothing_else_there_does(
    go_env_file: Path,
    tmp_path: Path,
) -> None:
    write_the_file(
        go_env_file,
        GOPRIVATE="corp.example/*",
        GOFLAGS="-mod=readonly",
        GOPROXY="http://evil.invalid",
        GOSUMDB="off",
    )
    control = plain_go_env(("GOFLAGS", "GOPROXY"), go_env_file)
    assert control == {"GOFLAGS": "-mod=readonly", "GOPROXY": "http://evil.invalid"}, (
        "the control must read the file"
    )

    seen = ask_go(
        ("GOPRIVATE", "GONOPROXY", "GONOSUMDB", "GOFLAGS", "GOPROXY", "GOSUMDB"), tmp_path
    )

    assert (seen["GOPRIVATE"], seen["GONOPROXY"], seen["GONOSUMDB"]) == ("corp.example/*",) * 3
    assert seen["GOFLAGS"] == ""
    assert "evil.invalid" not in seen["GOPROXY"]
    assert seen["GOSUMDB"] != "off"


@pytest.mark.integration
def test_the_file_goprivate_loses_to_an_exported_one_for_all_three_settings(
    go_env_file: Path,
    clean_environment: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    write_the_file(go_env_file, GOPRIVATE="a")
    clean_environment.setenv("GOPRIVATE", "b")
    assert plain_go_env(("GONOPROXY", "GONOSUMDB"), go_env_file, GOPRIVATE="b") == {
        "GONOPROXY": "b",
        "GONOSUMDB": "b",
    }, "this is what Go itself answers"

    seen = ask_go(("GOPRIVATE", "GONOPROXY", "GONOSUMDB"), tmp_path)

    assert seen == {"GOPRIVATE": "b", "GONOPROXY": "b", "GONOSUMDB": "b"}


@pytest.mark.integration
def test_a_gonoproxy_in_the_file_is_kept_beside_an_exported_goprivate_as_go_does(
    go_env_file: Path,
    clean_environment: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    write_the_file(go_env_file, GONOPROXY="c")
    clean_environment.setenv("GOPRIVATE", "b")
    assert plain_go_env(("GONOPROXY",), go_env_file, GOPRIVATE="b") == {"GONOPROXY": "c"}, (
        "this is what Go itself answers"
    )

    seen = ask_go(("GOPRIVATE", "GONOPROXY", "GONOSUMDB"), tmp_path)

    assert seen == {"GOPRIVATE": "b", "GONOPROXY": "c", "GONOSUMDB": "b"}


@pytest.mark.integration
def test_a_goprivate_in_the_file_alone_gives_all_three_settings_to_a_command(
    go_env_file: Path,
    tmp_path: Path,
) -> None:
    write_the_file(go_env_file, GOPRIVATE="a")

    seen = ask_go(("GOPRIVATE", "GONOPROXY", "GONOSUMDB"), tmp_path)

    assert seen == {"GOPRIVATE": "a", "GONOPROXY": "a", "GONOSUMDB": "a"}


@pytest.mark.integration
def test_every_command_still_runs_with_the_go_env_file_switched_off(
    go_env_file: Path,
    tmp_path: Path,
    repo: Path,
) -> None:
    write_the_file(
        go_env_file, GOPRIVATE="a", GOFLAGS="-mod=readonly", GOPROXY="http://evil.invalid"
    )
    assert plain_go_env(("GOFLAGS",), go_env_file) == {"GOFLAGS": "-mod=readonly"}, (
        "the control must read the file"
    )

    seen = ask_go(("GOFLAGS", "GOPROXY"), tmp_path)

    assert read_go_environment(repo)["GOENV"] == "off"
    assert seen["GOFLAGS"] == ""
    assert "evil.invalid" not in seen["GOPROXY"]


NAMED_TOOLCHAIN = "go1.28.0"


@pytest.mark.integration
def test_a_gotoolchain_in_the_file_cannot_start_a_program_found_on_path(
    go_env_file: Path,
    clean_environment: pytest.MonkeyPatch,
    tmp_path: Path,
    repo: Path,
) -> None:
    ran = tmp_path / "the-program-ran"
    program = tmp_path / "bin" / NAMED_TOOLCHAIN
    program.parent.mkdir()
    program.write_text(
        f'#!/bin/sh\ntouch {ran}\necho \'{{"GOPRIVATE": "", "GONOPROXY": "", "GONOSUMDB": ""}}\'\n',
        encoding="utf-8",
    )
    program.chmod(0o755)
    clean_environment.setenv("PATH", f"{program.parent}{os.pathsep}{REAL_PATH}")
    write_the_file(go_env_file, GOTOOLCHAIN=NAMED_TOOLCHAIN, GOPRIVATE="corp.example/*")
    control = subprocess.run(
        ["go", "env", "GOPRIVATE"],
        env={
            "HOME": os.environ["HOME"],
            "PATH": os.environ["PATH"],
            "GOENV": str(go_env_file),
        },
        capture_output=True,
        text=True,
        check=False,
    )
    assert ran.exists(), f"the control must start the program: {control.stdout} {control.stderr}"
    ran.unlink()

    environment = read_go_environment(repo)

    assert not ran.exists(), "the program named by the file's GOTOOLCHAIN was run"
    assert environment["GOPRIVATE"] == "corp.example/*"

"""Spec generation: swag turns a Go service's annotations into an OpenAPI 3.1 file.

The fixture's annotations and tags are written by hand, in the shape the renderer writes them, so
these tests need no renderer. The tests marked integration build and run the pinned swag.
"""

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml
from known_environment import known_go_environment
from openapi_spec_validator import OpenAPIV31SpecValidator, validate

from arde.adapters.child_process import ChildCommand, ChildOutcome
from arde.adapters.child_text import MESSAGE_TAIL_BYTES
from arde.adapters.language.go import swag
from arde.adapters.language.go.adapter import GoAdapter
from arde.adapters.language.go.errors import GoCommandError
from arde.adapters.language.go.go_environment import go_environment
from arde.adapters.language.go.output_limits import CHECK_OUTPUT_LIMIT
from arde.adapters.language.go.swag import SWAG_PACKAGE, SWAG_SOURCE, build_swag, run_swag
from arde.adapters.language.go.timeouts import GO_BUILD_TIMEOUT_S, SWAG_TIMEOUT_S
from arde.domain.profile import RepoProfile

ANNOTATED = Path(__file__).resolve().parents[2] / "fixtures" / "annotated"
# In a folder of its own, so the repo root holds no package and swag warns that it cannot name
# one, which is the noise a run must not fail on.
MAIN_FILE = "cmd/aviary/main.go"
SPEC_FILES = {"docs/swagger.json", "docs/swagger.yaml"}
NOISY_STDERR = "\x1b[2J" + "Generating web.Thing\n" * 5000 + "the real reason\n"


def annotated_profile() -> RepoProfile:
    return RepoProfile(
        main_package="./cmd/aviary", main_file=MAIN_FILE, port_env="AVIARY_PORT", patterns=()
    )


# A private copy, because swag writes into the repo it reads.
@pytest.fixture
def annotated(tmp_path: Path) -> Path:
    copy = tmp_path / "annotated"
    shutil.copytree(ANNOTATED, copy)
    return copy


def generated_spec(swag_binary: Path, repo: Path) -> Any:
    return json.loads(
        run_swag(repo, annotated_profile(), swag=swag_binary).read_text(encoding="utf-8")
    )


def schema(spec: Any, name: str) -> Any:
    return spec["components"]["schemas"][name]


def every_file(repo: Path) -> dict[str, bytes]:
    return {
        path.relative_to(repo).as_posix(): path.read_bytes()
        for path in repo.rglob("*")
        if path.is_file()
    }


def edit(path: Path, old: str, *, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    assert old in text, f"{old!r} is not in {path}"
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


# run_swag against real swag


@pytest.mark.integration
def test_generates_openapi_3_1_json_and_yaml(swag_binary: Path, annotated: Path) -> None:
    path = run_swag(annotated, annotated_profile(), swag=swag_binary)

    assert path == annotated / "docs" / "swagger.json"
    spec = json.loads(path.read_text(encoding="utf-8"))
    assert spec["openapi"] == "3.1.0"
    yaml_text = (annotated / "docs" / "swagger.yaml").read_text(encoding="utf-8")
    assert yaml.safe_load(yaml_text) == spec


@pytest.mark.integration
def test_no_docs_go_written_and_nothing_but_the_spec_changes(
    swag_binary: Path, annotated: Path
) -> None:
    before = every_file(annotated)

    run_swag(annotated, annotated_profile(), swag=swag_binary)

    after = every_file(annotated)
    assert not (annotated / "docs" / "docs.go").exists()
    assert after.keys() - before.keys() == SPEC_FILES
    assert {name: after[name] for name in before} == before


@pytest.mark.integration
def test_go_build_still_passes_afterwards(swag_binary: Path, annotated: Path) -> None:
    run_swag(annotated, annotated_profile(), swag=swag_binary)

    built = subprocess.run(
        ["go", "build", "./..."],
        cwd=annotated,
        env=go_environment(annotated),
        capture_output=True,
        text=True,
        timeout=GO_BUILD_TIMEOUT_S,
    )

    assert built.returncode == 0, built.stderr


# Named, because the validator otherwise picks the version from the spec, and passes Swagger 2.0.
@pytest.mark.integration
def test_spec_passes_openapi_validator(swag_binary: Path, annotated: Path) -> None:
    validate(generated_spec(swag_binary, annotated), cls=OpenAPIV31SpecValidator)


@pytest.mark.integration
def test_polymorphic_field_is_oneof(swag_binary: Path, annotated: Path) -> None:
    sighting = schema(generated_spec(swag_binary, annotated), "birds.Sighting")

    assert sighting["properties"]["details"] == {
        "oneOf": [
            {"$ref": "#/components/schemas/birds.Nest"},
            {"$ref": "#/components/schemas/birds.Flock"},
        ]
    }


# swag v2 reads every field as required under --requiredByDefault, omitempty or not, so the field
# carries binding:"optional" as the renderer writes it.
@pytest.mark.integration
def test_omitempty_field_not_required(swag_binary: Path, annotated: Path) -> None:
    sighting = schema(generated_spec(swag_binary, annotated), "birds.Sighting")

    assert "note" in sighting["properties"]
    assert sorted(sighting["required"]) == ["details", "id", "species"]


@pytest.mark.integration
def test_the_adapter_generates_the_spec_of_the_repo(adapter: GoAdapter, annotated: Path) -> None:
    path = adapter.generate_spec(annotated, annotated_profile())

    assert path == annotated / "docs" / "swagger.json"
    assert json.loads(path.read_text(encoding="utf-8"))["openapi"] == "3.1.0"


@pytest.mark.integration
def test_swag_failure_raises_with_output(swag_binary: Path, annotated: Path) -> None:
    edit(annotated / "birds" / "handlers.go", "{object} birds.Problem", new="{object} birds.Gone")

    with pytest.raises(GoCommandError) as failed:
        run_swag(annotated, annotated_profile(), swag=swag_binary)

    assert "cannot find type definition: birds.Gone" in str(failed.value)
    assert failed.value.next_action


# run_swag with swag played by a stand-in


class RecordingSwag:
    def __init__(self, outcome: ChildOutcome) -> None:
        self.outcome = outcome
        self.commands: list[ChildCommand] = []

    def __call__(self, command: ChildCommand) -> ChildOutcome:
        self.commands.append(command)
        return self.outcome


def stand_in(monkeypatch: pytest.MonkeyPatch, outcome: ChildOutcome) -> RecordingSwag:
    recording = RecordingSwag(outcome)
    monkeypatch.setattr(swag, "run_child", recording)
    monkeypatch.setattr(swag, "go_environment", known_go_environment)
    return recording


def exited(code: int, *, stdout: str = "", stderr: str = "") -> ChildOutcome:
    return ChildOutcome(exit_code=code, stdout=stdout, stderr=stderr)


def test_swag_runs_the_pinned_command_from_the_repo_root_with_the_go_environment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    recording = stand_in(monkeypatch, exited(0))
    binary = tmp_path / "bin" / "swag"

    run_swag(tmp_path, annotated_profile(), swag=binary)

    [command] = recording.commands
    assert command.argv == (
        str(binary),
        "init",
        "--v3.1",
        "-g",
        MAIN_FILE,
        "--requiredByDefault",
        "--ot",
        "json,yaml",
        "-o",
        "docs",
    )
    assert command.cwd == tmp_path
    assert command.env == known_go_environment(tmp_path)
    assert (command.timeout_s, command.output) == (SWAG_TIMEOUT_S, CHECK_OUTPUT_LIMIT)


# A child runs in the repo, where a relative path to the program could find one the repo planted.
def test_a_relative_swag_path_is_fixed_where_the_caller_stands(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    recording = stand_in(monkeypatch, exited(0))
    monkeypatch.chdir(tmp_path)
    repo = tmp_path / "repo"

    run_swag(repo, annotated_profile(), swag=Path("bin") / "swag")

    [command] = recording.commands
    assert command.argv[0] == str((tmp_path / "bin" / "swag").resolve())


def test_noise_from_a_swag_that_exits_zero_is_no_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    noise = "warning: failed to get package name in dir: ./\nfound schema with no Type\n"
    stand_in(monkeypatch, exited(0, stdout=noise, stderr=noise))

    assert run_swag(tmp_path, annotated_profile(), swag=tmp_path / "swag") == (
        tmp_path / "docs" / "swagger.json"
    )


def test_a_failure_gives_a_short_clean_message(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    stand_in(monkeypatch, exited(1, stderr=NOISY_STDERR))

    with pytest.raises(GoCommandError) as failed:
        run_swag(tmp_path, annotated_profile(), swag=tmp_path / "swag")

    assert "the real reason" in str(failed.value)
    assert "\x1b" not in str(failed.value)
    assert len(str(failed.value)) < 2 * MESSAGE_TAIL_BYTES


def test_a_failure_that_printed_nothing_says_so(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    stand_in(monkeypatch, exited(-9))

    with pytest.raises(GoCommandError, match="printed nothing"):
        run_swag(tmp_path, annotated_profile(), swag=tmp_path / "swag")


# build_swag with go played by a stand-in


class CountingBuild:
    def __init__(self) -> None:
        self.commands: list[ChildCommand] = []

    def __call__(self, command: ChildCommand) -> ChildOutcome:
        self.commands.append(command)
        Path(command.argv[command.argv.index("-o") + 1]).write_text("binary", encoding="utf-8")
        return exited(0)


@pytest.fixture
def counting_build(monkeypatch: pytest.MonkeyPatch) -> CountingBuild:
    build = CountingBuild()
    monkeypatch.setattr(swag, "run_child", build)
    monkeypatch.setattr(swag, "go_environment", known_go_environment)
    monkeypatch.setattr(swag, "_built", set())
    return build


def test_swag_is_built_from_its_pinned_module_with_the_go_environment(
    counting_build: CountingBuild, tmp_path: Path
) -> None:
    binary = build_swag(tmp_path / "bin")

    [command] = counting_build.commands
    assert binary == tmp_path / "bin" / "swag"
    assert command.argv == ("go", "build", "-buildvcs=false", "-o", str(binary), SWAG_PACKAGE)
    assert command.cwd == SWAG_SOURCE
    assert command.env == known_go_environment(SWAG_SOURCE)
    assert command.timeout_s == GO_BUILD_TIMEOUT_S


def test_swag_is_built_once_per_process_for_a_folder(
    counting_build: CountingBuild, tmp_path: Path
) -> None:
    first = build_swag(tmp_path / "bin")
    second = build_swag(tmp_path / "bin")

    assert first == second
    assert len(counting_build.commands) == 1


def test_a_swag_deleted_after_the_build_is_built_again(
    counting_build: CountingBuild, tmp_path: Path
) -> None:
    build_swag(tmp_path / "bin").unlink()
    build_swag(tmp_path / "bin")

    assert len(counting_build.commands) == 2


def test_a_failed_build_of_swag_names_the_next_action(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    stand_in(monkeypatch, exited(1, stderr="go: github.com/swaggo/swag/v2: no network\n"))
    monkeypatch.setattr(swag, "_built", set())

    with pytest.raises(GoCommandError, match="no network") as failed:
        build_swag(tmp_path / "bin")

    assert failed.value.next_action


def test_a_folder_for_swag_that_cannot_be_made_stops_with_a_next_action(
    counting_build: CountingBuild, tmp_path: Path
) -> None:
    (tmp_path / "a-file").write_text("not a folder", encoding="utf-8")

    with pytest.raises(GoCommandError) as failed:
        build_swag(tmp_path / "a-file" / "bin")

    assert "a-file" in str(failed.value)
    assert failed.value.next_action
    assert counting_build.commands == []


# The plan pins rc6, the one version measured to write a correct oneOf.
def test_the_swag_module_pins_rc6_with_its_hash() -> None:
    go_mod = (SWAG_SOURCE / "go.mod").read_text(encoding="utf-8")

    assert f"tool {SWAG_PACKAGE}\n" in go_mod
    assert "github.com/swaggo/swag/v2 v2.0.0-rc6" in go_mod
    assert "github.com/swaggo/swag/v2 v2.0.0-rc6 h1:" in (SWAG_SOURCE / "go.sum").read_text(
        encoding="utf-8"
    )

"""The model setting per role: the shipped defaults, and the override file a user may supply."""

from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from arde.application.models_config import ModelsFileError, RoleConfig, load_models
from arde.domain.errors import ArdeError
from arde.domain.report import ExitCode

ROLES = ("discovery", "explore", "docs", "classifier", "judge")
VALID_ROLE = {"model": "claude-opus-5-5", "effort": "high", "max_turns": 5, "max_tokens": 2000}


@pytest.fixture(autouse=True)
def no_models_file_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ARDE_MODELS_FILE", raising=False)


def write_models_file(path: Path, **changes: Any) -> Path:
    roles: dict[str, Any] = {role: dict(VALID_ROLE) for role in ROLES}
    roles.update(changes)
    path.write_text(yaml.safe_dump(roles), encoding="utf-8")
    return path


def test_default_config_uses_haiku_only_for_classifier() -> None:
    m = load_models()
    agent_roles = ("discovery", "explore", "docs", "judge")

    assert m["classifier"].model == "claude-haiku-5-5"
    assert {m[r].model for r in agent_roles} == {"claude-opus-5-5"}
    assert tuple(m[r].max_turns for r in agent_roles) == (15, 30, 8, 30)


def test_default_config_covers_every_role() -> None:
    assert set(load_models()) == set(ROLES)


def test_default_effort_and_token_limits() -> None:
    models = load_models()

    for role in ("discovery", "explore", "docs", "judge"):
        assert (models[role].effort, models[role].max_tokens) == ("high", 16000)
    classifier = models["classifier"]
    assert (classifier.effort, classifier.max_tokens, classifier.max_turns) == ("low", 1024, 1)


def test_a_role_config_cannot_be_changed() -> None:
    config = load_models()["docs"]

    with pytest.raises(ValidationError):
        config.max_turns = 99


def test_a_path_loads_that_file(tmp_path: Path) -> None:
    changed = dict(VALID_ROLE, model="claude-sonnet-5-5")
    path = write_models_file(tmp_path / "models.yaml", judge=changed)

    models = load_models(path)

    assert models["judge"] == RoleConfig(
        model="claude-sonnet-5-5", effort="high", max_turns=5, max_tokens=2000
    )
    assert models["docs"].max_turns == 5


def test_the_environment_variable_replaces_the_default_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = write_models_file(tmp_path / "mine.yaml")
    monkeypatch.setenv("ARDE_MODELS_FILE", str(path))

    assert load_models()["classifier"].max_turns == 5


def test_an_explicit_path_beats_the_environment_variable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from_environment = write_models_file(tmp_path / "env.yaml")
    explicit = write_models_file(tmp_path / "explicit.yaml", docs=dict(VALID_ROLE, max_turns=7))
    monkeypatch.setenv("ARDE_MODELS_FILE", str(from_environment))

    assert load_models(explicit)["docs"].max_turns == 7


def test_an_empty_environment_variable_means_the_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ARDE_MODELS_FILE", "")

    assert load_models()["classifier"].model == "claude-haiku-5-5"


def test_a_missing_file_is_a_user_error_that_names_the_file(tmp_path: Path) -> None:
    missing = tmp_path / "nowhere.yaml"

    with pytest.raises(ModelsFileError) as caught:
        load_models(missing)

    assert str(missing) in str(caught.value)
    assert str(missing) in caught.value.next_action
    assert caught.value.exit_code is ExitCode.BAD_USAGE


def test_models_file_errors_are_arde_errors(tmp_path: Path) -> None:
    with pytest.raises(ArdeError):
        load_models(tmp_path / "nowhere.yaml")


def test_a_file_that_is_not_yaml_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "models.yaml"
    path.write_text("docs: [unclosed", encoding="utf-8")

    with pytest.raises(ModelsFileError, match="not valid YAML"):
        load_models(path)


def test_a_file_without_roles_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "models.yaml"
    path.write_text("", encoding="utf-8")

    with pytest.raises(ModelsFileError):
        load_models(path)


def test_a_missing_role_is_named(tmp_path: Path) -> None:
    path = write_models_file(tmp_path / "models.yaml")
    roles = yaml.safe_load(path.read_text(encoding="utf-8"))
    del roles["judge"]
    path.write_text(yaml.safe_dump(roles), encoding="utf-8")

    with pytest.raises(ModelsFileError, match="judge"):
        load_models(path)


def test_a_misspelled_setting_is_named(tmp_path: Path) -> None:
    path = write_models_file(tmp_path / "models.yaml", docs=dict(VALID_ROLE, max_turn=3))

    with pytest.raises(ModelsFileError, match=r"docs\.max_turn"):
        load_models(path)


def test_an_unknown_role_is_refused(tmp_path: Path) -> None:
    path = write_models_file(tmp_path / "models.yaml", reviewer=dict(VALID_ROLE))

    with pytest.raises(ModelsFileError, match="reviewer"):
        load_models(path)


@pytest.mark.parametrize("setting", ["max_turns", "max_tokens"])
@pytest.mark.parametrize("value", [0, -1])
def test_a_limit_below_one_is_refused(tmp_path: Path, setting: str, value: int) -> None:
    path = write_models_file(tmp_path / "models.yaml", docs=dict(VALID_ROLE, **{setting: value}))

    with pytest.raises(ModelsFileError, match=rf"docs\.{setting}"):
        load_models(path)

"""The model setting per role: the shipped defaults, and the override file a user may supply."""

from pathlib import Path
from typing import Any, get_args

import pytest
import yaml
from pydantic import ValidationError

from arde.adapters.models_file import ModelsFileError, load_models
from arde.application.models_config import Role, RoleConfig
from arde.domain.errors import ArdeError
from arde.domain.report import ExitCode

ROLES = get_args(Role)
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


def test_a_variable_that_is_set_but_empty_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ARDE_MODELS_FILE", "")

    with pytest.raises(ModelsFileError, match="empty") as caught:
        load_models()

    assert "ARDE_MODELS_FILE" in caught.value.next_action


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


@pytest.mark.parametrize("effort", ["low", "medium", "high", "xhigh", "max"])
def test_every_documented_effort_is_accepted(tmp_path: Path, effort: str) -> None:
    path = write_models_file(tmp_path / "models.yaml", docs=dict(VALID_ROLE, effort=effort))

    assert load_models(path)["docs"].effort == effort


@pytest.mark.parametrize("effort", ["hihg", "banana", "", "HIGH"])
def test_a_misspelt_effort_is_refused_before_any_model_is_called(
    tmp_path: Path, effort: str
) -> None:
    path = write_models_file(tmp_path / "models.yaml", docs=dict(VALID_ROLE, effort=effort))

    with pytest.raises(ModelsFileError, match=r"docs\.effort"):
        load_models(path)


@pytest.mark.parametrize("model", ["gpt-5", "", "claude-opus-9"])
def test_a_model_with_no_price_is_refused_before_any_money_is_spent(
    tmp_path: Path, model: str
) -> None:
    path = write_models_file(tmp_path / "models.yaml", docs=dict(VALID_ROLE, model=model))

    with pytest.raises(ModelsFileError, match=r"docs\.model") as caught:
        load_models(path)

    assert "claude-opus-5-5" in str(caught.value)


def test_a_role_config_cannot_name_an_unpriced_model() -> None:
    with pytest.raises(ValidationError, match="price"):
        RoleConfig(model="gpt-5", effort="low", max_turns=1, max_tokens=1)


@pytest.mark.parametrize("setting", ["max_turns", "max_tokens"])
@pytest.mark.parametrize("value", [True, "5", 5.0], ids=["yes", "text", "float"])
def test_a_limit_must_be_a_real_integer(tmp_path: Path, setting: str, value: object) -> None:
    path = write_models_file(tmp_path / "models.yaml", docs=dict(VALID_ROLE, **{setting: value}))

    with pytest.raises(ModelsFileError, match=rf"docs\.{setting}"):
        load_models(path)


def write_text(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


def valid_file_text() -> str:
    roles = {role: dict(VALID_ROLE) for role in ROLES}
    return yaml.safe_dump(roles)


def test_a_repeated_setting_is_refused(tmp_path: Path) -> None:
    text = valid_file_text() + "extra:\n  max_turns: 15\n  max_turns: 99999\n"
    path = write_text(tmp_path / "models.yaml", text)

    with pytest.raises(ModelsFileError, match="max_turns"):
        load_models(path)


def test_a_repeated_role_is_refused(tmp_path: Path) -> None:
    path = write_text(tmp_path / "models.yaml", valid_file_text() + "docs:\n  model: x\n")

    with pytest.raises(ModelsFileError, match="docs"):
        load_models(path)


def test_a_file_that_is_not_utf8_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "models.yaml"
    path.write_bytes("docs: caf\u00e9\n".encode("latin-1"))

    with pytest.raises(ModelsFileError, match="cannot be read"):
        load_models(path)


@pytest.mark.parametrize(
    "text",
    [
        "model: 2024-13-45\n",
        "effort: 2001-12-14 99:59:43\n",
        "max_turns: " + "9" * 5_000 + "\n",
        "[" * 100_000,
        "a: " + "{b: " * 5_000,
    ],
    ids=["bad_date", "bad_time", "huge_number", "deep_list", "deep_map"],
)
def test_yaml_that_crashes_the_parser_is_a_models_file_error(tmp_path: Path, text: str) -> None:
    path = write_text(tmp_path / "models.yaml", text)

    with pytest.raises(ModelsFileError) as caught:
        load_models(path)

    assert caught.value.exit_code is ExitCode.BAD_USAGE

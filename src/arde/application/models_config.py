"""Which model each role uses, read from the shipped models.yaml or a file the user points to."""

import os
from importlib.resources import files
from importlib.resources.abc import Traversable
from pathlib import Path

import yaml
from pydantic import PositiveInt, ValidationError

from arde.domain.base import Frozen
from arde.domain.errors import ArdeError
from arde.domain.report import ExitCode

MODELS_FILE_VARIABLE = "ARDE_MODELS_FILE"


class RoleConfig(Frozen):
    model: str
    effort: str
    max_turns: PositiveInt
    max_tokens: PositiveInt


# Spelling out the roles lets the file be checked in one pass: a missing role, a misspelt role
# and a misspelt setting are all refused before any model is called.
class _ModelsFile(Frozen):
    discovery: RoleConfig
    explore: RoleConfig
    docs: RoleConfig
    classifier: RoleConfig
    judge: RoleConfig


class ModelsFileError(ArdeError):
    def __init__(self, source: str, problem: str) -> None:
        super().__init__(
            f"Cannot use the models file {source}: {problem}",
            next_action=(
                f"Fix {source}, or leave {MODELS_FILE_VARIABLE} unset to use the built-in models"
            ),
            exit_code=ExitCode.BAD_USAGE,
        )


def load_models(path: Path | None = None) -> dict[str, RoleConfig]:
    source = _choose_source(path)
    raw = _read_yaml(source)
    try:
        models_file = _ModelsFile.model_validate(raw)
    except ValidationError as error:
        raise ModelsFileError(str(source), _describe(error)) from error
    return {role: getattr(models_file, role) for role in _ModelsFile.model_fields}


# An explicit path beats the environment, which beats the file that ships with the package.
def _choose_source(path: Path | None) -> Traversable:
    if path is not None:
        return path
    from_environment = os.environ.get(MODELS_FILE_VARIABLE)
    if from_environment:
        return Path(from_environment)
    return files("arde") / "config" / "models.yaml"


def _read_yaml(source: Traversable) -> object:
    try:
        return yaml.safe_load(source.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError) as error:
        raise ModelsFileError(str(source), f"it cannot be read ({error})") from error
    except yaml.YAMLError as error:
        raise ModelsFileError(str(source), f"it is not valid YAML ({error})") from error


def _describe(error: ValidationError) -> str:
    return "; ".join(
        f"{'.'.join(str(part) for part in problem['loc']) or 'the file'}: {problem['msg']}"
        for problem in error.errors()
    )

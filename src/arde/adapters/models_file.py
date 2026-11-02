"""Which model each role uses, read from the shipped models.yaml or a file the user points to."""

import os
from importlib.resources import files
from importlib.resources.abc import Traversable
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from arde.application.models_config import Role, RoleConfig
from arde.domain.base import Frozen, describe_problems
from arde.domain.errors import ArdeError
from arde.domain.report import ExitCode

MODELS_FILE_VARIABLE = "ARDE_MODELS_FILE"


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


# PyYAML keeps the last of two equal keys, so a repeated setting would silently win.
class _UniqueKeyLoader(yaml.SafeLoader):
    def construct_mapping(self, node: yaml.MappingNode, deep: bool = False) -> dict[Any, Any]:
        keys = [key_node.value for key_node, _ in node.value if isinstance(key_node.value, str)]
        repeated = sorted({key for key in keys if keys.count(key) > 1})
        if repeated:
            raise yaml.constructor.ConstructorError(
                problem=f"repeated key: {', '.join(repeated)}", problem_mark=node.start_mark
            )
        return super().construct_mapping(node, deep)


def load_models(path: Path | None = None) -> dict[Role, RoleConfig]:
    source = _choose_source(path)
    raw = _read_yaml(source)
    try:
        models_file = _ModelsFile.model_validate(raw)
    except ValidationError as error:
        raise ModelsFileError(str(source), describe_problems(error, whole="the file")) from error
    return {
        "discovery": models_file.discovery,
        "explore": models_file.explore,
        "docs": models_file.docs,
        "classifier": models_file.classifier,
        "judge": models_file.judge,
    }


# An explicit path beats the environment, which beats the file that ships with the package. Set
# but empty is a mistake in a script, so it is refused rather than read as unset.
def _choose_source(path: Path | None) -> Traversable:
    if path is not None:
        return path
    from_environment = os.environ.get(MODELS_FILE_VARIABLE)
    if from_environment == "":
        raise ModelsFileError(MODELS_FILE_VARIABLE, "it is set but empty")
    if from_environment is not None:
        return Path(from_environment)
    return files("arde") / "config" / "models.yaml"


# PyYAML raises more than YAMLError: a bad date is a ValueError, deep nesting a RecursionError.
def _read_yaml(source: Traversable) -> object:
    try:
        return yaml.load(source.read_text(encoding="utf-8"), Loader=_UniqueKeyLoader)
    except (OSError, UnicodeDecodeError) as error:
        raise ModelsFileError(str(source), f"it cannot be read ({error})") from error
    except (yaml.YAMLError, ValueError, RecursionError) as error:
        raise ModelsFileError(str(source), f"it is not valid YAML ({error})") from error

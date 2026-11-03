"""sections.yaml: the facts each written doc section rests on, so a later run knows which to redo.

A file of another schema version reads as no file at all, which makes the next run write every
section again, as a first run does.
"""

import os
from collections.abc import Mapping, Sequence
from typing import Literal

import yaml
from pydantic import ValidationError

from arde.application.ports import RunStore
from arde.domain.base import Frozen, describe_problems, printable_name
from arde.domain.errors import ArdeError
from arde.domain.facts import Topic
from arde.domain.report import ExitCode

_SCHEMA_VERSION = 1


class _SectionsFile(Frozen):
    schema_version: Literal[1]
    sections: dict[Topic, tuple[str, ...]]


class SectionsFileError(ArdeError):
    def __init__(self, message: str, *, path: os.PathLike[str]) -> None:
        super().__init__(
            message,
            next_action=(
                f"Delete {printable_name(os.fspath(path))}, and the next run writes every doc "
                "section again."
            ),
            exit_code=ExitCode.BAD_USAGE,
        )


def load_sections(store: RunStore) -> dict[Topic, tuple[str, ...]]:
    """The fact ids each section was written from, by topic. Empty when no section was written."""
    path = store.sections_path
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    except (OSError, UnicodeDecodeError) as error:
        raise SectionsFileError(f"Cannot read {path.name}: {error}", path=path) from error
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as error:
        raise SectionsFileError(f"{path.name} is not valid YAML: {error}", path=path) from error
    if not isinstance(data, dict):
        raise SectionsFileError(f"{path.name} holds no mapping of sections", path=path)
    if data.get("schema_version") != _SCHEMA_VERSION:
        return {}
    try:
        return _SectionsFile.model_validate(data).sections
    except ValidationError as error:
        problems = describe_problems(error, whole="the file")
        message = f"{path.name} is not a sections file: {problems}"
        raise SectionsFileError(message, path=path) from error


def save_sections(store: RunStore, sections: Mapping[Topic, Sequence[str]]) -> None:
    """Writes the fact ids of each section, sorted, so the same sections give the same bytes."""
    path = store.sections_path
    document = {
        "schema_version": _SCHEMA_VERSION,
        "sections": {topic: sorted(ids) for topic, ids in sorted(sections.items())},
    }
    text = yaml.safe_dump(document, sort_keys=False)
    # A rename replaces the file whole, so a run stopped mid-write leaves the last one readable.
    staged = path.with_name(f".{path.name}.new")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        staged.write_text(text, encoding="utf-8")
        os.replace(staged, path)
    except OSError as error:
        raise SectionsFileError(f"Cannot write {path.name}: {error}", path=path) from error

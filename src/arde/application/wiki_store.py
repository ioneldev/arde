"""The wiki between runs: one YAML file per topic, and one for the probes its facts cite.

Each file carries the schema version, and facts and probes are written sorted, so one wiki always
writes the same bytes and a diff of two runs shows only what changed. Only ASCII is written:
PyYAML reads a few characters back differently from how it wrote them, such as U+0085, and an
escape reads back exactly.
"""

import os
from pathlib import Path
from typing import Any, Literal, get_args

import yaml
from pydantic import ValidationError

from arde.application.ports import RunStore
from arde.domain.base import Frozen, describe_problems
from arde.domain.errors import ArdeError
from arde.domain.facts import Fact, ProbeRecord, Topic, Wiki
from arde.domain.report import ExitCode

SCHEMA_VERSION = 1
TOPICS: tuple[Topic, ...] = get_args(Topic)
_PROBES = "probes"
_SUFFIX = ".yaml"


class WikiStoreError(ArdeError):
    def __init__(self, message: str, *, wiki_dir: Path) -> None:
        super().__init__(
            message,
            next_action=(
                f"Delete {wiki_dir} so that the next run builds the wiki again from the start, "
                "then run again"
            ),
            exit_code=ExitCode.BAD_USAGE,
        )


class _TopicFile(Frozen):
    schema_version: Literal[1]
    facts: tuple[Fact, ...]


class _ProbesFile(Frozen):
    schema_version: Literal[1]
    probes: tuple[ProbeRecord, ...]


def load_wiki(store: RunStore) -> Wiki:
    """The wiki the last run saved.

    No wiki, or a file of another schema version, reads as an empty wiki, which makes this run a
    first run. A file that cannot be read stops the run, because starting again from nothing
    would throw away paid work without saying so.
    """
    folder = store.wiki_dir
    documents = {
        name: _read(path, folder)
        for name in (*TOPICS, _PROBES)
        if (path := _file_of(folder, name)).exists()
    }
    if any(document.get("schema_version") != SCHEMA_VERSION for document in documents.values()):
        return Wiki(facts=())
    facts = [
        fact
        for topic in TOPICS
        if topic in documents
        for fact in _facts_in(documents[topic], topic=topic, folder=folder)
    ]
    probes = _probes_in(documents[_PROBES], folder=folder) if _PROBES in documents else ()
    try:
        return Wiki(facts=tuple(facts), probes=probes)
    except ValidationError as error:
        problems = describe_problems(error, whole="the wiki")
        raise WikiStoreError(
            f"The wiki in {folder} does not hold together: {problems}", wiki_dir=folder
        ) from error


def save_wiki(store: RunStore, wiki: Wiki) -> None:
    """Writes the wiki, one file per topic that has facts, and removes the file of each that has
    none, so a deleted fact cannot come back on the next load."""
    folder = store.wiki_dir
    documents: dict[str, dict[str, Any] | None] = {
        topic: _topic_document(wiki, topic) for topic in TOPICS
    }
    documents[_PROBES] = _probes_document(wiki)
    try:
        folder.mkdir(parents=True, exist_ok=True)
        _write_all(folder, documents)
    except OSError as error:
        raise WikiStoreError(
            f"Cannot write the wiki in {folder}: {error}", wiki_dir=folder
        ) from error


def _file_of(folder: Path, name: str) -> Path:
    return folder / f"{name}{_SUFFIX}"


def _read(path: Path, folder: Path) -> dict[str, Any]:
    try:
        document = yaml.safe_load(path.read_bytes())
    except (OSError, yaml.YAMLError) as error:
        raise WikiStoreError(
            f"Cannot read {path.name} of the wiki: {error}", wiki_dir=folder
        ) from error
    if not isinstance(document, dict):
        raise WikiStoreError(f"{path.name} of the wiki holds no mapping", wiki_dir=folder)
    return document


def _facts_in(document: dict[str, Any], *, topic: Topic, folder: Path) -> tuple[Fact, ...]:
    name = _file_of(folder, topic).name
    try:
        facts = _TopicFile.model_validate(document).facts
    except ValidationError as error:
        problems = describe_problems(error, whole=name)
        raise WikiStoreError(
            f"{name} of the wiki is not valid: {problems}", wiki_dir=folder
        ) from error
    for fact in facts:
        if fact.topic != topic:
            raise WikiStoreError(
                f"{name} holds {fact.id}, whose topic is {fact.topic}: each fact has one home",
                wiki_dir=folder,
            )
    return facts


def _probes_in(document: dict[str, Any], *, folder: Path) -> tuple[ProbeRecord, ...]:
    name = _file_of(folder, _PROBES).name
    try:
        return _ProbesFile.model_validate(document).probes
    except ValidationError as error:
        problems = describe_problems(error, whole=name)
        raise WikiStoreError(
            f"{name} of the wiki is not valid: {problems}", wiki_dir=folder
        ) from error


def _topic_document(wiki: Wiki, topic: Topic) -> dict[str, Any] | None:
    facts = sorted(wiki.by_topic(topic), key=lambda fact: fact.id)
    if not facts:
        return None
    return {
        "schema_version": SCHEMA_VERSION,
        "facts": [fact.model_dump(mode="json") for fact in facts],
    }


def _probes_document(wiki: Wiki) -> dict[str, Any] | None:
    probes = sorted(wiki.probes, key=lambda probe: (probe.method, probe.path_and_query))
    if not probes:
        return None
    return {
        "schema_version": SCHEMA_VERSION,
        "probes": [probe.model_dump(mode="json") for probe in probes],
    }


# Every file is written beside its final name first and then moved over it, so a run stopped
# part way leaves each file whole, old or new.
def _write_all(folder: Path, documents: dict[str, dict[str, Any] | None]) -> None:
    staged: dict[Path, Path] = {}
    try:
        for name, document in documents.items():
            if document is not None:
                final = _file_of(folder, name)
                staged[final] = _staged(final, document)
        for final, temporary in staged.items():
            os.replace(temporary, final)
    finally:
        for temporary in staged.values():
            temporary.unlink(missing_ok=True)
    for name, document in documents.items():
        if document is None:
            _file_of(folder, name).unlink(missing_ok=True)


def _staged(final: Path, document: dict[str, Any]) -> Path:
    temporary = final.with_name(f".{final.name}.new")
    text = yaml.safe_dump(document, sort_keys=False, allow_unicode=False)
    temporary.write_bytes(text.encode("ascii"))
    return temporary

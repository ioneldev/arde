"""render_manifest.yaml: the comment lines the last successful run wrote, for the next render to
remove before it writes.

A file of another schema version reads as no file, so the next render removes nothing it cannot
tell from a person's comment, and writes its own lines again.
"""

import os
from typing import Literal

import yaml
from pydantic import ValidationError

from arde.application.ports import RenderManifest, RunStore
from arde.domain.base import Frozen, describe_problems, printable_name
from arde.domain.errors import ArdeError
from arde.domain.report import ExitCode

_SCHEMA_VERSION = 1


class _ManifestFile(Frozen):
    schema_version: Literal[1]
    lines: dict[str, tuple[str, ...]]


class ManifestFileError(ArdeError):
    def __init__(self, message: str, *, path: os.PathLike[str]) -> None:
        super().__init__(
            message,
            next_action=(
                f"Delete {printable_name(os.fspath(path))} and run again. ARDE then leaves the "
                "comments it wrote before in place, and a later run may write them twice."
            ),
            exit_code=ExitCode.BAD_USAGE,
        )


def load_manifest(store: RunStore) -> RenderManifest:
    """What the last successful run wrote, or an empty manifest when no run has succeeded."""
    path = store.manifest_path
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return RenderManifest()
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as error:
        raise ManifestFileError(f"Cannot read {path.name}: {error}", path=path) from error
    if not isinstance(data, dict) or data.get("schema_version") != _SCHEMA_VERSION:
        return RenderManifest()
    try:
        return RenderManifest(lines=_ManifestFile.model_validate(data).lines)
    except ValidationError as error:
        problems = describe_problems(error, whole="the file")
        raise ManifestFileError(f"{path.name} is not a manifest: {problems}", path=path) from error


# ASCII only, as the wiki is written: PyYAML reads a few characters back differently from how it
# wrote them, and a line read back differently would never match the line in the file.
def save_manifest(store: RunStore, manifest: RenderManifest) -> None:
    path = store.manifest_path
    document = {
        "schema_version": _SCHEMA_VERSION,
        "lines": {file: list(lines) for file, lines in sorted(manifest.lines.items())},
    }
    staged = path.with_name(f".{path.name}.new")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        text = yaml.safe_dump(document, sort_keys=False, allow_unicode=False)
        staged.write_bytes(text.encode("ascii"))
        os.replace(staged, path)
    except OSError as error:
        raise ManifestFileError(f"Cannot write {path.name}: {error}", path=path) from error

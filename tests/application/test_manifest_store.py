from pathlib import Path

import pytest

from arde.adapters.workspace import Workspace
from arde.application.manifest_store import ManifestFileError, load_manifest, save_manifest
from arde.application.ports import RenderManifest


@pytest.fixture
def store(tmp_path: Path) -> Workspace:
    return Workspace(tmp_path, "repo-id")


def test_no_file_reads_as_an_empty_manifest(store: Workspace) -> None:
    assert load_manifest(store) == RenderManifest()


def test_a_saved_manifest_reads_back_the_same(store: Workspace) -> None:
    # A letter past ASCII, and a line separator, which a YAML reader could read back otherwise.
    summary = "// @Summary Caf\u00e9 " + chr(0x2028)
    manifest = RenderManifest(lines={"web/a.go": (summary, "// x"), "b.go": ("// y",)})

    save_manifest(store, manifest)

    assert load_manifest(store) == manifest
    assert store.manifest_path.read_bytes().isascii()


def test_a_file_of_another_schema_version_reads_as_none(store: Workspace) -> None:
    store.manifest_path.parent.mkdir(parents=True)
    store.manifest_path.write_text("schema_version: 2\nlines: {}\n")

    assert load_manifest(store) == RenderManifest()


def test_a_broken_file_stops_the_run_and_says_to_delete_it(store: Workspace) -> None:
    store.manifest_path.parent.mkdir(parents=True)
    store.manifest_path.write_text("schema_version: 1\nlines: [1, 2]\n")

    with pytest.raises(ManifestFileError) as raised:
        load_manifest(store)

    assert "Delete" in raised.value.next_action

"""sections.yaml: which facts each written doc section rests on, kept between runs."""

from pathlib import Path

import pytest

from arde.adapters.workspace import Workspace
from arde.application.section_store import SectionsFileError, load_sections, save_sections


def a_store(tmp_path: Path) -> Workspace:
    return Workspace(tmp_path, "repo")


def write_sections_file(store: Workspace, text: str) -> None:
    store.sections_path.parent.mkdir(parents=True, exist_ok=True)
    store.sections_path.write_text(text, encoding="utf-8")


def test_sections_round_trip_and_the_file_is_byte_stable(tmp_path: Path) -> None:
    store = a_store(tmp_path)

    save_sections(store, {"overview": ("rule:b", "rule:a"), "errors": ("unreachable:x",)})
    first = store.sections_path.read_bytes()
    save_sections(store, load_sections(store))

    assert load_sections(store) == {"errors": ("unreachable:x",), "overview": ("rule:a", "rule:b")}
    assert store.sections_path.read_bytes() == first


def test_no_sections_file_means_no_section_was_written(tmp_path: Path) -> None:
    assert load_sections(a_store(tmp_path)) == {}


def test_a_file_of_another_schema_version_counts_as_a_first_run(tmp_path: Path) -> None:
    store = a_store(tmp_path)
    write_sections_file(store, "schema_version: 2\nsections:\n  overview: [rule:a]\n")

    assert load_sections(store) == {}


@pytest.mark.parametrize(
    "text",
    [
        "sections: [",
        "",
        "- a list\n",
        "schema_version: 1\nsections:\n  nowhere: [rule:a]\n",
        "schema_version: 1\nsections:\n  overview: rule:a\n",
    ],
    ids=["not yaml", "empty", "a list", "an unknown topic", "ids not in a list"],
)
def test_a_broken_sections_file_stops_and_says_what_to_do(tmp_path: Path, text: str) -> None:
    store = a_store(tmp_path)
    write_sections_file(store, text)

    with pytest.raises(SectionsFileError) as stopped:
        load_sections(store)

    assert "sections.yaml" in str(stopped.value)
    assert "Delete" in stopped.value.next_action

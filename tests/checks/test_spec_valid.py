from pathlib import Path

import pytest
from checks_support import a_context, a_spec, an_operation

from arde.checks.spec_operations import SpecUnreadable
from arde.checks.spec_valid import SpecValid


def test_a_valid_openapi_31_spec_passes(tmp_path: Path) -> None:
    spec = a_spec(paths={"/things": {"get": an_operation()}})

    assert SpecValid().run(a_context(tmp_path, spec=spec)) == []


def test_an_invalid_spec_is_one_finding_owned_by_code(tmp_path: Path) -> None:
    spec = a_spec(paths={"/things": {"get": {"summary": "no responses"}}})
    spec["info"] = {"title": "Things"}

    [finding] = SpecValid().run(a_context(tmp_path, spec=spec))

    assert (finding.check, finding.owner) == ("spec_valid", "code")
    assert "version" in finding.message
    assert "spec.json" in finding.subject


def test_a_spec_of_another_version_is_a_finding(tmp_path: Path) -> None:
    spec = {**a_spec(), "openapi": "3.0.3"}

    [finding] = SpecValid().run(a_context(tmp_path, spec=spec))

    assert "3.1" in finding.message


def test_a_spec_that_is_not_json_stops_the_run(tmp_path: Path) -> None:
    context = a_context(tmp_path)
    context.spec_path.write_text("{", encoding="utf-8")

    with pytest.raises(SpecUnreadable):
        SpecValid().run(context)


def test_the_message_shows_no_control_character_from_the_spec(tmp_path: Path) -> None:
    spec = a_spec(paths={"things\x1b[2J": {"get": an_operation()}})

    [finding] = SpecValid().run(a_context(tmp_path, spec=spec))

    assert "things\\x1b[2J" in finding.message
    assert "\x1b" not in finding.message

"""Closed schemas: a reply may hold only the keys its schema lists.

Plain JSON Schema validation accepts any key a schema does not mention, so a reply with an
undocumented field would pass. Closing every object schema makes that field a failure.
"""

import json
from pathlib import Path
from typing import Any

import pytest
from annotated_support import annotated_spec
from jsonschema import Draft202012Validator

from arde.checks.closed_schema import ClosedSchemas, close_schema

SIGHTING = {"$ref": "#/components/schemas/birds.Sighting"}
# The two sightings the fixture serves, one per variant of its details field.
NEST_SIGHTING = {"id": 1, "species": "wren", "details": {"eggs": 4}}
FLOCK_SIGHTING = {
    "id": 2,
    "species": "starling",
    "note": "at dusk",
    "details": {"size": 300, "heading": "west"},
}


def open_problems(spec: Any, schema: dict[str, object], body: object) -> list[str]:
    root = {**schema, "components": spec["components"]}
    return [error.message for error in Draft202012Validator(root).iter_errors(body)]


@pytest.mark.integration
def test_closed_oneof_accepts_real_variant_and_rejects_extra_key(
    swag_binary: Path, annotated: Path
) -> None:
    spec = annotated_spec(annotated, swag=swag_binary)
    closed = ClosedSchemas(spec)
    extra_key = {**NEST_SIGHTING, "details": {"eggs": 4, "colour": "brown"}}

    assert closed.problems(SIGHTING, NEST_SIGHTING) == []
    assert closed.problems(SIGHTING, FLOCK_SIGHTING) == []
    assert closed.problems(SIGHTING, extra_key) != []
    # The negative control: without closing, the extra key passes, so the closing is what fails it.
    assert open_problems(spec, SIGHTING, extra_key) == []


@pytest.mark.integration
def test_the_problem_with_a_variant_names_the_key_of_the_closest_one(
    swag_binary: Path, annotated: Path
) -> None:
    spec = annotated_spec(annotated, swag=swag_binary)
    extra_key = {**NEST_SIGHTING, "details": {"eggs": 4, "colour": "brown"}}

    [problem] = ClosedSchemas(spec).problems(SIGHTING, extra_key)

    assert problem.startswith("$.details: fits none of the schemas it may take")
    assert "'colour' was unexpected" in problem


# close_schema on schemas written by hand

ITEM: dict[str, object] = {
    "type": "object",
    "properties": {"id": {"type": "integer"}, "name": {"type": "string"}},
    "required": ["id"],
}


def problems(schema: dict[str, object], body: object) -> list[str]:
    return ClosedSchemas({}).problems(schema, body)


def test_a_key_the_schema_does_not_list_is_a_problem() -> None:
    assert problems(ITEM, {"id": 1, "name": "a"}) == []
    assert problems(ITEM, {"id": 1, "extra": True}) == [
        "$: Unevaluated properties are not allowed ('extra' was unexpected)"
    ]


def test_a_missing_required_key_is_a_problem() -> None:
    assert problems(ITEM, {"name": "a"}) == ["$: 'id' is a required property"]


def test_objects_inside_arrays_and_properties_are_closed() -> None:
    schema = {"type": "object", "properties": {"items": {"type": "array", "items": ITEM}}}

    assert problems(schema, {"items": [{"id": 1}, {"id": 2, "extra": 0}]}) == [
        "$.items[1]: Unevaluated properties are not allowed ('extra' was unexpected)"
    ]


def test_the_components_a_reference_points_at_are_closed() -> None:
    spec = {"components": {"schemas": {"Item": ITEM}}}

    found = ClosedSchemas(spec).problems(
        {"$ref": "#/components/schemas/Item"}, {"id": 1, "extra": 0}
    )

    assert found == ["$: Unevaluated properties are not allowed ('extra' was unexpected)"]


def test_a_map_and_an_object_with_no_listed_keys_take_any_key() -> None:
    schema = {
        "type": "object",
        "properties": {
            "labels": {"type": "object", "additionalProperties": {"type": "string"}},
            "anything": {"type": "object"},
        },
    }

    assert problems(schema, {"labels": {"a": "b"}, "anything": {"c": 1}}) == []


# additionalProperties: false in a part of an allOf would refuse the keys the other parts list.
def test_an_allof_takes_the_keys_of_every_part_and_no_other() -> None:
    schema: dict[str, object] = {
        "allOf": [
            {"type": "object", "properties": {"id": {"type": "integer"}}},
            {"type": "object", "properties": {"name": {"type": "string"}}},
        ]
    }

    assert problems(schema, {"id": 1, "name": "a"}) == []
    assert problems(schema, {"id": 1, "name": "a", "extra": 0}) == [
        "$: Unevaluated properties are not allowed ('extra' was unexpected)"
    ]


def test_a_schema_that_says_what_it_does_with_other_keys_is_left_as_it_says() -> None:
    schema = {**ITEM, "additionalProperties": True}

    assert close_schema(schema) == schema


def test_values_are_not_read_as_schemas() -> None:
    value = {"properties": {"a": {}}}
    schema: dict[str, object] = {
        "type": "object",
        "properties": {"properties": {"type": "object", "default": value, "enum": [value]}},
    }

    closed = close_schema(schema)

    assert closed["properties"] == {
        "properties": {"type": "object", "default": value, "enum": [value]}
    }
    assert closed["unevaluatedProperties"] is False


def test_the_schema_given_is_not_changed() -> None:
    schema = {"type": "object", "properties": {"inner": dict(ITEM)}}
    before = json.dumps(schema, sort_keys=True)

    close_schema(schema)

    assert json.dumps(schema, sort_keys=True) == before

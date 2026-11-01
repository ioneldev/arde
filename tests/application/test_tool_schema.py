"""Strict tool use wants every object closed, and only schema positions may be touched for that."""

from typing import Any

from pydantic import BaseModel

from arde.application.tool_schema import strict_tool_schema
from arde.domain.profile import RepoProfile


def test_strict_tool_schema_marks_nested_objects_closed() -> None:
    assert strict_tool_schema(RepoProfile)["additionalProperties"] is False


def test_strict_tool_schema_closes_objects_that_were_open() -> None:
    class Inner(BaseModel):
        value: int

    class Outer(BaseModel):
        inner: Inner
        others: list[Inner] = []

    schema = strict_tool_schema(Outer)

    assert schema["additionalProperties"] is False
    assert schema["$defs"]["Inner"]["additionalProperties"] is False
    assert schema["$defs"]["Inner"]["required"] == ["value"]


def test_strict_tool_schema_requires_every_property_even_those_with_defaults() -> None:
    schema = strict_tool_schema(RepoProfile)

    assert set(schema["required"]) == set(RepoProfile.model_fields)


def test_strict_tool_schema_treats_a_field_named_properties_as_a_field() -> None:
    class Odd(BaseModel):
        properties: list[str]

    assert strict_tool_schema(Odd)["required"] == ["properties"]


def test_strict_tool_schema_leaves_the_model_schema_untouched() -> None:
    class Open(BaseModel):
        count: int = 0

    strict_tool_schema(Open)

    assert "additionalProperties" not in Open.model_json_schema()


def test_a_default_that_looks_like_a_keyword_is_data_and_does_not_crash() -> None:
    class Holder(BaseModel):
        labels: dict[str, str] = {"properties": "x"}

    schema = strict_tool_schema(Holder)

    assert schema["properties"]["labels"]["default"] == {"properties": "x"}


def test_a_default_that_looks_like_a_schema_is_left_as_written() -> None:
    class Holder(BaseModel):
        shape: dict[str, object] = {"properties": {"a": {}}}

    schema = strict_tool_schema(Holder)

    assert schema["properties"]["shape"]["default"] == {"properties": {"a": {}}}


class SchemaWritten(BaseModel):
    """Lets a test hand over a schema Pydantic would not write for these models."""

    written: dict[str, Any] = {}

    @classmethod
    def model_json_schema(cls, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return cls.model_fields["written"].get_default()


def schema_of(written: dict[str, Any]) -> dict[str, Any]:
    class Shaped(SchemaWritten):
        @classmethod
        def model_json_schema(cls, *args: Any, **kwargs: Any) -> dict[str, Any]:
            return written

    return strict_tool_schema(Shaped)


def test_data_keywords_are_left_alone_at_any_depth() -> None:
    data = {"properties": {"a": {}}}
    written = {
        "type": "object",
        "properties": {
            "fixed": {"const": data},
            "picked": {"enum": [data, "other"]},
            "shown": {"examples": [data]},
        },
    }

    fields = schema_of(written)["properties"]

    assert fields["fixed"]["const"] == data
    assert fields["picked"]["enum"] == [data, "other"]
    assert fields["shown"]["examples"] == [data]


def test_an_object_inside_a_list_of_schemas_is_closed_too() -> None:
    inline = {"type": "object", "properties": {"a": {"type": "string"}}}

    closed = schema_of({"anyOf": [inline, {"type": "null"}]})

    assert closed["anyOf"][0]["additionalProperties"] is False
    assert closed["anyOf"][0]["required"] == ["a"]
    assert closed["anyOf"][1] == {"type": "null"}

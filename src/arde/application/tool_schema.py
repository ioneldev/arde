"""Turns a model's JSON schema into one that strict tool use accepts."""

from typing import Any

from pydantic import BaseModel

# Their values are data, so a dict here that looks like a schema must be left as written.
_DATA_KEYWORDS = frozenset({"default", "const", "enum", "examples"})


def strict_tool_schema(model: type[BaseModel]) -> dict[str, Any]:
    return _closed_schema(model.model_json_schema())


# Strict tool use wants every object closed and every property required. Pydantic leaves
# properties with a default optional, so both are set here rather than trusted to the model.
def _closed_schema(schema: dict[str, Any]) -> dict[str, Any]:
    closed = {
        key: value if key in _DATA_KEYWORDS else _closed_value(value)
        for key, value in schema.items()
        if key != "properties"
    }
    if "properties" in schema:
        # Visited here so a field named "properties" is not mistaken for a schema keyword.
        properties = {name: _closed_schema(field) for name, field in schema["properties"].items()}
        closed |= {
            "properties": properties,
            "additionalProperties": False,
            "required": list(properties),
        }
    return closed


def _closed_value(value: Any) -> Any:
    if isinstance(value, dict):
        return _closed_schema(value)
    if isinstance(value, list):
        return [_closed_value(item) for item in value]
    return value

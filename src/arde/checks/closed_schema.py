"""Closed schemas: a reply may hold only the keys its schema lists, at every depth.

Plain JSON Schema validation accepts any key a schema does not mention, so a reply with a field
the spec leaves out would pass. Every object schema that lists its properties is closed here.
"""

import copy
from collections.abc import Mapping

# jsonschema ships no type information, and its stubs are not a dependency of ARDE.
from jsonschema import Draft202012Validator, ValidationError  # type: ignore[import-untyped]
from jsonschema.exceptions import best_match  # type: ignore[import-untyped]

from arde.domain.base import one_line

# The keywords whose values are data, not schemas: a default may be an object with a key named
# properties, and closing it would change the value the spec gives.
_VALUE_KEYWORDS = frozenset({"const", "default", "enum", "example", "examples"})
# The keywords whose value maps names to schemas, so the map itself is no schema.
_SCHEMA_MAP_KEYWORDS = frozenset(
    {"$defs", "definitions", "dependentSchemas", "patternProperties", "properties"}
)
# A schema that says either of these already says what it does with other keys.
_OTHER_KEYS_KEYWORDS = ("additionalProperties", "unevaluatedProperties")
# How much of one validation message a problem keeps. A message quotes the reply.
PROBLEM_LIMIT = 300


def close_schema(schema: dict[str, object]) -> dict[str, object]:
    """A copy of schema in which every object that lists its properties takes no other key.

    unevaluatedProperties, not additionalProperties, because it counts the keys that the parts of
    an allOf and the variant that matched have listed, where additionalProperties sees only the
    properties beside it. A part of an allOf is left open at its top, for the same reason: the
    schema that holds the allOf counts its keys. An object with no listed properties stays open,
    because it says nothing about its keys.
    """
    closed = _closed(schema, part_of_all=False)
    assert isinstance(closed, dict)
    return closed


class ClosedSchemas:
    """The spec's schemas, closed, to check replies against."""

    def __init__(self, spec: Mapping[str, object]) -> None:
        components = spec.get("components")
        schemas = components.get("schemas") if isinstance(components, dict) else None
        named = schemas if isinstance(schemas, dict) else {}
        self._components = {
            "schemas": {name: _closed(s, part_of_all=False) for name, s in named.items()}
        }

    def problems(self, schema: Mapping[str, object], body: object) -> list[str]:
        """What in body the closed schema refuses, each with where it is. Empty when it passes."""
        # The spec's references point into its components, so they sit beside the schema.
        root = {**close_schema(dict(schema)), "components": self._components}
        return sorted(_described(error) for error in Draft202012Validator(root).iter_errors(body))


def _closed(schema: object, *, part_of_all: bool) -> object:
    if not isinstance(schema, dict):
        return copy.deepcopy(schema)
    closed = {key: _closed_value(key, value) for key, value in schema.items()}
    if _lists_its_keys(schema) and not part_of_all:
        closed["unevaluatedProperties"] = False
    return closed


def _closed_value(key: str, value: object) -> object:
    if key in _VALUE_KEYWORDS or key.startswith("x-"):
        return copy.deepcopy(value)
    if key in _SCHEMA_MAP_KEYWORDS and isinstance(value, dict):
        return {name: _closed(schema, part_of_all=False) for name, schema in value.items()}
    if isinstance(value, list):
        return [_closed(item, part_of_all=key == "allOf") for item in value]
    return _closed(value, part_of_all=False)


def _lists_its_keys(schema: dict[str, object]) -> bool:
    lists = isinstance(schema.get("properties"), dict) or isinstance(schema.get("allOf"), list)
    return lists and not any(keyword in schema for keyword in _OTHER_KEYS_KEYWORDS)


# A value that fits none of the schemas it may take gets a message that quotes the value and gives
# no reason, so the reason of the closest schema stands in for it.
def _described(error: ValidationError) -> str:
    if error.context:
        closest = best_match(error.context)
        reason = f"fits none of the schemas it may take, the closest because {closest.message}"
    else:
        reason = error.message
    return f"{error.json_path}: {one_line(reason, PROBLEM_LIMIT)}"

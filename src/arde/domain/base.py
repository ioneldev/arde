"""The base every domain model builds on, and the helpers that keep model text safe."""

from typing import Annotated, Any

from pydantic import AfterValidator, BaseModel, ConfigDict


class Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


def _reject_line_breaks(text: str) -> str:
    if "\n" in text or "\r" in text:
        raise ValueError("must be a single line: remove the line break")
    return text


# Text the renderer writes into source comments. A second line there could be a compiler
# directive, so a line break is refused when the model is built, not when it is rendered.
SingleLine = Annotated[str, AfterValidator(_reject_line_breaks)]


def strict_tool_schema(model: type[BaseModel]) -> dict[str, Any]:
    return _closed_schema(model.model_json_schema())


# Strict tool use wants every object closed and every property required. Pydantic leaves
# properties with a default optional, so both are set here rather than trusted to the model.
def _closed_schema(schema: dict[str, Any]) -> dict[str, Any]:
    closed = {key: _closed_value(value) for key, value in schema.items() if key != "properties"}
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

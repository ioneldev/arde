"""What a declared Go type is in JSON, as encoding/json writes it.

The text is a type as goinspect prints it from the source, so a qualifier is the name the file
imports a package under, which is the package's own name unless the import renames it.
"""

import re

from arde.application.ports import JsonKind, JsonShape, StructRef

_INTEGERS = (
    "int",
    "int8",
    "int16",
    "int32",
    "int64",
    "uint",
    "uint8",
    "uint16",
    "uint32",
    "uint64",
    "uintptr",
    "byte",
    "rune",
)
_SCALARS: dict[str, JsonKind] = {
    **dict.fromkeys(_INTEGERS, "integer"),
    "float32": "number",
    "float64": "number",
    "string": "string",
    "bool": "boolean",
    # Standard types that encode as one value rather than as their fields.
    "time.Time": "string",
    "time.Duration": "integer",
    "json.Number": "number",
}
# Names that hold no shape of their own: any value, an error, a raw JSON document, or a complex
# number, which encoding/json refuses to write.
_SHAPELESS = frozenset({"any", "error", "comparable", "complex64", "complex128", "json.RawMessage"})
# encoding/json writes a byte slice as one base64 string, though an array of bytes as numbers.
_BYTE_SLICES = frozenset({"[]byte", "[]uint8"})
_SLICE = "[]"
_MAP = "map["
_FIXED_ARRAY = re.compile(r"\[[^\[\]]+\](.+)", re.DOTALL)
# A generic instance, such as Page[Item], does not match: its fields name its type parameters.
_NAMED = re.compile(r"(?:([^\W\d]\w*)\.)?([^\W\d]\w*)")
_UNKNOWN = JsonShape(kind="unknown")


def json_shape(declared: str) -> JsonShape:
    text = _without_pointers(declared)
    if text in _SCALARS:
        return JsonShape(kind=_SCALARS[text])
    if text in _BYTE_SLICES:
        return JsonShape(kind="string")
    if text.startswith(_SLICE):
        return JsonShape(kind="array", element=text.removeprefix(_SLICE))
    if fixed := _FIXED_ARRAY.fullmatch(text):
        return JsonShape(kind="array", element=fixed.group(1))
    if text.startswith(_MAP):
        return _map_shape(text)
    named = _NAMED.fullmatch(text)
    if named is None or text in _SHAPELESS:
        return _UNKNOWN
    package, name = named.groups()
    return JsonShape(kind="object", struct=StructRef(name=name, package=package))


# encoding/json writes what a pointer points to, and parentheses change nothing in a type.
def _without_pointers(declared: str) -> str:
    text = declared.strip()
    while True:
        if text.startswith("*"):
            text = text[1:].strip()
        elif text.startswith("(") and text.endswith(")"):
            text = text[1:-1].strip()
        else:
            return text


# The key may hold brackets of its own, as in map[[2]int]string, so the value starts after the
# bracket that closes the first one.
def _map_shape(text: str) -> JsonShape:
    depth = 0
    for index in range(len(_MAP) - 1, len(text)):
        if text[index] == "[":
            depth += 1
        elif text[index] == "]":
            depth -= 1
            if depth == 0:
                value = text[index + 1 :]
                return JsonShape(kind="object", element=value) if value else _UNKNOWN
    return _UNKNOWN

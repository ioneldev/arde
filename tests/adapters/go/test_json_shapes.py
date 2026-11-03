"""What a declared Go type is in JSON, as encoding/json writes it."""

from pathlib import Path

import pytest

from arde.adapters.language.go.adapter import GoAdapter
from arde.adapters.language.go.goinspect_client import GoinspectClient
from arde.adapters.language.go.json_shapes import json_shape
from arde.application.ports import JsonShape, StructRef

UNKNOWN = JsonShape(kind="unknown")


def an_object(name: str, package: str | None = None) -> JsonShape:
    return JsonShape(kind="object", struct=StructRef(name=name, package=package))


def an_array(element: str) -> JsonShape:
    return JsonShape(kind="array", element=element)


@pytest.mark.parametrize(
    "declared",
    ["int", "int8", "int32", "int64", "uint", "uint8", "uint64", "uintptr", "byte", "rune"],
)
def test_every_integer_type_is_an_integer(declared: str) -> None:
    assert json_shape(declared) == JsonShape(kind="integer")


@pytest.mark.parametrize(
    ("declared", "kind"),
    [
        ("float32", "number"),
        ("float64", "number"),
        ("json.Number", "number"),
        ("string", "string"),
        ("time.Time", "string"),
        ("bool", "boolean"),
        ("time.Duration", "integer"),
    ],
)
def test_a_scalar_is_its_json_kind(declared: str, kind: str) -> None:
    assert json_shape(declared) == JsonShape.model_validate({"kind": kind})


# encoding/json writes a byte slice as one base64 string, not as an array of numbers.
@pytest.mark.parametrize("declared", ["[]byte", "[]uint8"])
def test_a_byte_slice_is_a_string(declared: str) -> None:
    assert json_shape(declared) == JsonShape(kind="string")


@pytest.mark.parametrize(
    ("declared", "element"),
    [
        ("[]ItemView", "ItemView"),
        ("[]*pricing.Rate", "*pricing.Rate"),
        ("[][]int", "[]int"),
        ("[3]int", "int"),
        ("[16]byte", "byte"),
        ("[]map[string]int", "map[string]int"),
    ],
)
def test_a_slice_or_an_array_holds_its_element(declared: str, element: str) -> None:
    assert json_shape(declared) == an_array(element)


@pytest.mark.parametrize(
    ("declared", "element"),
    [
        ("map[string]int", "int"),
        ("map[string][]ItemView", "[]ItemView"),
        ("map[int64]*pricing.Rate", "*pricing.Rate"),
        ("map[[2]int]string", "string"),
    ],
)
def test_a_map_is_an_object_whose_values_are_its_element(declared: str, element: str) -> None:
    assert json_shape(declared) == JsonShape(kind="object", element=element)


@pytest.mark.parametrize(
    ("declared", "shape"),
    [
        ("Quote", an_object("Quote")),
        ("*Quote", an_object("Quote")),
        ("**Quote", an_object("Quote")),
        ("(Quote)", an_object("Quote")),
        ("pricing.Rate", an_object("Rate", "pricing")),
        ("*pricing.Rate", an_object("Rate", "pricing")),
    ],
)
def test_a_named_type_refers_to_the_struct_it_names(declared: str, shape: JsonShape) -> None:
    assert json_shape(declared) == shape


# None of these says what JSON it holds: any value, a raw document, a generic struct whose fields
# name its type parameters, or a type encoding/json refuses.
@pytest.mark.parametrize(
    "declared",
    [
        "any",
        "interface{}",
        "interface{ String() string }",
        "error",
        "json.RawMessage",
        "struct{ A int }",
        "Page[ItemView]",
        "pricing.Page[ItemView]",
        "func()",
        "chan int",
        "complex128",
        "",
        "map[string",
    ],
)
def test_a_type_with_no_shape_of_its_own_is_unknown(declared: str) -> None:
    assert json_shape(declared) == UNKNOWN


def test_the_adapter_answers_with_the_go_reading() -> None:
    adapter = GoAdapter(Path("goinspect-not-built"), swag=Path("swag-not-built"))

    assert adapter.json_shape("[]*Quote") == an_array("*Quote")


REAL_STRUCT = """package shapes

import (
\t"encoding/json"
\t"time"
)

type Money struct {
\tCents int64 `json:"cents"`
}

type Page[T any] struct {
\tItems []T `json:"items"`
}

type Every struct {
\tCount    int64              `json:"count"`
\tRatio    float32            `json:"ratio"`
\tName     string             `json:"name"`
\tDone     bool               `json:"done"`
\tAt       time.Time          `json:"at"`
\tRaw      json.RawMessage    `json:"raw"`
\tBlob     []byte             `json:"blob"`
\tPrice    *Money             `json:"price"`
\tPrices   []*Money           `json:"prices"`
\tGrid     [3][]int           `json:"grid"`
\tByName   map[string][]Money `json:"by_name"`
\tAnything any                `json:"anything"`
\tEmpty    interface{}        `json:"empty"`
\tInline   struct{ A int }    `json:"inline"`
\tPaged    Page[Money]        `json:"paged"`
}
"""
REAL_SHAPES = {
    "Count": JsonShape(kind="integer"),
    "Ratio": JsonShape(kind="number"),
    "Name": JsonShape(kind="string"),
    "Done": JsonShape(kind="boolean"),
    "At": JsonShape(kind="string"),
    "Raw": UNKNOWN,
    "Blob": JsonShape(kind="string"),
    "Price": an_object("Money"),
    "Prices": an_array("*Money"),
    "Grid": an_array("[]int"),
    "ByName": JsonShape(kind="object", element="[]Money"),
    "Anything": UNKNOWN,
    "Empty": UNKNOWN,
    "Inline": UNKNOWN,
    "Paged": UNKNOWN,
}


# The types as goinspect prints them from source, which is the text the inventory holds.
@pytest.mark.integration
def test_the_types_goinspect_prints_read_as_their_json(
    goinspect_binary: Path, tmp_path: Path
) -> None:
    (tmp_path / "shapes.go").write_text(REAL_STRUCT, encoding="utf-8")

    fields = GoinspectClient(goinspect_binary).declarations(tmp_path).fields

    every = {field.name: json_shape(field.go_type) for field in fields if field.struct == "Every"}
    assert every == REAL_SHAPES

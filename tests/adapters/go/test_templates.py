"""The text of every annotation and tag ARDE writes, checked against how swag reads it."""

import re
from typing import Any

import pytest

from arde.adapters.language.go.directives import is_ordinary_comment
from arde.adapters.language.go.errors import RenderError
from arde.adapters.language.go.struct_tags import OPTIONAL_PAIR, one_of_pair, retagged
from arde.adapters.language.go.templates import (
    endpoint_block,
    field_comment,
    general_block,
    param_line,
)
from arde.domain.facts import CodeQuote, EndpointFact, Links, ParamFact, ResponseFact

QUOTE = CodeQuote(kind="code", file="web/handlers.go", quote="pieces := 1")


def a_param(**overrides: Any) -> ParamFact:
    fields: dict[str, Any] = {
        "name": "pieces",
        "location": "query",
        "declared_type": "int",
        "required": False,
        "description": "Units to price",
        "minimum": 1,
        "maximum": 10,
        "enum": (),
        "default": "1",
    }
    return ParamFact(**{**fields, **overrides})


def a_response(**overrides: Any) -> ResponseFact:
    fields: dict[str, Any] = {
        "status": 200,
        "declared_type": "web.Quote",
        "description": "The priced item",
        "error_codes": (),
    }
    return ResponseFact(**{**fields, **overrides})


def an_endpoint(**overrides: Any) -> EndpointFact:
    fields: dict[str, Any] = {
        "id": "endpoint:quote",
        "topic": "api",
        "evidence": (QUOTE,),
        "links": Links(routes=("GET /items/{sku}",)),
        "kind": "endpoint",
        "route": "GET /items/{sku}",
        "summary": "Price some pieces of one item",
        "params": (a_param(),),
        "responses": (a_response(),),
    }
    return EndpointFact(**{**fields, **overrides})


PIECES = a_param()
WRAPPING = a_param(
    name="wrapping",
    declared_type="string",
    description="How the order is wrapped",
    minimum=None,
    maximum=None,
    enum=("NONE", "PAPER", "BOX"),
    default="NONE",
)
INDEX = an_endpoint(route="GET /", summary="List the items", params=())


# How swag v2.0.0-rc6 reads a @Param line, from its operation.go: the five parts, then each
# attribute as the first match of its pattern anywhere in the line, cut at the first ")".
SWAG_PARAM = re.compile(r'(\S+)\s+(\w+)\s+([\S. ]+?)\s+(\w+)\s+"([^"]+)"')
SWAG_ATTRIBUTES = {
    "enums": re.compile(r"(?i)\s+enums\(.*\)"),
    "maximum": re.compile(r"(?i)\s+maxinum|maximum\(.*\)"),
    "minimum": re.compile(r"(?i)\s+mininum|minimum\(.*\)"),
    "default": re.compile(r"(?i)\s+default\(.*\)"),
    "minlength": re.compile(r"(?i)\s+minlength\(.*\)"),
    "maxlength": re.compile(r"(?i)\s+maxlength\(.*\)"),
    "format": re.compile(r"(?i)\s+format\(.*\)"),
    "example": re.compile(r"(?i)\s+example\(.*\)"),
}


def swag_reads(line: str) -> dict[str, str]:
    remainder = line.removeprefix("// @Param ")
    parts = SWAG_PARAM.search(remainder)
    assert parts is not None, f"swag would refuse {line!r}"
    read = {"name": parts[1], "description": parts[5]}
    for name, pattern in SWAG_ATTRIBUTES.items():
        found = pattern.search(remainder)
        if found and "(" in found[0] and ")" in found[0]:
            text = found[0]
            read[name] = text[text.index("(") + 1 : text.index(")")].strip()
    return read


def test_param_line_matches_spec_exactly() -> None:
    assert param_line(PIECES) == (
        '// @Param pieces query int false "Units to price" minimum(1) maximum(10) default(1)'
    )


def test_enum_and_default() -> None:
    assert "Enums(NONE, PAPER, BOX) default(NONE)" in param_line(WRAPPING)


def test_double_quotes_in_description_are_escaped() -> None:
    line = param_line(a_param(description='Say "how many"'))

    assert swag_reads(line)["description"] == "Say 'how many'"


@pytest.mark.parametrize(
    "description",
    [
        "Units default(9) to price",
        "Units maximum(99)",
        "Units, at least minimum(0)",
        "Pick enums(a,b)",
        "As example(3)",
    ],
)
def test_a_description_cannot_add_or_change_an_attribute(description: str) -> None:
    read = swag_reads(param_line(a_param(description=description)))

    assert {name: read.get(name) for name in ("minimum", "maximum", "default", "enums")} == {
        "minimum": "1",
        "maximum": "10",
        "default": "1",
        "enums": None,
    }
    assert "example" not in read


def test_a_required_path_param_reads_true() -> None:
    line = param_line(a_param(name="sku", location="path", declared_type="string", required=True))

    assert line.startswith("// @Param sku path string true ")


def test_a_form_param_reads_as_swag_names_form_data() -> None:
    line = param_line(a_param(name="note", location="form", declared_type="string"))

    assert line.startswith("// @Param note formData string false ")


def test_a_header_param_reads_header() -> None:
    line = param_line(a_param(name="X-Request-Id", location="header", declared_type="string"))

    assert line.startswith("// @Param X-Request-Id header string false ")


@pytest.mark.parametrize("declared_type", ["bool", "number"])
def test_a_bool_or_number_param_type_reads_as_it_is(declared_type: str) -> None:
    line = param_line(a_param(declared_type=declared_type, minimum=None, maximum=None))

    assert line.startswith(f"// @Param pieces query {declared_type} false ")


@pytest.mark.parametrize("name", ["maximum(5)", "a(b", "a)b"])
def test_a_param_name_swag_would_read_as_an_attribute_stops_the_render(name: str) -> None:
    with pytest.raises(RenderError, match="parenthesis"):
        param_line(a_param(name=name))


def test_general_block_uses_version_0_0_0() -> None:
    assert "// @version 0.0.0" in general_block("Shop API")


def test_general_block_names_the_api() -> None:
    assert general_block("Shop API") == ["// @title Shop API", "// @version 0.0.0"]


def test_root_route_renders_as_slash() -> None:
    assert "// @Router / [get]" in endpoint_block([INDEX])


def test_an_endpoint_block_reads_summary_produce_params_responses_then_router() -> None:
    fact = an_endpoint(
        params=(
            a_param(
                name="sku",
                location="path",
                declared_type="string",
                required=True,
                description="Stock code",
                minimum=None,
                maximum=None,
                default=None,
            ),
            PIECES,
        ),
        responses=(
            a_response(),
            a_response(status=404, declared_type="web.problem", description="No such item"),
        ),
    )

    assert endpoint_block([fact]) == [
        "// @Summary Price some pieces of one item",
        "// @Produce json",
        '// @Param sku path string true "Stock code"',
        '// @Param pieces query int false "Units to price" minimum(1) maximum(10) default(1)',
        '// @Success 200 {object} web.Quote "The priced item"',
        '// @Failure 404 {object} web.problem "No such item"',
        "// @Router /items/{sku} [get]",
    ]


def test_a_handler_serving_several_routes_gets_one_router_line_per_route() -> None:
    block = endpoint_block(
        [an_endpoint(route="GET /ping"), an_endpoint(route="HEAD /ping", summary="Other")]
    )

    assert [line for line in block if line.startswith("// @Router")] == [
        "// @Router /ping [get]",
        "// @Router /ping [head]",
    ]
    assert [line for line in block if line.startswith("// @Summary")] == [
        "// @Summary Price some pieces of one item"
    ]


@pytest.mark.parametrize(
    ("declared", "rendered"),
    [("*web.Quote", "web.Quote"), ("[]*web.ItemView", "[]web.ItemView"), ("string", "string")],
)
def test_a_response_type_is_written_as_swag_reads_it(declared: str, rendered: str) -> None:
    block = endpoint_block([an_endpoint(responses=(a_response(declared_type=declared),))])

    assert f"// @Success 200 {{object}} {rendered} " in "\n".join(block)


def test_a_double_quote_in_a_response_description_is_written_as_a_single_one() -> None:
    block = endpoint_block([an_endpoint(responses=(a_response(description='The "best" price'),))])

    assert "// @Success 200 {object} web.Quote \"The 'best' price\"" in block


@pytest.mark.parametrize("route", ["GET /a,b", "GET /café", "PROPFIND /files"])
def test_a_route_swag_cannot_read_stops_the_render(route: str) -> None:
    with pytest.raises(RenderError, match="@Router"):
        endpoint_block([an_endpoint(route=route)])


# Each would make a rendered line more than an ordinary comment, which the comparison of a later
# run reads as a change to the code.
NOT_ORDINARY = [
    "Rows /* or columns */",
    "Built with +build tags",
    "Built with +BUILD tags",
    "Code generated by the server",
    "#cgo flags",
    "Output: the total",
    "Unordered output: the rows",
]


@pytest.mark.parametrize("text", NOT_ORDINARY)
def test_every_rendered_line_is_an_ordinary_comment(text: str) -> None:
    fact = an_endpoint(
        summary=text,
        params=(a_param(description=text),),
        responses=(a_response(description=text),),
    )
    lines = [*endpoint_block([fact]), *general_block(text), *field_comment(text)]

    assert [line for line in lines if not is_ordinary_comment(line)] == []


def test_a_field_comment_is_the_description() -> None:
    assert field_comment("The item's stock code") == ["// The item's stock code"]


def test_an_empty_field_description_writes_nothing() -> None:
    assert field_comment(" ") == []


# Struct tags


def test_the_polymorphic_tag_lists_its_variants_in_order() -> None:
    assert (
        one_of_pair(("web.LeafTraits", "web.CupTraits")) == 'oneOf:"web.LeafTraits,web.CupTraits"'
    )


def test_a_pointer_variant_is_written_as_its_target() -> None:
    assert one_of_pair(("*web.LeafTraits",)) == 'oneOf:"web.LeafTraits"'


def test_arde_pairs_go_after_the_persons_own() -> None:
    assert retagged('json:"note,omitempty"', [OPTIONAL_PAIR]) == (
        'json:"note,omitempty" binding:"optional"'
    )


def test_a_tag_already_holding_the_wanted_pairs_is_unchanged() -> None:
    tag = 'json:"note,omitempty" binding:"optional"'

    assert retagged(tag, [OPTIONAL_PAIR]) == tag


def test_a_pair_no_longer_wanted_is_removed() -> None:
    assert retagged('json:"traits" oneOf:"web.A,web.B"', []) == 'json:"traits"'


def test_a_tag_with_nothing_of_arde_is_left_exactly_as_written() -> None:
    assert retagged('json:"a"   xml:"a"', []) == 'json:"a"   xml:"a"'


def test_a_persons_binding_is_kept_and_wins() -> None:
    assert retagged('json:"a,omitempty" binding:"required"', [OPTIONAL_PAIR]) == (
        'json:"a,omitempty" binding:"required"'
    )


@pytest.mark.parametrize("tag", ['json:"a\\"b"', 'json:"a",xml:"b"', 'json:"a" json:"b"', "json"])
def test_a_tag_arde_cannot_split_is_not_edited(tag: str) -> None:
    assert retagged(tag, []) == tag
    assert retagged(tag, [OPTIONAL_PAIR]) is None

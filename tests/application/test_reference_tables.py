"""The reference tables code renders from facts, so a new param or field shows as one new row."""

import difflib
from typing import Any

import pytest

from arde.application.reference_tables import ReferenceTableError, api_reference, error_table
from arde.domain.facts import (
    CodeQuote,
    EndpointFact,
    Fact,
    FieldFact,
    Links,
    ParamFact,
    PolymorphicFact,
    ResponseFact,
    RuleFact,
    UnreachableFact,
    Wiki,
)
from arde.domain.inventory import Inventory, ResponseField

QUOTE_ROUTE = "GET /quote/{sku}"
LIST_ROUTE = "GET /"
EVIDENCE = (CodeQuote(kind="code", file="web/handlers.go", quote="refuse(w, code, status)"),)


def a_param(**overrides: Any) -> ParamFact:
    fields: dict[str, Any] = {
        "name": "pieces",
        "location": "query",
        "declared_type": "int",
        "required": False,
        "description": "How many pieces to price",
        "minimum": 1,
        "maximum": 50,
        "enum": (),
        "default": "1",
    }
    return ParamFact(**{**fields, **overrides})


SKU = a_param(
    name="sku",
    location="path",
    declared_type="string",
    required=True,
    description="The item to price",
    minimum=None,
    maximum=None,
    default=None,
)


def a_response(**overrides: Any) -> ResponseFact:
    fields: dict[str, Any] = {
        "status": 400,
        "declared_type": "web.problem",
        "description": "The request was refused",
        "error_codes": ("bad_pieces",),
    }
    return ResponseFact(**{**fields, **overrides})


QUOTED = a_response(status=200, declared_type="web.Quote", description="The quote", error_codes=())
UNKNOWN = a_response(status=404, description="No such item", error_codes=("unknown_sku",))


def an_endpoint(**overrides: Any) -> EndpointFact:
    fields: dict[str, Any] = {
        "id": "endpoint:quote",
        "topic": "api",
        "evidence": EVIDENCE,
        "links": Links(routes=(QUOTE_ROUTE,)),
        "kind": "endpoint",
        "route": QUOTE_ROUTE,
        "summary": "Price a number of pieces of one item",
        "params": (SKU, a_param()),
        "responses": (QUOTED, a_response(), UNKNOWN),
    }
    return EndpointFact(**{**fields, **overrides})


def a_field_fact(field: str, description: str, **overrides: Any) -> FieldFact:
    fields: dict[str, Any] = {
        "id": f"field:{field}",
        "topic": "api",
        "evidence": EVIDENCE,
        "links": Links(declarations=("web/payloads.go::type Quote",)),
        "kind": "field",
        "struct": "Quote",
        "field": field,
        "description": description,
    }
    return FieldFact(**{**fields, **overrides})


def a_response_field(name: str, json_name: str, **overrides: Any) -> ResponseField:
    fields: dict[str, Any] = {
        "struct": "Quote",
        "name": name,
        "json_name": json_name,
        "declared_type": "int64",
        "may_be_absent": False,
        "file": "web/payloads.go",
        "package": "web",
    }
    return ResponseField(**{**fields, **overrides})


def an_inventory(*fields: ResponseField) -> Inventory:
    return Inventory(
        routes=(),
        params=(),
        error_codes=(),
        response_fields=fields,
        env_vars=(),
        make_targets=(),
    )


INVENTORY = an_inventory(
    a_response_field("ChargeCents", "charge_cents"),
    a_response_field("Note", "note", declared_type="string", may_be_absent=True),
    a_response_field("Traits", "traits", declared_type="any"),
)
CHARGE = a_field_fact("ChargeCents", "What the pieces cost, in cents")
NOTE = a_field_fact("Note", "Why the price differs from the list")


def a_wiki(*facts: Fact) -> Wiki:
    return Wiki(facts=facts)


def rows_of(table: str) -> list[str]:
    return [line for line in table.splitlines() if line.startswith("| ") and "---" not in line]


def test_api_reference_has_row_per_param_and_field() -> None:
    reference = api_reference(a_wiki(an_endpoint(), CHARGE, NOTE), INVENTORY)

    assert "### `GET /quote/{sku}`: Price a number of pieces of one item" in reference
    assert rows_of(reference)[:2] == [
        "| Parameter | In | Type | Required | Description | Minimum | Maximum | Choices "
        "| Default |",
        "| `sku` | path | string | yes | The item to price |  |  |  |  |",
    ]
    assert "| `pieces` | query | int | no | How many pieces to price | 1 | 50 |  | `1` |" in (
        reference.splitlines()
    )
    assert [row for row in rows_of(reference) if row.startswith("| `Quote` |")] == [
        "| `Quote` | `ChargeCents` | `charge_cents` | `int64` | no "
        "| What the pieces cost, in cents |",
        "| `Quote` | `Note` | `note` | `string` | yes | Why the price differs from the list |",
    ]


def test_each_response_is_a_row_with_its_error_codes() -> None:
    reference = api_reference(a_wiki(an_endpoint()), INVENTORY)

    assert "| 200 | `web.Quote` | The quote |  |" in reference.splitlines()
    assert "| 404 | `web.problem` | No such item | `unknown_sku` |" in reference.splitlines()


def test_new_param_changes_one_table_row_only() -> None:
    before = api_reference(a_wiki(an_endpoint(), CHARGE), INVENTORY)
    color = a_param(name="color", declared_type="string", description="The color to price")
    wider = an_endpoint(params=(SKU, a_param(), color.model_copy(update={"default": None})))

    after = api_reference(a_wiki(wider, CHARGE), INVENTORY)

    changed = [
        line
        for line in difflib.ndiff(before.splitlines(), after.splitlines())
        if line.startswith(("+ ", "- "))
    ]
    assert changed == ["+ | `color` | query | string | no | The color to price | 1 | 50 |  |  |"]


def test_new_field_changes_one_table_row_only() -> None:
    before = api_reference(a_wiki(an_endpoint(), CHARGE), INVENTORY)

    after = api_reference(a_wiki(an_endpoint(), CHARGE, NOTE), INVENTORY)

    changed = [
        line
        for line in difflib.ndiff(before.splitlines(), after.splitlines())
        if line.startswith(("+ ", "- "))
    ]
    assert changed == [
        "+ | `Quote` | `Note` | `note` | `string` | yes | Why the price differs from the list |"
    ]


# Another task adds locations and types to ParamFact, and the table must not need to know them.
def test_a_location_or_type_the_tables_have_never_seen_prints_as_written() -> None:
    unseen = ParamFact.model_construct(**{**a_param().model_dump(), "location": "header"})
    flagged = unseen.model_copy(update={"declared_type": "bool", "name": "X-Trace"})

    reference = api_reference(a_wiki(an_endpoint(params=(flagged,))), INVENTORY)

    assert "| `X-Trace` | header | bool | no |" in reference


def test_a_polymorphic_field_lists_its_variants() -> None:
    traits = a_field_fact("Traits", "What kind of item it is")
    variants = PolymorphicFact(
        id="polymorphic:traits",
        topic="api",
        evidence=EVIDENCE,
        links=Links(declarations=("web/payloads.go::type Quote",)),
        kind="polymorphic",
        struct="Quote",
        field="Traits",
        variants=("web.LeafTraits", "web.CupTraits"),
    )

    reference = api_reference(a_wiki(an_endpoint(), traits, variants), INVENTORY)

    assert [row for row in rows_of(reference) if "`Traits`" in row] == [
        "| `Quote` | `Traits` | `traits` | `any`, one of `web.LeafTraits`, `web.CupTraits` | no "
        "| What kind of item it is |"
    ]


def test_two_structs_of_one_name_are_told_apart_by_the_facts_link() -> None:
    other = a_response_field("ChargeCents", "total_cents", file="legacy/payloads.go")
    inventory = an_inventory(a_response_field("ChargeCents", "charge_cents"), other)
    linked = CHARGE.model_copy(
        update={"links": Links(declarations=("legacy/payloads.go::type Quote",))}
    )

    reference = api_reference(a_wiki(an_endpoint(), linked), inventory)

    assert "`total_cents`" in reference
    assert "`charge_cents`" not in reference


def test_a_field_the_inventory_does_not_hold_stops_with_its_name() -> None:
    invented = a_field_fact("Discount", "A discount")

    with pytest.raises(ReferenceTableError) as stopped:
        api_reference(a_wiki(an_endpoint(), invented), INVENTORY)

    assert "Quote.Discount" in str(stopped.value)
    assert stopped.value.next_action


def test_text_that_would_split_a_cell_is_escaped() -> None:
    piped = a_param(description="Pieces | boxes, as a\\ number")

    reference = api_reference(a_wiki(an_endpoint(params=(piped,))), INVENTORY)

    assert "| Pieces \\| boxes, as a\\\\ number |" in reference


# Fact text came from the repo by way of a model, and an HTML comment it opened would hide the
# rest of the page from a reader.
def test_fact_text_cannot_open_an_html_comment_or_tag() -> None:
    hidden = an_endpoint(summary="Price it <!-- the rest is hidden")

    reference = api_reference(a_wiki(hidden), INVENTORY)

    assert "### `GET /quote/{sku}`: Price it \\<!-- the rest is hidden" in reference
    assert "<!--" not in reference.replace("\\<!--", "")


def test_a_value_holding_a_backtick_stays_one_code_span() -> None:
    odd = a_param(enum=("a`b",), default="`x")

    reference = api_reference(a_wiki(an_endpoint(params=(odd,))), INVENTORY)

    assert "| 1 | 50 | ``a`b`` | `` `x `` |" in reference


def test_a_rule_on_the_api_topic_is_kept_in_the_reference() -> None:
    rule = RuleFact(
        id="rule:case",
        topic="api",
        evidence=EVIDENCE,
        links=Links(routes=(QUOTE_ROUTE,)),
        kind="rule",
        statement="The sku is matched\nwhatever its case.",
    )

    reference = api_reference(a_wiki(an_endpoint(), rule), INVENTORY)

    assert "## Notes\n\n- The sku is matched whatever its case." in reference


def test_endpoints_are_listed_by_path_whatever_the_wiki_order() -> None:
    listing = an_endpoint(id="endpoint:list", route=LIST_ROUTE, params=(), responses=(QUOTED,))

    reference = api_reference(a_wiki(an_endpoint(), listing), INVENTORY)

    assert reference.index("### `GET /`") < reference.index("### `GET /quote/{sku}`")


def test_the_reference_holds_the_error_table() -> None:
    wiki = a_wiki(an_endpoint())

    assert error_table(wiki) in api_reference(wiki, INVENTORY)


def test_error_table_marks_unreachable_codes() -> None:
    unreachable = UnreachableFact(
        id="unreachable:internal",
        topic="errors",
        evidence=EVIDENCE,
        links=Links(files=("web/failures.go",)),
        kind="unreachable",
        error_code="internal",
        reason="Every handler checks its input\nbefore it reads the store.",
    )

    table = error_table(a_wiki(an_endpoint(), unreachable))

    assert rows_of(table) == [
        "| Code | Status | Returned by | Note |",
        "| `bad_pieces` | 400 | `GET /quote/{sku}` |  |",
        "| `internal` |  |  | Never returned: Every handler checks its input before it reads the "
        "store. |",
        "| `unknown_sku` | 404 | `GET /quote/{sku}` |  |",
    ]


def test_a_code_several_routes_return_is_one_row_naming_them_all() -> None:
    listing = an_endpoint(id="endpoint:list", route=LIST_ROUTE, params=(), responses=(UNKNOWN,))

    table = error_table(a_wiki(an_endpoint(), listing))

    assert "| `unknown_sku` | 404 | `GET /`, `GET /quote/{sku}` |  |" in rows_of(table)

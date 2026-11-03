"""How code scores the blind judge: a prediction against the reply, the arithmetic the judge may
ask for, and the items a run requires it to try.

The service is the shop fixture's, as its facts describe it, so each reply here is one the shop
gives.
"""

import json

import pytest
from pydantic import ValidationError
from shop_facts import (
    BAD_PIECES,
    LISTING,
    NOTHING,
    TEA,
    a_field_fact,
    a_param,
    a_response,
    a_rule,
    an_endpoint,
    an_unreachable,
    covering_facts,
    quote,
)
from shop_support import SHOP_INVENTORY

from arde.application.judge_scoring import (
    JudgeScope,
    NotArithmetic,
    Prediction,
    RequiredItems,
    RequiredParam,
    compare_prediction,
    required_items,
    safe_calculate,
    tries_required,
    untried,
)
from arde.application.ports import HttpRequest
from arde.domain.facts import Fact, Links, ProbeRecord, Wiki
from arde.domain.inventory import ErrorCode, Inventory, Route

SHOP_FACTS = covering_facts(SHOP_INVENTORY)
SHOP_WIKI = Wiki(facts=SHOP_FACTS, probes=(TEA, NOTHING, BAD_PIECES, LISTING))
QUOTE_ROUTE = "GET /items/{sku}"


def pred(
    path_and_query: str = "/items/tea-100",
    *,
    status: int | str = 200,
    method: str = "GET",
    **fields: object,
) -> Prediction:
    return Prediction.model_validate(
        {"method": method, "path_and_query": path_and_query, "status": status, "fields": fields}
    )


def actual(
    body: object, *, status: int = 200, path_and_query: str = "/items/tea-100"
) -> ProbeRecord:
    return ProbeRecord(
        kind="probe",
        method="GET",
        path_and_query=path_and_query,
        status=status,
        content_type="application/json",
        body=body if isinstance(body, str) else json.dumps(body),
    )


# A prediction against the reply


def test_status_and_fields_compared() -> None:
    assert compare_prediction(pred(sku="tea-100", pieces=1), TEA) is None

    wrong_status = compare_prediction(pred(status=404, sku="tea-100"), TEA)
    wrong_field = compare_prediction(pred(pieces=2), TEA)

    assert wrong_status is not None
    assert wrong_status.detail == "status: predicted 404, the service answered 200"
    assert (wrong_status.prediction, wrong_status.actual) == (pred(status=404, sku="tea-100"), TEA)
    assert wrong_field is not None
    assert wrong_field.detail == "pieces: predicted 2, the reply holds 1"


def test_fields_the_judge_did_not_predict_are_not_compared() -> None:
    assert compare_prediction(pred(), TEA) is None


def test_total_compared_exactly() -> None:
    mismatch = compare_prediction(pred(total=20.0), actual({"total": 19.996}))

    assert mismatch is not None
    assert mismatch.detail == "total: predicted 20.0, the reply holds 19.996"


def test_an_integer_and_a_float_of_the_same_value_are_equal() -> None:
    assert compare_prediction(pred(total=450.0), actual({"total": 450})) is None


@pytest.mark.parametrize(
    ("predicted", "held"),
    [(True, 1), (1, True), ("1", 1), (None, 0), (0, None), ("null", None)],
)
def test_a_value_of_another_json_type_is_a_mismatch(predicted: object, held: object) -> None:
    assert compare_prediction(pred(value=predicted), actual({"value": held})) is not None


def test_null_matches_null() -> None:
    assert compare_prediction(pred(note=None), actual({"note": None})) is None


def test_a_dotted_path_reaches_through_objects_and_arrays() -> None:
    body = {"items": [{"price": {"amount": 3}}, {"price": {"amount": 5}}]}

    assert compare_prediction(pred(**{"items.1.price.amount": 5}), actual(body)) is None
    missing = compare_prediction(pred(**{"items.2.price.amount": 5}), actual(body))
    assert missing is not None
    assert missing.detail == "items.2.price.amount: predicted 5, the reply holds no such field"


def test_a_scalar_predicted_where_the_reply_holds_an_object_is_a_mismatch() -> None:
    mismatch = compare_prediction(pred(price=3), actual({"price": {"amount": 3}}))

    assert mismatch is not None
    assert mismatch.detail == 'price: predicted 3, the reply holds {"amount": 3}'


def test_fields_predicted_of_a_body_that_is_not_json_are_a_mismatch() -> None:
    reply = actual("404 page not found\n", status=404)

    mismatch = compare_prediction(pred(status=404, code="unknown_sku"), reply)

    assert mismatch is not None
    assert mismatch.detail == 'code: predicted "unknown_sku", and the reply body is not JSON'


def test_every_wrong_part_is_in_the_detail() -> None:
    mismatch = compare_prediction(pred(status=201, pieces=2, sku="tea-100"), TEA)

    assert mismatch is not None
    assert mismatch.detail == (
        "status: predicted 201, the service answered 200; pieces: predicted 2, the reply holds 1"
    )


def test_an_unknown_status_is_not_compared() -> None:
    assert compare_prediction(pred(status="unknown"), NOTHING) is None


@pytest.mark.parametrize(
    "changes",
    [
        {"method": "POST"},
        {"path_and_query": "http://elsewhere.example/items"},
        {"path_and_query": "/items/a b"},
        {"status": 99},
        {"status": "maybe"},
        {"fields": {"items..sku": "x"}},
        {"fields": {"": "x"}},
        {"fields": {"items": ["x"]}},
        # The trace is JSON, which holds no infinity, and a model can write 1e400.
        {"fields": {"total": float("inf")}},
        {"fields": {"total": float("nan")}},
    ],
)
def test_a_prediction_that_breaks_a_rule_is_refused(changes: dict[str, object]) -> None:
    fields = {"method": "GET", "path_and_query": "/items/tea-100", "status": 200, "fields": {}}

    with pytest.raises(ValidationError):
        Prediction.model_validate({**fields, **changes})


def test_a_prediction_names_the_request_it_makes() -> None:
    assert pred("/items/tea-100?pieces=2", method="HEAD").request == HttpRequest(
        method="HEAD", path_and_query="/items/tea-100?pieces=2"
    )


# Arithmetic


def test_calculate_does_arithmetic() -> None:
    assert safe_calculate("(450 * 3 + 20) / 2") == 685.0
    assert safe_calculate("-4 + +1") == -3.0
    assert safe_calculate("round(10 / 3, 2)") == 3.33
    assert safe_calculate("round(2.5)") == 2.0
    assert safe_calculate("0.1 + 0.2") == 0.1 + 0.2


@pytest.mark.parametrize(
    "expression",
    [
        "__import__('os')",
        "__import__('os').system('true')",
        "2 ** 10",
        "7 // 2",
        "7 % 2",
        "abs(-1)",
        "x + 1",
        "'a' * 3",
        "1 if 1 else 2",
        "True + 1",
        "1j * 2",
        "[1][0]",
        "round(1, 2, 3)",
        "round(number=1.5)",
        "round(1.5, 0.5)",
        "(lambda: 1)()",
        "1 < 2",
        "",
        "1 +",
    ],
)
def test_calculate_rejects_non_arithmetic(expression: str) -> None:
    with pytest.raises(NotArithmetic):
        safe_calculate(expression)


@pytest.mark.parametrize("expression", ["1 / 0", "1e308 * 10", "1" + "0" * 400])
def test_calculate_refuses_a_result_that_is_no_finite_number(expression: str) -> None:
    with pytest.raises(NotArithmetic):
        safe_calculate(expression)


def test_calculate_refuses_a_long_expression() -> None:
    with pytest.raises(NotArithmetic, match="characters"):
        safe_calculate("1+" * 300 + "1")


# Measured: the parser refuses 249 nested parentheses, and 499 nested signs stay within the
# recursion limit, so no expression short enough to be read can crash the evaluation.
def test_an_expression_nested_deep_is_refused_or_computed_never_a_crash() -> None:
    with pytest.raises(NotArithmetic, match="nested"):
        safe_calculate("(" * 249 + "1" + ")" * 249)
    assert safe_calculate("-" * 499 + "1") == -1.0


# The items a run requires


def first_run(wiki: Wiki = SHOP_WIKI, inventory: Inventory = SHOP_INVENTORY) -> RequiredItems:
    return required_items(JudgeScope(inventory=inventory, wiki=wiki, changed_ids=None))


def re_run(*changed_ids: str, wiki: Wiki = SHOP_WIKI) -> RequiredItems:
    return required_items(
        JudgeScope(inventory=SHOP_INVENTORY, wiki=wiki, changed_ids=frozenset(changed_ids))
    )


def with_facts(*facts: Fact, wiki: Wiki = SHOP_WIKI) -> Wiki:
    return wiki.model_copy(update={"facts": (*wiki.facts, *facts)})


def test_a_first_run_requires_every_route_param_and_error_code() -> None:
    required = first_run()

    assert required.routes == ("GET /", QUOTE_ROUTE)
    assert required.params == (
        RequiredParam(route=QUOTE_ROUTE, name="pieces", location="query"),
        RequiredParam(route=QUOTE_ROUTE, name="sku", location="path"),
    )
    assert required.error_codes == ("bad_pieces", "unknown_sku")
    assert (required.requests, required.exemptions) == ((), ())


INTERNAL = ErrorCode(code="internal", status="http.StatusInternalServerError")
WITH_INTERNAL = SHOP_INVENTORY.model_copy(
    update={"error_codes": (*SHOP_INVENTORY.error_codes, INTERNAL)}
)
INTERNAL_UNREACHABLE = an_unreachable(
    id="unreachable.internal",
    error_code="internal",
    reason="The lookup never fails.",
)


def test_unreachable_error_code_not_required() -> None:
    required = first_run(with_facts(INTERNAL_UNREACHABLE), inventory=WITH_INTERNAL)

    assert required.error_codes == ("bad_pieces", "unknown_sku")
    assert required.exemptions == (
        "error code internal: no request reaches it, as fact unreachable.internal says: The "
        "lookup never fails.",
    )


def test_an_error_code_no_endpoint_lists_and_no_fact_exempts_is_required() -> None:
    assert "internal" in first_run(inventory=WITH_INTERNAL).error_codes


ORDERS = Route(method="POST", path="/orders", handler="s.order", file="web/server.go", line=25)
ORDER_FACT = an_endpoint(
    id="endpoint.order",
    evidence=(quote('refuse(w, "unknown_sku", http.StatusNotFound)'),),
    links=Links(routes=("POST /orders",)),
    route="POST /orders",
    params=(a_param(name="note", location="form"),),
    responses=(),
)
WITH_ORDERS = SHOP_INVENTORY.model_copy(update={"routes": (*SHOP_INVENTORY.routes, ORDERS)})


def test_a_route_the_judge_cannot_send_is_exempt_with_its_params() -> None:
    required = first_run(with_facts(ORDER_FACT), inventory=WITH_ORDERS)

    assert required.routes == ("GET /", QUOTE_ROUTE)
    assert [param.name for param in required.params] == ["pieces", "sku"]
    assert required.exemptions == (
        "param note (form) of POST /orders: the judge sends GET and HEAD alone",
        "route POST /orders: the judge sends GET and HEAD alone",
    )


def test_an_error_code_only_a_route_the_judge_cannot_send_returns_is_exempt() -> None:
    bad_order = a_response(status=400, declared_type="web.problem", error_codes=("bad_order",))
    refusing = ORDER_FACT.model_copy(update={"responses": (bad_order,)})
    inventory = WITH_ORDERS.model_copy(
        update={
            "error_codes": (*WITH_ORDERS.error_codes, ErrorCode(code="bad_order", status="400"))
        }
    )

    required = first_run(with_facts(refusing), inventory=inventory)

    assert required.error_codes == ("bad_pieces", "unknown_sku")
    assert (
        "error code bad_order: only POST /orders returns it, and the judge sends GET and HEAD alone"
        in required.exemptions
    )


def test_an_any_route_is_required_and_its_get_fact_gives_its_params() -> None:
    anything = Route(method="ANY", path="/echo", handler="s.echo", file="web/server.go", line=26)
    echo = an_endpoint(
        id="endpoint.echo",
        links=Links(routes=("ANY /echo",)),
        route="GET /echo",
        params=(a_param(name="pieces"),),
        responses=(),
    )
    inventory = SHOP_INVENTORY.model_copy(update={"routes": (*SHOP_INVENTORY.routes, anything)})

    required = first_run(with_facts(echo), inventory=inventory)

    assert "ANY /echo" in required.routes
    assert RequiredParam(route="GET /echo", name="pieces", location="query") in required.params


def test_a_header_param_is_exempt() -> None:
    quote_endpoint = SHOP_FACTS[0]
    with_header = quote_endpoint.model_copy(
        update={"params": (*quote_endpoint.params, a_param(name="pieces", location="header"))}
    )
    wiki = SHOP_WIKI.model_copy(update={"facts": (with_header, *SHOP_FACTS[1:])})

    required = first_run(wiki)

    assert RequiredParam(route=QUOTE_ROUTE, name="pieces", location="query") in required.params
    assert required.exemptions == (
        "param pieces (header) of GET /items/{sku}: the judge's requests carry no headers",
    )


def test_rerun_requires_only_changed_items() -> None:
    listing_changed = re_run("endpoint.listing")
    quote_changed = re_run("endpoint.quote")

    assert listing_changed == RequiredItems(routes=("GET /",))
    assert quote_changed.routes == (QUOTE_ROUTE,)
    assert [param.name for param in quote_changed.params] == ["pieces", "sku"]
    assert quote_changed.error_codes == ("bad_pieces", "unknown_sku")


def test_a_changed_fact_requires_each_route_it_links() -> None:
    field = a_field_fact(id="field.listed", links=Links(routes=("GET /",)))

    assert re_run(field.id, wiki=with_facts(field)) == RequiredItems(routes=("GET /",))


def test_a_changed_rule_that_links_no_route_requires_nothing() -> None:
    rule = a_rule()

    assert re_run(rule.id, wiki=with_facts(rule)).is_empty()


def test_nothing_changed_requires_nothing() -> None:
    assert re_run().is_empty()


def test_a_changed_unreachable_fact_is_listed_as_an_exemption() -> None:
    wiki = with_facts(INTERNAL_UNREACHABLE)

    required = required_items(
        JudgeScope(
            inventory=WITH_INTERNAL, wiki=wiki, changed_ids=frozenset({INTERNAL_UNREACHABLE.id})
        )
    )

    assert required.error_codes == ()
    assert [line.split(":")[0] for line in required.exemptions] == ["error code internal"]


def test_the_requests_an_earlier_judge_got_wrong_are_required_again() -> None:
    again = HttpRequest(method="GET", path_and_query="/items/tea-100?pieces=3")

    required = required_items(
        JudgeScope(
            inventory=SHOP_INVENTORY, wiki=SHOP_WIKI, changed_ids=frozenset(), again=(again,)
        )
    )

    assert required == RequiredItems(requests=(again,))
    assert not required.is_empty()


# What a reply tried


def test_untried_lists_each_required_item_no_reply_tried() -> None:
    assert untried(first_run(), [TEA]) == [
        "route GET /",
        "param pieces (query) of GET /items/{sku}",
        "error code bad_pieces",
        "error code unknown_sku",
    ]


def test_every_item_is_tried_by_the_shops_four_probes() -> None:
    assert untried(first_run(), [TEA, NOTHING, BAD_PIECES, LISTING]) == []


def test_a_query_param_is_tried_only_by_a_request_to_its_route_that_sets_it() -> None:
    required = RequiredItems(
        params=(RequiredParam(route=QUOTE_ROUTE, name="pieces", location="query"),)
    )
    label = ["param pieces (query) of GET /items/{sku}"]

    assert untried(required, [actual({}, path_and_query="/items/tea-100?pieces=2")]) == []
    assert untried(required, [actual({}, path_and_query="/items/tea-100?pieces")]) == []
    assert untried(required, [actual({}, path_and_query="/items/tea-100?piecesx=2")]) == label
    assert untried(required, [actual({}, path_and_query="/?pieces=2")]) == label


def test_an_error_code_is_tried_by_an_error_reply_that_holds_it() -> None:
    required = RequiredItems(error_codes=("unknown_sku",))

    assert untried(required, [NOTHING]) == []
    assert untried(required, [actual({"code": "unknown_sku"}, status=200)]) != []
    assert untried(required, [actual({"code": "unknown_skus"}, status=404)]) != []


def test_a_route_is_tried_by_its_method_and_a_path_it_matches() -> None:
    required = RequiredItems(routes=(QUOTE_ROUTE, "ANY /echo"))
    head = TEA.model_copy(update={"method": "HEAD"})
    empty_segment = actual({}, path_and_query="/items/")
    echo = TEA.model_copy(update={"method": "HEAD", "path_and_query": "/echo?x=1"})

    assert untried(required, [head, empty_segment, echo]) == ["route GET /items/{sku}"]


def test_a_request_is_tried_only_as_written() -> None:
    again = HttpRequest(method="GET", path_and_query="/items/tea-100?pieces=3")
    required = RequiredItems(requests=(again,))

    assert untried(required, [actual({}, path_and_query="/items/tea-100?pieces=3")]) == []
    assert untried(required, [actual({}, path_and_query="/items/tea-100?pieces=03")]) == [
        "the request GET /items/tea-100?pieces=3"
    ]


def test_a_reply_tries_a_required_item_or_none() -> None:
    required = first_run()

    assert tries_required(required, TEA)
    assert not tries_required(required, actual({}, status=404, path_and_query="/nothing/here"))
    assert not tries_required(RequiredItems(), TEA)

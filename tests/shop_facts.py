"""Grounded facts about the shop fixture, and the probes they cite, as the explore agent would
submit them. covering_facts covers every item of an inventory of the shop or of its hostile copy."""

from typing import Any

from arde.domain.facts import (
    CodeQuote,
    EndpointFact,
    Fact,
    FieldFact,
    Links,
    ParamFact,
    ProbeCitation,
    ProbeRecord,
    ResponseFact,
    RuleFact,
    UnreachableFact,
)
from arde.domain.inventory import Inventory

NOT_FOUND_CODE = 'refuse(w, "unknown_sku", http.StatusNotFound)'
QUOTE_HANDLER = CodeQuote(kind="code", file="web/handlers.go", quote=NOT_FOUND_CODE)
TEA = ProbeRecord(
    kind="probe",
    method="GET",
    path_and_query="/items/tea-100",
    status=200,
    content_type="application/json",
    body='{"issued_by":"shop","sku":"tea-100","pieces":1,"charge_cents":450}\n',
)
NOTHING = ProbeRecord(
    kind="probe",
    method="GET",
    path_and_query="/items/nothing",
    status=404,
    content_type="application/json",
    body='{"code":"unknown_sku"}\n',
)
BAD_PIECES = ProbeRecord(
    kind="probe",
    method="GET",
    path_and_query="/items/tea-100?pieces=0",
    status=400,
    content_type="application/json",
    body='{"code":"bad_pieces"}\n',
)
LISTING = ProbeRecord(
    kind="probe",
    method="GET",
    path_and_query="/",
    status=200,
    content_type="application/json",
    body='{"items":[]}\n',
)


def cite(record: ProbeRecord) -> ProbeCitation:
    return ProbeCitation(kind="probe", method=record.method, path_and_query=record.path_and_query)


def quote(text: str, *, file: str = "web/handlers.go") -> CodeQuote:
    return CodeQuote(kind="code", file=file, quote=text)


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
        "status": 200,
        "declared_type": "web.Quote",
        "description": "The price of the pieces",
        "error_codes": (),
    }
    return ResponseFact(**{**fields, **overrides})


NOT_FOUND = a_response(
    status=404, declared_type="web.problem", description="No item", error_codes=("unknown_sku",)
)


def an_endpoint(**overrides: Any) -> EndpointFact:
    fields: dict[str, Any] = {
        "id": "endpoint.quote",
        "topic": "api",
        "kind": "endpoint",
        "evidence": (QUOTE_HANDLER, cite(TEA), cite(NOTHING)),
        "links": Links(
            routes=("GET /items/{sku}",), declarations=("web/handlers.go::func server.quote",)
        ),
        "route": "GET /items/{sku}",
        "summary": "Price some pieces of one item",
        "params": (SKU, a_param()),
        "responses": (a_response(), NOT_FOUND),
    }
    return EndpointFact(**{**fields, **overrides})


def the_listing(**overrides: Any) -> EndpointFact:
    fields: dict[str, Any] = {
        "id": "endpoint.listing",
        "evidence": (cite(LISTING),),
        "links": Links(routes=("GET /",)),
        "route": "GET /",
        "summary": "List every item",
        "params": (),
        "responses": (a_response(declared_type="web.Listing", description="The items"),),
    }
    return an_endpoint(**{**fields, **overrides})


def a_field_fact(**overrides: Any) -> FieldFact:
    fields: dict[str, Any] = {
        "id": "field.quote.pieces",
        "topic": "api",
        "kind": "field",
        "evidence": (quote('Pieces      int    `json:"pieces"`', file="web/payloads.go"),),
        "links": Links(declarations=("web/payloads.go::type Quote",)),
        "struct": "Quote",
        "field": "Pieces",
        "description": "How many pieces were priced",
    }
    return FieldFact(**{**fields, **overrides})


def a_rule(**overrides: Any) -> RuleFact:
    fields: dict[str, Any] = {
        "id": "rule.no-discount",
        "topic": "business-rules",
        "kind": "rule",
        "evidence": (quote("return unitCents * int64(pieces)", file="pricing/pricing.go"),),
        "links": Links(declarations=("pricing/pricing.go::func Charge",)),
        "statement": "Ten pieces cost ten times one piece: there is no volume discount.",
    }
    return RuleFact(**{**fields, **overrides})


def an_unreachable(**overrides: Any) -> UnreachableFact:
    fields: dict[str, Any] = {
        "id": "unreachable.bad-pieces",
        "topic": "errors",
        "kind": "unreachable",
        "evidence": (quote('refuse(w, "bad_pieces", http.StatusBadRequest)'),),
        "links": Links(declarations=("web/handlers.go::func server.quote",)),
        "error_code": "bad_pieces",
        "reason": "Stands in for a reason the code gives.",
    }
    return UnreachableFact(**{**fields, **overrides})


BAD_REQUEST = a_response(status=400, declared_type="web.problem", error_codes=("bad_pieces",))


def field_facts_of(inventory: Inventory) -> tuple[FieldFact, ...]:
    return tuple(
        a_field_fact(
            id=f"field.{reply.struct}.{reply.name}",
            evidence=(quote(f"{reply.name} {reply.declared_type}", file=reply.file),),
            links=Links(declarations=(f"{reply.file}::type {reply.struct}",)),
            struct=reply.struct,
            field=reply.name,
            description=f"The {reply.json_name} of the reply",
        )
        for reply in inventory.response_fields
    )


def covering_facts(inventory: Inventory) -> tuple[Fact, ...]:
    quote_endpoint = an_endpoint(
        evidence=(QUOTE_HANDLER, cite(TEA), cite(NOTHING), cite(BAD_PIECES)),
        responses=(a_response(), NOT_FOUND, BAD_REQUEST),
    )
    return (quote_endpoint, the_listing(), *field_facts_of(inventory))

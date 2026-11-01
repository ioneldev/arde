"""Domain models: frozen, closed, and single-line wherever free text can reach generated code."""

import importlib
import json
import pkgutil
from collections.abc import Callable
from typing import Any

import pytest
import yaml
from pydantic import BaseModel, ValidationError

import arde.domain
from arde.domain.base import strict_tool_schema
from arde.domain.change import ChangeReport
from arde.domain.facts import (
    CodeQuote,
    EndpointFact,
    FieldFact,
    Links,
    ParamFact,
    PolymorphicFact,
    ProbeRecord,
    ResponseFact,
    RuleFact,
    UnreachableFact,
    Wiki,
)
from arde.domain.findings import Finding
from arde.domain.profile import ExtractionPattern, RepoProfile
from arde.domain.report import ExitCode, RunReport
from arde.domain.usage import RoleTotals

CODE_QUOTE = CodeQuote(kind="code", file="catalog/handler.go", quote="http.Error(w, msg, 400)")
PROBE = ProbeRecord(
    kind="probe",
    method="GET",
    path_and_query="/products/1?qty=2",
    status=200,
    content_type="application/json",
    body='{"id": 1}',
)


def a_param(**overrides: Any) -> ParamFact:
    fields: dict[str, Any] = {
        "name": "qty",
        "location": "query",
        "go_type": "int",
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
        "status": 400,
        "go_type": "ErrorBody",
        "description": "The request was invalid",
        "error_codes": ("bad_qty",),
    }
    return ResponseFact(**{**fields, **overrides})


def fact_fields(kind: str, **overrides: Any) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "id": f"{kind}:1",
        "topic": "api",
        "evidence": (CODE_QUOTE, PROBE),
        "links": Links(declarations=("catalog.Price",), routes=("GET /products/{id}",)),
        "kind": kind,
    }
    return {**fields, **overrides}


def an_endpoint(**overrides: Any) -> EndpointFact:
    fields = fact_fields(
        "endpoint",
        route="GET /products/{id}",
        summary="Price one product",
        params=(a_param(),),
        responses=(a_response(),),
    )
    return EndpointFact(**{**fields, **overrides})


def a_field(**overrides: Any) -> FieldFact:
    fields = fact_fields("field", struct="Product", field="name", description="Display name")
    return FieldFact(**{**fields, **overrides})


def a_polymorphic(**overrides: Any) -> PolymorphicFact:
    fields = fact_fields(
        "polymorphic", struct="Product", field="Specs", variants=("LaptopSpecs", "BookSpecs")
    )
    return PolymorphicFact(**{**fields, **overrides})


def a_rule() -> RuleFact:
    fields = fact_fields("rule", topic="business-rules", statement="Totals are computed in bani")
    return RuleFact(**fields)


def an_unreachable() -> UnreachableFact:
    fields = fact_fields(
        "unreachable", topic="errors", error_code="db_down", reason="Nothing returns it"
    )
    return UnreachableFact(**fields)


def a_wiki() -> Wiki:
    return Wiki(
        facts=(an_endpoint(), a_field(), a_polymorphic(), a_rule(), an_unreachable()),
    )


def a_run_report(**overrides: Any) -> RunReport:
    fields: dict[str, Any] = {
        "status": "failed",
        "exit_code": ExitCode.CHECKS_FAILED,
        "message": "2 checks failed",
        "next_action": "Read the findings in report.json",
        "findings": (
            Finding(
                check="spec_valid",
                owner="code",
                subject="openapi.yaml",
                message="invalid",
                next_action="Fix the annotation",
            ),
        ),
        "warnings": ("slow probe",),
        "exemptions": (),
        "change_labels": ("behaviour",),
        "cost_usd": 1.25,
        "duration_s": 42.5,
        "per_role": {
            "explore": RoleTotals(
                calls=3,
                input_tokens=900,
                output_tokens=300,
                cache_read=100,
                cache_write=50,
                cost_usd=1.25,
                latency_s=12.5,
            )
        },
        "changed_paths": ("handlers.go",),
        "run_dir": "runs/20261101-1",
    }
    return RunReport(**{**fields, **overrides})


def test_models_are_frozen() -> None:
    finding = Finding(check="c", owner="docs", subject="s", message="m", next_action="n")

    with pytest.raises(ValidationError):
        finding.message = "changed"


def test_unknown_fields_rejected() -> None:
    with pytest.raises(ValidationError):
        Finding(  # type: ignore[call-arg]
            check="c", owner="docs", subject="s", message="m", next_action="n", severity="high"
        )


def test_unknown_fields_are_rejected_inside_nested_facts() -> None:
    stored = a_wiki().model_dump(mode="json")
    stored["facts"][0]["params"][0]["unexpected"] = True

    with pytest.raises(ValidationError):
        Wiki.model_validate(stored)


def test_every_domain_model_is_frozen_and_closed() -> None:
    for model in domain_models():
        assert model.model_config.get("frozen") is True, model.__name__
        assert model.model_config.get("extra") == "forbid", model.__name__


@pytest.mark.parametrize("line_break", ["\n", "\r", "\r\n"])
@pytest.mark.parametrize(
    "build",
    [
        lambda text: a_param(description=text),
        lambda text: a_param(enum=("ok", text)),
        lambda text: a_param(default=text),
        lambda text: a_response(description=text),
        lambda text: an_endpoint(summary=text),
        lambda text: a_field(description=text),
    ],
    ids=[
        "param description",
        "param enum value",
        "param default",
        "response description",
        "endpoint summary",
        "field description",
    ],
)
def test_description_must_be_single_line(build: Callable[[str], object], line_break: str) -> None:
    with pytest.raises(ValidationError, match="single line"):
        build(f"ok{line_break}//go:debug x=1")


# Every field the renderer writes into a comment or a struct tag. Each builder takes the whole
# value of that field and fills the rest of the fact with valid data.
RENDERED_FIELDS: dict[str, Callable[[str], object]] = {
    "param name": lambda value: a_param(name=value),
    "response go_type": lambda value: a_response(go_type=value),
    "response error code": lambda value: a_response(error_codes=("bad_qty", value)),
    "endpoint route": lambda value: an_endpoint(route=value),
    "field struct": lambda value: a_field(struct=value),
    "field field": lambda value: a_field(field=value),
    "polymorphic struct": lambda value: a_polymorphic(struct=value),
    "polymorphic field": lambda value: a_polymorphic(field=value),
    "polymorphic variant": lambda value: a_polymorphic(variants=("LaptopSpecs", value)),
}

# A route needs its method and slash around the suspect text, or it fails for the wrong reason.
INJECTION_TARGETS: dict[str, Callable[[str], object]] = {
    **RENDERED_FIELDS,
    "endpoint route": lambda text: an_endpoint(route=f"GET /{text}"),
}

INJECTIONS = {
    "line feed": "ok\n//go:debug x=1",
    "carriage return": "ok\r//go:debug x=1",
    "trailing line feed": "ok\n",
    "double quote": 'ok"x',
    "single quote": "ok'x",
    "backtick": "ok`x",
}


@pytest.mark.parametrize("injection", INJECTIONS.values(), ids=INJECTIONS.keys())
@pytest.mark.parametrize("build", INJECTION_TARGETS.values(), ids=INJECTION_TARGETS.keys())
def test_names_that_reach_source_reject_line_breaks_quotes_and_backticks(
    build: Callable[[str], object], injection: str
) -> None:
    with pytest.raises(ValidationError, match="must be"):
        build(injection)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("param name", "qty"),
        ("param name", "page-size"),
        ("response go_type", "ProductResponse"),
        ("response go_type", "catalog.LaptopSpecs"),
        ("response go_type", "[]ProductSummary"),
        ("response go_type", "[]*ProductSummary"),
        ("response error code", "invalid_qty"),
        ("response error code", "rate-limit.exceeded"),
        ("endpoint route", "GET /{id}"),
        ("endpoint route", "GET /"),
        ("endpoint route", "POST /products/{id}/price"),
        ("field struct", "Product"),
        ("field struct", "_internal2"),
        ("field field", "name"),
        ("polymorphic struct", "Product"),
        ("polymorphic field", "Specs"),
        ("polymorphic variant", "catalog.LaptopSpecs"),
    ],
)
def test_real_names_are_accepted(field: str, value: str) -> None:
    RENDERED_FIELDS[field](value)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("field struct", ""),
        ("field struct", "1Product"),
        ("field struct", "catalog.Product"),
        ("field field", "first name"),
        ("polymorphic field", "a-b"),
        ("response go_type", ""),
        ("response go_type", "map[string]int"),
        ("response go_type", "catalog."),
        ("response go_type", "[]"),
        ("response go_type", "a b"),
        ("response go_type", "a,b"),
        ("polymorphic variant", "a,b"),
        ("polymorphic variant", "a b"),
        ("response error code", ""),
        ("response error code", "bad code"),
        ("response error code", "a/b"),
        ("endpoint route", ""),
        ("endpoint route", "GET"),
        ("endpoint route", "GET x"),
        ("endpoint route", "/x"),
        ("endpoint route", "get /x"),
        ("endpoint route", "GET  /x"),
        ("endpoint route", "GET /a b"),
        ("param name", ""),
        ("param name", "a b"),
    ],
)
def test_names_with_the_wrong_shape_are_rejected(field: str, value: str) -> None:
    with pytest.raises(ValidationError, match="must be"):
        RENDERED_FIELDS[field](value)


@pytest.mark.parametrize(
    ("field", "value", "instruction"),
    [
        ("field struct", "1Product", "letters, digits and underscore"),
        ("response go_type", "map[string]int", "identifiers joined by dots"),
        ("response error code", "bad code", "letters, digits, underscore, hyphen and dot"),
        ("endpoint route", "get /x", "upper-case method"),
        ("param name", "a b", "no whitespace, quotes or backticks"),
    ],
)
def test_a_rejected_name_says_what_to_use_instead(field: str, value: str, instruction: str) -> None:
    with pytest.raises(ValidationError, match=instruction):
        RENDERED_FIELDS[field](value)


def test_a_stored_wiki_edited_to_hold_a_line_break_is_rejected_on_load() -> None:
    stored = a_wiki().model_dump(mode="json")
    stored["facts"][1]["description"] = "Display name\n//go:debug httpmuxgo121=1"

    with pytest.raises(ValidationError, match="single line"):
        Wiki.model_validate(stored)


def test_code_quotes_may_span_lines() -> None:
    quote = CodeQuote(kind="code", file="a.go", quote="if x {\n\treturn\n}")

    assert quote.quote.count("\n") == 2


def test_fact_union_round_trips_through_yaml() -> None:
    wiki = a_wiki()

    stored = yaml.safe_dump(wiki.model_dump(mode="json"), sort_keys=False)

    assert Wiki.model_validate(yaml.safe_load(stored)) == wiki


def test_an_unknown_fact_kind_is_rejected() -> None:
    stored = a_wiki().model_dump(mode="json")
    stored["facts"][3]["kind"] = "opinion"

    with pytest.raises(ValidationError):
        Wiki.model_validate(stored)


def test_wiki_returns_facts_by_topic() -> None:
    wiki = a_wiki()

    assert wiki.by_topic("api") == (wiki.facts[0], wiki.facts[1], wiki.facts[2])
    assert wiki.by_topic("errors") == (wiki.facts[4],)
    assert wiki.by_topic("overview") == ()


def test_wiki_returns_a_fact_by_id() -> None:
    wiki = a_wiki()

    assert wiki.by_id("rule:1") == wiki.facts[3]


def test_wiki_names_the_missing_id() -> None:
    with pytest.raises(KeyError, match="rule:404"):
        a_wiki().by_id("rule:404")


def test_wiki_rejects_two_facts_with_one_id() -> None:
    with pytest.raises(ValidationError, match="field:1"):
        Wiki(facts=(a_field(), a_field(description="Another description")))


def test_exit_codes_match_the_documented_numbers() -> None:
    assert {code.name: code.value for code in ExitCode} == {
        "OK": 0,
        "CHECKS_FAILED": 1,
        "BAD_USAGE": 2,
        "DISCOVERY_NEEDS_INPUT": 3,
        "BUDGET_EXCEEDED": 4,
    }


def test_run_report_round_trips_through_json() -> None:
    report = a_run_report()

    saved = report.model_dump_json()

    assert RunReport.model_validate_json(saved) == report
    assert json.loads(saved)["exit_code"] == 1


def test_change_report_holds_what_changed_between_two_trees() -> None:
    report = ChangeReport(
        changed_files=("handlers.go",),
        changed_declarations=("handlers.go:Price",),
        added_inventory=("route GET /rates",),
        removed_inventory=(),
        changed_routes=("GET /products/{id}",),
        changed_probes=("GET /products/1",),
        responses_changed=True,
        diff="--- a\n+++ b\n",
    )

    assert report.responses_changed is True


def test_repo_profile_defaults_to_no_test_commands() -> None:
    profile = RepoProfile(
        main_package="./cmd/server",
        main_file="cmd/server/main.go",
        port_env="PORT",
        patterns=(
            ExtractionPattern(
                kind="route", call="^mux\\.HandleFunc$", args={"path": 0, "handler": 1}
            ),
        ),
    )

    assert profile.reported_test_commands == ()
    assert profile.extra_test_commands == ()


def test_strict_tool_schema_marks_nested_objects_closed() -> None:
    assert strict_tool_schema(RepoProfile)["additionalProperties"] is False


def test_strict_tool_schema_closes_objects_that_were_open() -> None:
    class Inner(BaseModel):
        value: int

    class Outer(BaseModel):
        inner: Inner
        others: list[Inner] = []

    schema = strict_tool_schema(Outer)

    assert schema["additionalProperties"] is False
    assert schema["$defs"]["Inner"]["additionalProperties"] is False
    assert schema["$defs"]["Inner"]["required"] == ["value"]


def test_strict_tool_schema_requires_every_property_even_those_with_defaults() -> None:
    schema = strict_tool_schema(RepoProfile)

    assert set(schema["required"]) == set(RepoProfile.model_fields)


def test_strict_tool_schema_treats_a_field_named_properties_as_a_field() -> None:
    class Odd(BaseModel):
        properties: list[str]

    assert strict_tool_schema(Odd)["required"] == ["properties"]


def test_strict_tool_schema_leaves_the_model_schema_untouched() -> None:
    class Open(BaseModel):
        count: int = 0

    strict_tool_schema(Open)

    assert "additionalProperties" not in Open.model_json_schema()


def domain_models() -> list[type[BaseModel]]:
    modules = [
        importlib.import_module(f"arde.domain.{module.name}")
        for module in pkgutil.iter_modules(arde.domain.__path__)
    ]
    return [
        member
        for module in modules
        for member in vars(module).values()
        if isinstance(member, type)
        and issubclass(member, BaseModel)
        and member.__module__ == module.__name__
    ]

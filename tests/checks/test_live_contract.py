"""The running service against the spec: enum values, bounds and their neighbours, defaults, and
the shape of every reply against a closed schema.

A stand-in service answers in place of a real one, so these tests need no network and no Go. The
tests marked integration run the annotated fixture service.
"""

import json
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlsplit

import pytest
from annotated_support import annotated_profile, annotated_spec
from checks_support import AFTER, a_context, a_spec, a_spec_param, a_wiki, an_operation

from arde.adapters.http_tool import LocalServiceClient
from arde.adapters.language.go.toolchain import built_service
from arde.adapters.service_runner import LocalServiceRunner
from arde.application.checks_runner import ServicePair
from arde.application.ports import HttpRequest, ServiceConfig, ServiceHandle
from arde.checks.live_contract import BoundaryCase, LiveContract, boundary_requests
from arde.checks.spec_operations import Operation, SpecParam
from arde.domain.facts import ProbeRecord
from arde.domain.findings import Finding

COUNT = SpecParam(name="count", location="query", type="integer", minimum=1, maximum=10)
UNIT = SpecParam(name="unit", location="query", type="string", enum=("m", "ft"))
ID = SpecParam(name="id", location="path", required=True, type="integer", minimum=1)
COUNT_OP = Operation(method="GET", path="/things/{id}", params=(COUNT,))
UNIT_OP = Operation(method="GET", path="/things/{id}", params=(UNIT,))


def values(cases: list[BoundaryCase], *, accepted: bool) -> set[str]:
    return {case.value for case in cases if case.accepted is accepted}


# boundary_requests


def test_each_enum_value_requested() -> None:
    assert values(boundary_requests(UNIT_OP), accepted=True) == {"m", "ft"}


def test_non_enum_value_expects_error() -> None:
    [unlisted] = [case for case in boundary_requests(UNIT_OP) if not case.accepted]

    assert unlisted.value not in {"m", "ft"}


# A value in another case, or with spaces, is still a listed one to a service that reads values
# loosely, and the spec's enum lists the documented values only.
def test_the_value_outside_the_enum_is_no_spelling_of_one_in_it() -> None:
    loose = SpecParam(name="unit", location="query", enum=("unlisted", "UNLISTED-X "))

    cases = boundary_requests(Operation(method="GET", path="/things", params=(loose,)))

    [unlisted] = [case for case in cases if not case.accepted]

    assert unlisted.value.strip().casefold() not in {"unlisted", "unlisted-x"}


def test_a_whole_number_outside_an_enum_of_whole_numbers_is_one_past_the_largest() -> None:
    sizes = SpecParam(name="size", location="query", type="integer", enum=("1", "5"))

    cases = boundary_requests(Operation(method="GET", path="/things", params=(sizes,)))

    assert values(cases, accepted=False) == {"6"}


def test_min_max_and_just_outside_requested() -> None:
    cases = boundary_requests(COUNT_OP)

    assert {c.value for c in cases} >= {"0", "1", "10", "11"}
    assert values(cases, accepted=True) == {"1", "10"}
    assert values(cases, accepted=False) == {"0", "11"}


# A path param names a thing, so a value inside its bounds may name nothing.
def test_a_path_param_is_sent_only_outside_its_bounds() -> None:
    bounded = ID.model_copy(update={"maximum": 99})

    cases = boundary_requests(Operation(method="GET", path="/things/{id}", params=(bounded,)))

    assert values(cases, accepted=True) == set()
    assert values(cases, accepted=False) == {"0", "100"}


def test_a_param_a_request_cannot_carry_gets_no_case() -> None:
    header = SpecParam(name="X-Unit", location="header", enum=("m", "ft"), minimum=1)

    assert boundary_requests(Operation(method="GET", path="/things", params=(header,))) == []


def test_each_case_says_what_the_spec_claims() -> None:
    claims = {case.value: case.claim for case in boundary_requests(COUNT_OP)}

    assert claims["11"] == "count has the maximum 10"
    assert claims["0"] == "count has the minimum 1"


# LiveContract against a stand-in service


class StandInService:
    """Answers GET /things/{id}?count=&unit= as an honest service would, unless told otherwise."""

    def __init__(self, *, max_count: int = 10, default_count: int = 1) -> None:
        self.max_count = max_count
        self.default_count = default_count
        self.extra: dict[str, object] = {}
        self.missing: set[str] = set()
        # The paths under which the service answers in plain text, not JSON.
        self.plain_text_under = ""
        # A text in the request that makes the service fail with a 500.
        self.server_error_on = ""
        self.sent: list[HttpRequest] = []

    def send_request(self, service: ServiceHandle, request: HttpRequest) -> ProbeRecord:
        assert service == AFTER
        self.sent.append(request)
        status, reply = self.answer(request.path_and_query)
        body = json.dumps(reply)
        if self.plain_text_under and request.path_and_query.startswith(self.plain_text_under):
            body = "no such thing"
        return ProbeRecord(
            kind="probe",
            method=request.method,
            path_and_query=request.path_and_query,
            status=status,
            content_type="application/json",
            body="" if request.method == "HEAD" else body,
        )

    def answer(self, path_and_query: str) -> tuple[int, dict[str, object]]:
        if self.server_error_on and self.server_error_on in path_and_query:
            return 500, {}
        parts = urlsplit(path_and_query)
        query = dict(parse_qsl(parts.query))
        segments = parts.path.split("/")
        if len(segments) != 3 or segments[1] != "things" or segments[2] not in {"1", "2"}:
            return 404, {"code": "no_thing", "message": "no such thing"}
        count = query.get("count", str(self.default_count))
        if not count.isdigit() or not 1 <= int(count) <= self.max_count:
            return 400, {"code": "bad_count", "message": "count is 1 to 10"}
        unit = query.get("unit", "m")
        if unit.strip().lower() not in {"m", "ft"}:
            return 400, {"code": "bad_unit", "message": "unit is m or ft"}
        thing = {"id": int(segments[2]), "count": int(count), "unit": unit, **self.extra}
        return 200, {key: value for key, value in thing.items() if key not in self.missing}

    def paths_sent(self) -> list[str]:
        return [request.path_and_query for request in self.sent]


PROBLEM = {
    "type": "object",
    "properties": {"code": {"type": "string"}, "message": {"type": "string"}},
    "required": ["code", "message"],
}
THING = {
    "type": "object",
    "properties": {
        "id": {"type": "integer"},
        "count": {"type": "integer"},
        "unit": {"type": "string"},
    },
    "required": ["id", "count", "unit"],
}


def json_reply(name: str) -> dict[str, Any]:
    return {
        "description": name,
        "content": {"application/json": {"schema": {"$ref": f"#/components/schemas/web.{name}"}}},
    }


REPLIES = {"200": json_reply("Thing"), "400": json_reply("Problem"), "404": json_reply("Problem")}


def things_spec(
    *, count: dict[str, Any] | None = None, replies: dict[str, Any] | None = None
) -> Any:
    params = [
        a_spec_param("id", "path", minimum=1),
        a_spec_param("count", **(count or {"minimum": 1, "maximum": 10, "default": 1})),
        {
            "name": "unit",
            "in": "query",
            "description": "The unit",
            "schema": {"type": "string", "enum": ["m", "ft"], "default": "m"},
        },
    ]
    operation = an_operation(params=params, responses=replies or REPLIES)
    return a_spec(
        {"/things/{id}": {"get": operation}}, {"web.Thing": THING, "web.Problem": PROBLEM}
    )


def a_probe(path_and_query: str, *, status: int = 200, method: str = "GET") -> ProbeRecord:
    return ProbeRecord(
        kind="probe",
        method=method,
        path_and_query=path_and_query,
        status=status,
        content_type="application/json",
        body="{}",
    )


PROBES = (a_probe("/things/7", status=404), a_probe("/things/1?count=2"))


def run_against(
    service: StandInService,
    tmp_path: Path,
    *,
    spec: Any = None,
    probes: tuple[ProbeRecord, ...] = PROBES,
) -> list[Finding]:
    context = a_context(tmp_path, spec=spec or things_spec(), wiki=a_wiki(probes=probes))
    return LiveContract(service).run(context)


def subjects(found: list[Finding]) -> list[str]:
    return [finding.subject for finding in found]


def test_an_honest_spec_gives_no_findings(tmp_path: Path) -> None:
    assert run_against(StandInService(), tmp_path) == []


def test_every_case_is_sent_from_the_probe_that_succeeded(tmp_path: Path) -> None:
    service = StandInService()

    run_against(service, tmp_path)

    assert set(service.paths_sent()) == {
        "/things/1?count=2",
        "/things/0?count=2",
        "/things/1?count=0",
        "/things/1?count=1",
        "/things/1?count=10",
        "/things/1?count=11",
        "/things/1?count=2&unit=m",
        "/things/1?count=2&unit=ft",
        "/things/1?count=2&unit=unlisted",
        "/things/1",
    }
    assert {request.method for request in service.sent} == {"GET"}


def test_wrong_max_in_spec_flagged(tmp_path: Path) -> None:
    spec = things_spec(count={"minimum": 1, "maximum": 20, "default": 1})

    [finding] = run_against(StandInService(max_count=10), tmp_path, spec=spec)

    assert finding.subject == "GET /things/{id} query param count"
    assert finding.owner == "explore"
    assert finding.check == "live_contract"
    assert "count has the maximum 20" in finding.message
    assert "/things/1?count=20" in finding.message
    assert "400" in finding.message


def test_a_max_the_service_goes_past_is_flagged(tmp_path: Path) -> None:
    [finding] = run_against(StandInService(max_count=50), tmp_path)

    assert finding.subject == "GET /things/{id} query param count"
    assert "/things/1?count=11" in finding.message


# A crash is no refusal: the spec says the value is refused, which is a 4xx.
def test_a_value_past_the_max_that_fails_on_the_server_is_flagged(tmp_path: Path) -> None:
    service = StandInService()
    service.server_error_on = "count=11"

    found = run_against(service, tmp_path, spec=things_spec(replies={**REPLIES, "500": {}}))

    assert subjects(found) == ["GET /things/{id} query param count"]
    assert "should be refused with a 4xx status" in found[0].message


def test_default_equals_omitted_param(tmp_path: Path) -> None:
    [finding] = run_against(StandInService(default_count=3), tmp_path)

    assert finding.subject == "GET /things/{id} query param count"
    assert "default 1" in finding.message
    assert "/things/1?count=1" in finding.message


def test_undocumented_response_field_flagged(tmp_path: Path) -> None:
    service = StandInService()
    service.extra = {"secret": "x"}

    [finding] = run_against(service, tmp_path)

    assert finding.subject == "GET /things/{id} reply 200"
    assert finding.owner == "explore"
    assert "'secret' was unexpected" in finding.message


def test_missing_required_field_flagged(tmp_path: Path) -> None:
    service = StandInService()
    service.missing = {"unit"}

    [finding] = run_against(service, tmp_path)

    assert finding.subject == "GET /things/{id} reply 200"
    assert "'unit' is a required property" in finding.message


def test_a_status_the_spec_does_not_list_is_flagged(tmp_path: Path) -> None:
    replies = {"200": REPLIES["200"], "404": REPLIES["404"]}

    found = run_against(StandInService(), tmp_path, spec=things_spec(replies=replies))

    assert subjects(found) == ["GET /things/{id} reply 400"]


def test_a_reply_that_is_not_json_is_flagged(tmp_path: Path) -> None:
    service = StandInService()
    service.plain_text_under = "/things/0"

    found = run_against(service, tmp_path)

    assert subjects(found) == ["GET /things/{id} reply 404"]
    assert "not JSON" in found[0].message


def test_an_operation_with_no_probe_that_succeeded_is_flagged(tmp_path: Path) -> None:
    service = StandInService()

    [finding] = run_against(service, tmp_path, probes=(a_probe("/things/7", status=404),))

    assert finding.subject == "GET /things/{id}"
    assert finding.owner == "explore"
    assert service.sent == []


def test_a_probe_that_succeeded_and_now_fails_stops_the_cases(tmp_path: Path) -> None:
    service = StandInService()

    [finding] = run_against(service, tmp_path, probes=(a_probe("/things/2?count=50"),))

    assert finding.subject == "GET /things/{id}"
    assert "/things/2?count=50" in finding.message
    assert service.paths_sent() == ["/things/2?count=50"]


# A request must not change what the service holds, so a verb other than GET or HEAD is never
# sent. An ANY route is requested with the GET and HEAD the spec lists on its path.
def test_only_get_and_head_operations_are_sent(tmp_path: Path) -> None:
    spec = things_spec()
    get = spec["paths"]["/things/{id}"]["get"]
    spec["paths"]["/things/{id}"] = {"get": get, "head": get, "post": get, "delete": get}
    service = StandInService()
    probes = (*PROBES, a_probe("/things/1", method="HEAD"))

    assert run_against(service, tmp_path, spec=spec, probes=probes) == []
    assert {request.method for request in service.sent} == {"GET", "HEAD"}


# The real annotated service


@pytest.mark.integration
def test_the_annotated_service_meets_its_own_spec(
    swag_binary: Path, annotated: Path, tmp_path: Path
) -> None:
    assert live_findings(annotated, swag=swag_binary, tmp_path=tmp_path) == []


@pytest.mark.integration
def test_a_maximum_the_annotated_service_goes_past_is_flagged(
    swag_binary: Path, annotated: Path, tmp_path: Path
) -> None:
    handlers = annotated / "birds" / "handlers.go"
    text = handlers.read_text(encoding="utf-8")
    handlers.write_text(text.replace("minimum(1)", "minimum(1) maximum(1)"), encoding="utf-8")

    [finding] = live_findings(annotated, swag=swag_binary, tmp_path=tmp_path)

    assert finding.subject == "GET /sightings/{id} path param id"
    assert "/sightings/2" in finding.message


def live_findings(repo: Path, *, swag: Path, tmp_path: Path) -> list[Finding]:
    spec = annotated_spec(repo, swag=swag)
    probes = (a_probe("/sightings/1"),)
    with built_service(repo, annotated_profile()) as binary:
        config = ServiceConfig(
            binary=binary, cwd=repo, port_env="AVIARY_PORT", log=tmp_path / "service.log"
        )
        with LocalServiceRunner().running(config) as service:
            context = a_context(
                tmp_path,
                spec=spec,
                wiki=a_wiki(probes=probes),
                services=ServicePair(before=service, after=service),
            )
            return LiveContract(LocalServiceClient()).run(context)

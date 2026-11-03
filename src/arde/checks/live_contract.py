"""The running service against the spec: each enum value and one outside it, each bound and the
value just past it, each default against leaving the param out, and every reply against its
closed schema.

The requests go to the service built from the output copy, which the spec describes. Each one
starts from a request that succeeded while Explore probed the route, and changes one param, so a
refusal can only come from that param. Only GET and HEAD are sent, because a request must not
change what the service holds: an operation with another verb is not requested, and an ANY route
is requested with the GET and HEAD the spec lists on its path.
"""

import json
from collections.abc import Iterator
from dataclasses import dataclass, field
from urllib.parse import quote, unquote_plus

from arde.application.checks_runner import CheckContext
from arde.application.ports import HttpRequest, ServiceClient, ServiceHandle
from arde.checks.closed_schema import ClosedSchemas
from arde.checks.spec_operations import Operation, SpecParam, operations, read_spec
from arde.domain.base import Frozen
from arde.domain.facts import ProbeRecord
from arde.domain.findings import Finding

CHECK = "live_contract"
SENT_METHODS = frozenset({"GET", "HEAD"})
SENT_LOCATIONS = frozenset({"path", "query"})
# The value a string param is sent outside its enum, made longer until no listed value matches.
UNLISTED_VALUE = "unlisted"
_FIX_THE_PARAM = (
    "Probe the param at that value, and correct its bounds, enum or default in the endpoint fact, "
    "then run again."
)


class BoundaryCase(Frozen):
    param: str
    location: str
    value: str
    # Whether the spec says the service takes the value.
    accepted: bool
    # What the spec says about the param that makes the case, as a reader would say it.
    claim: str


def boundary_requests(operation: Operation) -> list[BoundaryCase]:
    """The values each param is sent, and whether the spec says the service takes each one.

    Only path and query params get cases, because a request here carries nothing else.
    """
    cases: dict[tuple[str, str, str], BoundaryCase] = {}
    for param in operation.params:
        if param.location not in SENT_LOCATIONS:
            continue
        for case in (*_enum_cases(param), *_bound_cases(param)):
            cases.setdefault((case.param, case.location, case.value), case)
    return list(cases.values())


def _enum_cases(param: SpecParam) -> Iterator[BoundaryCase]:
    if not param.enum:
        return
    claim = f"{param.name} is one of {', '.join(param.enum)}"
    for value in param.enum:
        yield _case(param, value, accepted=True, claim=claim)
    yield _case(param, _unlisted(param), accepted=False, claim=claim)


# A service may read a value in any case and with spaces around it, and the enum lists the
# documented spellings only, so the value outside it is no spelling of a listed one.
def _unlisted(param: SpecParam) -> str:
    if param.type == "integer" and all(_is_whole_number(value) for value in param.enum):
        return str(max(int(value) for value in param.enum) + 1)
    listed = {value.strip().casefold() for value in param.enum}
    value = UNLISTED_VALUE
    while value.casefold() in listed:
        value += "-x"
    return value


def _is_whole_number(text: str) -> bool:
    return text.lstrip("-").isdigit()


# A path param names a thing, so a value inside its bounds may name nothing, and only the values
# just outside them are sent.
def _bound_cases(param: SpecParam) -> Iterator[BoundaryCase]:
    inside_is_sent = param.location != "path"
    if param.minimum is not None:
        claim = f"{param.name} has the minimum {param.minimum}"
        yield _case(param, str(param.minimum - 1), accepted=False, claim=claim)
        if inside_is_sent:
            yield _case(param, str(param.minimum), accepted=True, claim=claim)
    if param.maximum is not None:
        claim = f"{param.name} has the maximum {param.maximum}"
        if inside_is_sent:
            yield _case(param, str(param.maximum), accepted=True, claim=claim)
        yield _case(param, str(param.maximum + 1), accepted=False, claim=claim)


def _case(param: SpecParam, value: str, *, accepted: bool, claim: str) -> BoundaryCase:
    return BoundaryCase(
        param=param.name, location=param.location, value=value, accepted=accepted, claim=claim
    )


class LiveContract:
    """Checks the service built from the output copy against its spec.

    A request that gets no reply raises ProbeFailed and stops the run, because a service that does
    not answer is nothing Explore can correct in a fact.
    """

    name = CHECK

    def __init__(self, client: ServiceClient) -> None:
        self._client = client

    def run(self, ctx: CheckContext) -> list[Finding]:
        spec = read_spec(ctx.spec_path)
        exchange = _Exchange(
            client=self._client, service=ctx.services.after, schemas=ClosedSchemas(spec)
        )
        for operation in operations(spec):
            if operation.method in SENT_METHODS:
                _check_operation(operation, probes=ctx.wiki.probes, exchange=exchange)
        return sorted(exchange.findings.values(), key=lambda f: (f.subject, f.message))


@dataclass
class _Exchange:
    """Sends requests to one service, and keeps every finding once, by what it says."""

    client: ServiceClient
    service: ServiceHandle
    schemas: ClosedSchemas
    findings: dict[tuple[str, str], Finding] = field(default_factory=dict)

    def send(self, operation: Operation, path_and_query: str) -> ProbeRecord:
        """The service's reply, after its shape is checked against what the spec says of it."""
        # Validated, because the method is text from the spec and the request takes two alone.
        request = HttpRequest.model_validate(
            {"method": operation.method, "path_and_query": path_and_query}
        )
        reply = self.client.send_request(self.service, request)
        self._check_shape(operation, reply)
        return reply

    # A problem every reply shares is reported once, keyed without the request that showed it.
    def report(self, *, subject: str, problem: str, message: str, next_action: str) -> None:
        self.findings.setdefault(
            (subject, problem),
            Finding(
                check=CHECK,
                owner="explore",
                subject=subject,
                message=message,
                next_action=next_action,
            ),
        )

    def _check_shape(self, operation: Operation, reply: ProbeRecord) -> None:
        subject = f"{operation.key} reply {reply.status}"
        request = f"{reply.method} {reply.path_and_query}"
        status = str(reply.status)
        if status not in operation.responses:
            self.report(
                subject=subject,
                problem="not listed",
                message=(
                    f"{request} answered {reply.status}, which the spec does not list for "
                    f"{operation.key}."
                ),
                next_action="Add the response to the endpoint fact, with its type, then run again.",
            )
            return
        schema = operation.responses[status]
        # A reply to HEAD has no body to check.
        if schema is None or reply.method == "HEAD":
            return
        for problem in self._problems(schema, reply.body):
            self.report(
                subject=subject,
                problem=problem,
                message=(
                    f"{request} answered {reply.status} with a reply its schema does not "
                    f"describe: {problem}."
                ),
                next_action=(
                    "Probe the route again, and correct the response's type in the endpoint fact, "
                    "or the fact for that field, then run again."
                ),
            )

    def _problems(self, schema: dict[str, object], body: str) -> list[str]:
        try:
            parsed = json.loads(body)
        except json.JSONDecodeError:
            return ["the body is not JSON, and the spec gives it a JSON schema"]
        return self.schemas.problems(schema, parsed)


def _check_operation(
    operation: Operation, *, probes: tuple[ProbeRecord, ...], exchange: _Exchange
) -> None:
    probe = _probe_that_succeeded(operation, probes)
    if probe is None:
        if any(status.startswith("2") for status in operation.responses):
            exchange.report(
                subject=operation.key,
                problem="no probe",
                message=(
                    f"No probe of {operation.key} succeeded while exploring, so its params and its "
                    "replies cannot be checked against the running service."
                ),
                next_action="Probe the route with a request that succeeds, then run again.",
            )
        return
    baseline = exchange.send(operation, probe.path_and_query)
    if not _succeeded(baseline):
        exchange.report(
            subject=operation.key,
            problem="baseline failed",
            message=(
                f"{operation.method} {probe.path_and_query} answered {probe.status} while "
                f"exploring and {baseline.status} now, so no case can start from it."
            ),
            next_action=(
                "Probe the route again with a request that succeeds on the service as it is, "
                "then run again."
            ),
        )
        return
    for case in boundary_requests(operation):
        _send_case(operation, case, start=probe.path_and_query, exchange=exchange)
    for param in operation.params:
        if param.location == "query" and param.default is not None:
            _compare_default(operation, param, start=probe.path_and_query, exchange=exchange)


def _send_case(
    operation: Operation, case: BoundaryCase, *, start: str, exchange: _Exchange
) -> None:
    path_and_query = _with_value(start, operation=operation, case=case)
    reply = exchange.send(operation, path_and_query)
    if case.accepted and not _succeeded(reply):
        expected = "should succeed"
    elif not case.accepted and not 400 <= reply.status < 500:
        expected = "should be refused with a 4xx status"
    else:
        return
    exchange.report(
        subject=f"{operation.key} {case.location} param {case.param}",
        problem=f"{case.value} {expected}",
        message=(
            f"The spec says {case.claim}, so {operation.method} {path_and_query} {expected}, "
            f"and the service answered {reply.status}."
        ),
        next_action=_FIX_THE_PARAM,
    )


def _compare_default(
    operation: Operation, param: SpecParam, *, start: str, exchange: _Exchange
) -> None:
    assert param.default is not None
    omitted = _without(start, param.name)
    given = _with_query_value(start, name=param.name, value=param.default)
    left_out = exchange.send(operation, omitted)
    set_to_default = exchange.send(operation, given)
    if (left_out.status, left_out.body) == (set_to_default.status, set_to_default.body):
        return
    exchange.report(
        subject=f"{operation.key} query param {param.name}",
        problem=f"default {param.default}",
        message=(
            f"The spec gives {param.name} the default {param.default}, but "
            f"{operation.method} {omitted} answered {left_out.status} and "
            f"{operation.method} {given} answered {set_to_default.status} with another reply."
        ),
        next_action=_FIX_THE_PARAM,
    )


def _probe_that_succeeded(
    operation: Operation, probes: tuple[ProbeRecord, ...]
) -> ProbeRecord | None:
    for probe in probes:
        path = probe.path_and_query.split("?", 1)[0]
        fits = probe.method == operation.method and _fits(operation.path, path)
        if fits and _succeeded(probe):
            return probe
    return None


def _succeeded(reply: ProbeRecord) -> bool:
    return 200 <= reply.status < 300


# A segment {name} takes one segment of the path, {name...} takes the rest, and a pattern that
# ends in {$} matches only the path that ends there.
def _fits(template: str, path: str) -> bool:
    wanted = template.split("/")
    given = path.split("/")
    for index, segment in enumerate(wanted):
        if _is_rest(segment):
            return index == len(wanted) - 1 and len(given) >= index
        if index >= len(given):
            return False
        if segment == "{$}":
            if given[index] != "":
                return False
        elif _is_wildcard(segment):
            if given[index] == "":
                return False
        elif segment != given[index]:
            return False
    return len(given) == len(wanted)


def _with_value(start: str, *, operation: Operation, case: BoundaryCase) -> str:
    if case.location == "path":
        return _with_path_value(start, template=operation.path, case=case)
    return _with_query_value(start, name=case.param, value=case.value)


def _with_path_value(start: str, *, template: str, case: BoundaryCase) -> str:
    path, separator, query = start.partition("?")
    segments = path.split("/")
    value = quote(case.value, safe="")
    for index, segment in enumerate(template.split("/")):
        if segment == f"{{{case.param}}}":
            segments[index] = value
        elif segment == f"{{{case.param}...}}":
            segments = [*segments[:index], value]
    return "/".join(segments) + separator + query


def _with_query_value(start: str, *, name: str, value: str) -> str:
    path, _, query = _without(start, name).partition("?")
    pair = f"{quote(name, safe='')}={quote(value, safe='')}"
    return f"{path}?{query}&{pair}" if query else f"{path}?{pair}"


# The other pairs keep the bytes the probe sent, so + and %2B mean what they meant then.
def _without(start: str, name: str) -> str:
    path, _, query = start.partition("?")
    kept = [pair for pair in query.split("&") if pair and _name_of(pair) != name]
    return f"{path}?{'&'.join(kept)}" if kept else path


def _name_of(pair: str) -> str:
    return unquote_plus(pair.split("=", 1)[0])


def _is_wildcard(segment: str) -> bool:
    return segment.startswith("{") and segment.endswith("}")


def _is_rest(segment: str) -> bool:
    return _is_wildcard(segment) and segment.endswith("...}")

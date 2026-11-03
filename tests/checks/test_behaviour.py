"""The checks that prove a run changed no behaviour: its edits, the repo's own checks, the replies,
the routes and the layout.

The rules run with fakes in the fast tests. The measured cases run on rendered copies of the shop
fixture, with both services built and running, and need the Go toolchain.
"""

import dataclasses
import shutil
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import cast

import pytest
from fixtures.mutations import (
    add_a_route,
    log_the_query,
    misalign_a_field,
    refuse_the_largest_order,
    space_out_an_operator,
)
from render_support import SHOP_FACTS, copy_of_shop, render_shop
from shop_support import shop_profile

from arde.adapters.http_tool import local_service_client
from arde.adapters.service_runner import local_service_runner
from arde.application.checks_runner import CheckContext, ServicePair
from arde.application.ports import (
    CheckResult,
    EditCheckRequest,
    HttpRequest,
    LanguageAdapter,
    ProbeFailed,
    RenderManifest,
    RenderResult,
    ServiceClient,
    ServiceConfig,
    ServiceHandle,
)
from arde.checks.behaviour import Behaviour, compare_responses
from arde.checks.formatting import FormattingUnchanged
from arde.checks.routes import RoutesUnchanged
from arde.domain.facts import ProbeRecord, Wiki
from arde.domain.findings import Finding
from arde.domain.inventory import Inventory, Route
from arde.domain.profile import RepoProfile

BEFORE = ServiceHandle(base_url="http://127.0.0.1:5001", port=5001)
AFTER = ServiceHandle(base_url="http://127.0.0.1:5002", port=5002)
PAIR = ServicePair(before=BEFORE, after=AFTER)
# For the checks that start no service: nothing listens there.
NO_SERVICES = ServicePair(
    before=ServiceHandle(base_url="http://127.0.0.1:9", port=9),
    after=ServiceHandle(base_url="http://127.0.0.1:9", port=9),
)
PASSING = CheckResult(
    build_ok=True,
    vet_ok=True,
    test_ok=True,
    output="",
    tests_run=3,
    tests_skipped=(),
    tests_listed=3,
    ignored_files=(),
    unchecked_modules=(),
)
NO_RENDER = RenderResult(added_lines={}, tag_changes={}, changed_files=())
LAST_RUN = RenderManifest(lines={"web/payloads.go": ("// How many pieces were priced",)})


def get(path_and_query: str) -> HttpRequest:
    return HttpRequest(method="GET", path_and_query=path_and_query)


def reply(status: int = 200, *, body: str = "{}", content_type: str = "application/json") -> str:
    return f"{status}|{content_type}|{body}"


def probe(method: str, path_and_query: str) -> ProbeRecord:
    return ProbeRecord(
        kind="probe",
        method=method,
        path_and_query=path_and_query,
        status=200,
        content_type="application/json",
        body="{}",
    )


def route(method: str, path: str) -> Route:
    return Route(method=method, path=path, handler="h", file="web/server.go", line=1)


def inventory(*routes: Route) -> Inventory:
    return Inventory(
        routes=routes, params=(), error_codes=(), response_fields=(), env_vars=(), make_targets=()
    )


class FakeClient:
    """Answers each request from a table, by the port of the service and the path and query.

    A reply is written as status|content type|body. Anything not in the table gets reply().
    """

    def __init__(self, replies: dict[tuple[int, str], str | ProbeFailed] | None = None) -> None:
        self._replies = replies or {}
        self.sent: list[tuple[int, HttpRequest]] = []

    def send_request(self, service: ServiceHandle, request: HttpRequest) -> ProbeRecord:
        self.sent.append((service.port, request))
        answer = self._replies.get((service.port, request.path_and_query), reply())
        if isinstance(answer, ProbeFailed):
            raise answer
        status, content_type, body = answer.split("|", 2)
        return ProbeRecord(
            kind="probe",
            method=request.method,
            path_and_query=request.path_and_query,
            status=int(status),
            content_type=content_type,
            body=body,
        )

    def sent_to(self, service: ServiceHandle) -> list[HttpRequest]:
        return [request for port, request in self.sent if port == service.port]


class FakeAdapter:
    """The adapter methods these checks call, answering from what each test sets."""

    def __init__(
        self,
        *,
        edits: Sequence[Finding] = (),
        result: CheckResult = PASSING,
        output_inventory: Inventory | None = None,
        format_issues: dict[Path, set[str]] | None = None,
    ) -> None:
        self._edits = list(edits)
        self._result = result
        self._output_inventory = output_inventory
        self._format_issues = format_issues or {}
        self.edit_requests: list[EditCheckRequest] = []
        self.checked: list[Path] = []

    def check_agent_edits(self, request: EditCheckRequest) -> list[Finding]:
        self.edit_requests.append(request)
        return self._edits

    def run_checks(self, repo: Path, profile: RepoProfile) -> CheckResult:
        self.checked.append(repo)
        return self._result

    def extract_inventory(self, repo: Path, profile: RepoProfile) -> Inventory:
        assert self._output_inventory is not None
        return self._output_inventory

    def format_issues(self, repo: Path) -> set[str]:
        return self._format_issues.get(repo, set())


def as_adapter(fake: FakeAdapter) -> LanguageAdapter:
    return cast(LanguageAdapter, fake)


def as_client(fake: FakeClient) -> ServiceClient:
    return cast(ServiceClient, fake)


INPUT, OUTPUT = Path("/runs/1/input"), Path("/runs/1/output")
CONTEXT = CheckContext(
    input_repo=INPUT,
    output_repo=OUTPUT,
    profile=shop_profile(),
    inventory=inventory(route("GET", "/"), route("GET", "/items/{sku}")),
    wiki=Wiki(facts=(), probes=(probe("GET", "/items/tea-100?pieces=50"),)),
    render=NO_RENDER,
    spec_path=OUTPUT / "docs" / "swagger.json",
    services=PAIR,
)


def behaviour_findings(
    adapter: FakeAdapter, client: FakeClient, ctx: CheckContext = CONTEXT
) -> list[Finding]:
    check = Behaviour(as_adapter(adapter), as_client(client), last_run_manifest=LAST_RUN)
    return check.run(ctx)


# Comparing replies


def test_response_difference_names_the_request() -> None:
    client = FakeClient({(AFTER.port, "/items/tea-100?pieces=50"): reply(400)})

    findings = compare_responses(PAIR, [get("/items/tea-100?pieces=50")], client=as_client(client))

    assert [(finding.check, finding.owner, finding.subject) for finding in findings] == [
        ("behaviour", "code", "GET /items/tea-100?pieces=50")
    ]
    assert "200" in findings[0].message
    assert "400" in findings[0].message


def test_the_same_replies_are_no_finding() -> None:
    client = FakeClient()

    findings = compare_responses(PAIR, [get("/"), get("/items/x")], client=as_client(client))

    assert findings == []
    assert client.sent_to(BEFORE) == client.sent_to(AFTER) == [get("/"), get("/items/x")]


@pytest.mark.parametrize(
    "after", [reply(body='{"items":[]}'), reply(content_type="text/plain; charset=utf-8")]
)
def test_a_different_body_or_content_type_is_a_difference(after: str) -> None:
    client = FakeClient({(AFTER.port, "/"): after})

    findings = compare_responses(PAIR, [get("/")], client=as_client(client))

    assert [finding.subject for finding in findings] == ["GET /"]


def test_a_request_only_one_side_answers_is_a_difference() -> None:
    failed = ProbeFailed("GET / got no response: timed out.", next_action="Read service.log")
    client = FakeClient({(AFTER.port, "/"): failed})

    findings = compare_responses(PAIR, [get("/")], client=as_client(client))

    assert [finding.subject for finding in findings] == ["GET /"]
    assert "timed out" in findings[0].message


def test_a_request_neither_side_answers_is_no_difference() -> None:
    failed = ProbeFailed("GET / got no response: timed out.", next_action="Read service.log")
    client = FakeClient({(BEFORE.port, "/"): failed, (AFTER.port, "/"): failed})

    assert compare_responses(PAIR, [get("/")], client=as_client(client)) == []


# What Behaviour runs


def test_behaviour_sends_each_recorded_probe_and_each_route_without_a_placeholder() -> None:
    wiki = Wiki(
        facts=(),
        probes=(probe("GET", "/items/tea-100?pieces=50"), probe("HEAD", "/"), probe("GET", "/")),
    )
    routes = inventory(route("GET", "/"), route("GET", "/items/{sku}"), route("ANY", "/static/"))
    client = FakeClient()

    behaviour_findings(
        FakeAdapter(), client, dataclasses.replace(CONTEXT, wiki=wiki, inventory=routes)
    )

    assert client.sent_to(AFTER) == [
        get("/items/tea-100?pieces=50"),
        HttpRequest(method="HEAD", path_and_query="/"),
        get("/"),
        get("/static/"),
    ]
    assert client.sent_to(BEFORE) == client.sent_to(AFTER)


def test_behaviour_reports_a_reply_that_differs() -> None:
    client = FakeClient({(AFTER.port, "/items/tea-100?pieces=50"): reply(400)})

    findings = behaviour_findings(FakeAdapter(), client)

    assert [finding.subject for finding in findings] == ["GET /items/tea-100?pieces=50"]


def test_behaviour_checks_the_edits_against_the_render_and_the_last_runs_manifest() -> None:
    edit = Finding(
        check="agent_edits", owner="code", subject="main.go", message="m", next_action="n"
    )
    adapter = FakeAdapter(edits=[edit])

    findings = behaviour_findings(adapter, FakeClient())

    assert findings == [edit]
    assert adapter.edit_requests == [
        EditCheckRequest(before=INPUT, after=OUTPUT, render=NO_RENDER, manifest=LAST_RUN)
    ]


@pytest.mark.parametrize(
    ("failed", "subject"), [("build_ok", "build"), ("vet_ok", "vet"), ("test_ok", "tests")]
)
def test_the_output_failing_its_own_checks_is_a_finding(failed: str, subject: str) -> None:
    adapter = FakeAdapter(result=PASSING.model_copy(update={failed: False}))

    findings = behaviour_findings(adapter, FakeClient())

    assert adapter.checked == [OUTPUT]
    assert [(finding.check, finding.owner, finding.subject) for finding in findings] == [
        ("behaviour", "code", subject)
    ]


# Routes and layout


def test_a_route_the_output_gained_or_lost_is_a_finding() -> None:
    output = inventory(route("GET", "/"), route("GET", "/health"))

    findings = RoutesUnchanged(as_adapter(FakeAdapter(output_inventory=output))).run(CONTEXT)

    assert [(finding.check, finding.owner, finding.subject) for finding in findings] == [
        ("routes_unchanged", "code", "GET /health"),
        ("routes_unchanged", "code", "GET /items/{sku}"),
    ]


def test_the_same_routes_are_no_finding() -> None:
    adapter = FakeAdapter(output_inventory=CONTEXT.inventory)

    assert RoutesUnchanged(as_adapter(adapter)).run(CONTEXT) == []


def test_only_a_file_the_output_newly_lays_out_badly_is_a_finding() -> None:
    issues = {INPUT: {"web/payloads.go"}, OUTPUT: {"web/payloads.go", "pricing/pricing.go"}}

    findings = FormattingUnchanged(as_adapter(FakeAdapter(format_issues=issues))).run(CONTEXT)

    assert [(finding.check, finding.owner, finding.subject) for finding in findings] == [
        ("formatting_unchanged", "code", "pricing/pricing.go")
    ]


# The measured cases, on rendered copies of the shop

# One of each reply the shop gives: the list, a quote, the edges of the pieces limit, a bad value
# and an unknown item.
SHOP_REQUESTS = [
    get(path)
    for path in (
        "/",
        "/items/tea-100",
        "/items/tea-100?pieces=3",
        "/items/mug-blue?pieces=49",
        "/items/tea-100?pieces=51",
        "/items/tea-100?pieces=x",
        "/items/nothing",
    )
]


@dataclasses.dataclass
class Run:
    """The two copies of one run, as the checks find them."""

    before: Path
    after: Path
    render: RenderResult
    inventory: Inventory


def a_rendered_run(adapter: LanguageAdapter, root: Path) -> Run:
    before = copy_of_shop(root / "before")
    after = copy_of_shop(root / "after")
    return Run(
        before=before,
        after=after,
        render=render_shop(adapter, after).result,
        inventory=adapter.extract_inventory(before, shop_profile()),
    )


def context_of(
    run: Run, *, services: ServicePair = NO_SERVICES, probes: Sequence[ProbeRecord] = ()
) -> CheckContext:
    return CheckContext(
        input_repo=run.before,
        output_repo=run.after,
        profile=shop_profile(),
        inventory=run.inventory,
        wiki=Wiki(facts=SHOP_FACTS, probes=tuple(probes)),
        render=run.render,
        spec_path=run.after / "docs" / "swagger.json",
        services=services,
    )


@contextmanager
def both_running(adapter: LanguageAdapter, run: Run, logs: Path) -> Iterator[ServicePair]:
    runner = local_service_runner()
    profile = shop_profile()

    def config(binary: Path, repo: Path, log: str) -> ServiceConfig:
        return ServiceConfig(
            binary=binary, cwd=repo, port_env="SHOP_PORT", log=logs / log, timeout_s=30
        )

    with (
        adapter.built_service(run.before, profile) as before_binary,
        adapter.built_service(run.after, profile) as after_binary,
        runner.running(config(before_binary, run.before, "before.log")) as before,
        runner.running(config(after_binary, run.after, "after.log")) as after,
    ):
        yield ServicePair(before=before, after=after)


@pytest.mark.integration
def test_a_tightened_limit_is_caught_though_go_test_passes(
    adapter: LanguageAdapter, tmp_path: Path
) -> None:
    run = a_rendered_run(adapter, tmp_path)
    refuse_the_largest_order(run.after)
    at_the_limit = probe("GET", "/items/tea-100?pieces=50")
    check = Behaviour(adapter, local_service_client(), last_run_manifest=RenderManifest())

    with both_running(adapter, run, tmp_path) as services:
        findings = check.run(context_of(run, services=services, probes=(at_the_limit,)))

    # No finding names the build, vet or the tests, which all still pass on the output.
    assert {finding.subject for finding in findings} == {
        "web/handlers.go",
        "GET /items/tea-100?pieces=50",
    }


@pytest.mark.integration
def test_a_logged_query_is_caught_by_the_code_comparison_only(
    adapter: LanguageAdapter, tmp_path: Path
) -> None:
    run = a_rendered_run(adapter, tmp_path)
    log_the_query(run.after)
    request = EditCheckRequest(
        before=run.before, after=run.after, render=run.render, manifest=RenderManifest()
    )

    with both_running(adapter, run, tmp_path) as services:
        differences = compare_responses(services, SHOP_REQUESTS, client=local_service_client())
    edits = adapter.check_agent_edits(request)

    assert differences == []
    assert any(
        finding.subject == "web/handlers.go" and "in its code" in finding.message
        for finding in edits
    )


@pytest.mark.integration
def test_a_rendered_copy_serves_the_same_routes(adapter: LanguageAdapter, tmp_path: Path) -> None:
    run = a_rendered_run(adapter, tmp_path)

    assert RoutesUnchanged(adapter).run(context_of(run)) == []


@pytest.mark.integration
def test_new_route_rejected(adapter: LanguageAdapter, tmp_path: Path) -> None:
    run = a_rendered_run(adapter, tmp_path)
    add_a_route(run.after)

    findings = RoutesUnchanged(adapter).run(context_of(run))

    assert [(finding.owner, finding.subject) for finding in findings] == [("code", "GET /health")]


@pytest.mark.integration
def test_gofmt_fails_only_on_newly_flagged_files(adapter: LanguageAdapter, tmp_path: Path) -> None:
    before = copy_of_shop(tmp_path / "before")
    misalign_a_field(before)
    after = tmp_path / "after"
    shutil.copytree(before, after)
    render = render_shop(adapter, after).result
    space_out_an_operator(after)
    run = Run(before=before, after=after, render=render, inventory=inventory())

    findings = FormattingUnchanged(adapter).run(context_of(run))

    assert [(finding.owner, finding.subject) for finding in findings] == [
        ("code", "pricing/pricing.go")
    ]

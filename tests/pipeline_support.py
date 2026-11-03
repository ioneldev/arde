"""A small service the pipeline tests run end to end with stand-ins for the language, the service
and the model: one route, GET /things, which answers a list of things.

The stand-ins hold no logic of their own beyond what a step reads, so a test chooses what the
language's tools measure and the pipeline's own code decides the rest.
"""

import json
import shutil
import subprocess
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from recording_tracer import RecordingTracer
from scripted_model import calling, tool_call

from arde.adapters.http_tool import HTTP_PROBE
from arde.adapters.llm.fake import FakeLLM
from arde.adapters.repo_tools import repo_tools
from arde.adapters.workspace import Workspace
from arde.application.agent_loop import AgentDeps
from arde.application.budget import Budget
from arde.application.models_config import RoleConfig
from arde.application.pipeline import RunDeps, RunRequest
from arde.application.pipeline_deps import PipelineDeps
from arde.application.ports import (
    AgentToolbox,
    CheckResult,
    EditCheckRequest,
    HttpRequest,
    LLMResponse,
    QuoteContent,
    RenderManifest,
    RenderOutcome,
    RenderRequest,
    RenderResult,
    ResolvedSource,
    ResultCommit,
    RouteHashes,
    ServiceConfig,
    ServiceHandle,
    SourceQuote,
    Tool,
    ToolOutcome,
)
from arde.application.probe_log import ProbeLog
from arde.domain.facts import EndpointFact, Fact, Links, ProbeCitation, ProbeRecord, ResponseFact
from arde.domain.findings import Finding
from arde.domain.inventory import Inventory, Route
from arde.domain.profile import RepoProfile

MAIN = "package main\n\nfunc main() {}\n\nfunc things() {}\n"
THINGS_BODY = '{"things":[]}\n'
NOT_FOUND = "404 page not found\n"
ANNOTATION = "// things lists the things."
SPEC_FILE = "docs/openapi.json"

THINGS_ROUTE = Route(
    method="GET",
    path="/things",
    handler="things",
    file="main.go",
    line=3,
    handler_id="example.com/things.things",
    handler_file="main.go",
    handler_line=5,
    roots=("example.com/things.things",),
)

PROFILE = {
    "main_package": ".",
    "main_file": "main.go",
    "port_env": "THINGS_PORT",
    "patterns": [
        {"kind": "route", "call": r"^http\.HandleFunc$", "args": {"path": 0, "handler": 1}}
    ],
}

THINGS_PROBE = ProbeRecord(
    kind="probe",
    method="GET",
    path_and_query="/things",
    status=200,
    content_type="application/json",
    body=THINGS_BODY,
)

THINGS_FACT = EndpointFact(
    id="endpoint.things",
    topic="api",
    kind="endpoint",
    evidence=(ProbeCitation(kind="probe", method="GET", path_and_query="/things"),),
    links=Links(routes=("GET /things",)),
    route="GET /things",
    summary="List the things",
    params=(),
    responses=(
        ResponseFact(status=200, declared_type="Things", description="The things", error_codes=()),
    ),
)

ROLE_CONFIG = RoleConfig(model="claude-opus-5-5", effort="low", max_turns=5, max_tokens=1000)
MODELS = {role: ROLE_CONFIG for role in ("discovery", "explore", "docs", "classifier", "judge")}


def an_inventory(**changes: object) -> Inventory:
    fields: dict[str, object] = {
        "routes": (THINGS_ROUTE,),
        "params": (),
        "error_codes": (),
        "response_fields": (),
        "env_vars": (),
        "make_targets": (),
        "declarations": ("main.go::func main", "main.go::func things"),
    }
    return Inventory.model_validate({**fields, **changes})


def a_check_result(**changes: object) -> CheckResult:
    fields: dict[str, object] = {
        "build_ok": True,
        "vet_ok": True,
        "test_ok": True,
        "output": "== build (exit 0) ==\n",
        "tests_run": 2,
        "tests_skipped": (),
        "tests_listed": 2,
        "ignored_files": (),
        "unchecked_modules": (),
    }
    return CheckResult.model_validate({**fields, **changes})


@dataclass
class FakeAdapter:
    """The language's tools as a test chooses them. Each call is recorded by its name."""

    present: bool = True
    baseline: CheckResult = field(default_factory=a_check_result)
    inventory: Inventory = field(default_factory=an_inventory)
    hashes: RouteHashes | None = None
    changed: list[str] = field(default_factory=list)
    edit_findings: list[Finding] = field(default_factory=list)
    calls: list[str] = field(default_factory=list)
    checked_repos: list[Path] = field(default_factory=list)

    def detects(self, repo: Path) -> bool:
        self.calls.append("detects")
        return self.present

    def discovery_guidance(self) -> str:
        return "What the language needs a profile to say."

    def is_comment_only(self, old_file: Path, new_file: Path) -> bool:
        return _without_annotation(old_file) == _without_annotation(new_file)

    def changed_declarations(self, old_repo: Path, new_repo: Path) -> list[str]:
        self.calls.append("changed_declarations")
        return list(self.changed)

    def extract_inventory(self, repo: Path, profile: RepoProfile) -> Inventory:
        self.calls.append("extract_inventory")
        return self.inventory

    def route_fingerprints(self, repo: Path, routes: object, *, main_package: str) -> RouteHashes:
        if self.hashes is not None:
            return self.hashes
        text = "\n".join(_without_annotation(repo / "main.go"))
        return RouteHashes(hashes={route.key: text for route in self.inventory.routes}, unhashed={})

    def render_annotations(self, request: RenderRequest) -> RenderOutcome:
        self.calls.append("render_annotations")
        main = request.repo / "main.go"
        lines = [line for line in main.read_text().splitlines() if line != ANNOTATION]
        at = lines.index("func things() {}")
        main.write_text("\n".join([*lines[:at], ANNOTATION, *lines[at:]]) + "\n")
        return RenderOutcome(
            result=RenderResult(
                added_lines={"main.go": (ANNOTATION,)}, tag_changes={}, changed_files=("main.go",)
            ),
            manifest=RenderManifest(lines={"main.go": (ANNOTATION,)}),
        )

    def run_checks(self, repo: Path, profile: RepoProfile) -> CheckResult:
        self.calls.append("run_checks")
        self.checked_repos.append(repo)
        return self.baseline

    @contextmanager
    def built_service(self, repo: Path, profile: RepoProfile) -> Iterator[Path]:
        yield repo / "built-service"

    def generate_spec(self, repo: Path, profile: RepoProfile) -> Path:
        self.calls.append("generate_spec")
        path = repo / SPEC_FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        paths = {
            route.path: {route.method.lower(): {"responses": {"200": {"description": "OK"}}}}
            for route in self.inventory.routes
        }
        spec = {"openapi": "3.1.0", "info": {"title": "Things", "version": "1.0"}, "paths": paths}
        path.write_text(json.dumps(spec), encoding="utf-8")
        return path

    def format_issues(self, repo: Path) -> set[str]:
        return set()

    def check_agent_edits(self, request: EditCheckRequest) -> list[Finding]:
        return list(self.edit_findings)

    def quote_content(self, quote: SourceQuote) -> QuoteContent:
        return "code"

    def tool_versions(self) -> dict[str, str]:
        return {"compiler": "1.2.3", "helper": "sha256:abc"}


def _without_annotation(path: Path) -> list[str]:
    return [line for line in path.read_text().splitlines() if line != ANNOTATION]


@dataclass
class FakeRunner:
    """Hands out a new address per service and records how each was started."""

    configs: list[ServiceConfig] = field(default_factory=list)

    @contextmanager
    def running(self, config: ServiceConfig) -> Iterator[ServiceHandle]:
        self.configs.append(config)
        port = 5000 + len(self.configs)
        yield ServiceHandle(base_url=f"http://127.0.0.1:{port}", port=port)


@dataclass
class FakeService:
    """Answers GET /things with the list, and every other path as when nothing serves it."""

    requested: list[HttpRequest] = field(default_factory=list)

    def send_request(self, service: ServiceHandle, request: HttpRequest) -> ProbeRecord:
        self.requested.append(request)
        found = request.path_and_query == "/things"
        return ProbeRecord(
            kind="probe",
            method=request.method,
            path_and_query=request.path_and_query,
            status=200 if found else 404,
            content_type="application/json" if found else "text/plain; charset=utf-8",
            body=THINGS_BODY if found else NOT_FOUND,
        )


@dataclass
class FakeToolbox(AgentToolbox):
    """The real repo tools, and a probe that asks the fake service and records its reply."""

    service_client: FakeService

    def repo_tools(self, root: Path) -> tuple[Tool, ...]:
        return repo_tools(root)

    def http_probe_tool(self, service: ServiceHandle, log: ProbeLog) -> Tool:
        def probe(tool_input: dict[str, object]) -> ToolOutcome:
            reply = self.service_client.send_request(
                service, HttpRequest.model_validate(tool_input)
            )
            log.record(reply)
            return ToolOutcome(content=f"HTTP {reply.status}\n\n{reply.body}")

        return Tool(definition=HTTP_PROBE, handler=probe)


@dataclass
class FakeSourceControl:
    """Copies with shutil and commits nothing: it records each commit it was asked for."""

    commits: list[ResultCommit] = field(default_factory=list)

    def copy_source(self, source: ResolvedSource, dest: Path) -> None:
        shutil.copytree(source.root, dest, symlinks=True, ignore=shutil.ignore_patterns(".git"))

    def commit_result(self, commit: ResultCommit) -> str:
        self.commits.append(commit)
        return "c0ffee" * 6 + "abcd"


def a_source_tree(root: Path, *, main: str = MAIN) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "main.go").write_text(main, encoding="utf-8")
    return root


def a_git_repo(root: Path, *, main: str = MAIN) -> Path:
    """The tree, committed once, so the cli can resolve it as a local repo."""
    a_source_tree(root, main=main)
    git(root, "init", "--quiet")
    git(root, "add", "--all")
    git(root, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "--quiet", "-m", "first")
    return root


def git(repo: Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", *arguments], cwd=repo, capture_output=True, text=True, check=True
    ).stdout


def a_source(root: Path, *, kind: str = "local") -> ResolvedSource:
    return ResolvedSource.model_validate(
        {
            "kind": kind,
            "origin": str(root) if kind == "local" else "https://example.com/team/things.git",
            "repo_id": "things-0123456789ab",
            "root": root,
            "revision": "1234567890abcdef1234567890abcdef12345678",
        }
    )


# Model replies, agent by agent


def submitting_the_profile() -> LLMResponse:
    return calling(tool_call("submit_profile", **PROFILE, suspected_injections=[]))


def exploring(*facts: Fact, injections: tuple[str, ...] = ()) -> list[LLMResponse]:
    return [
        calling(tool_call("http_probe", method="GET", path_and_query="/things")),
        calling(
            tool_call(
                "add_facts",
                upserts=[fact.model_dump(mode="json") for fact in facts or (THINGS_FACT,)],
                deletes=[],
                suspected_gaps=[],
            )
        ),
        calling(tool_call("finish_facts", suspected_injections=list(injections))),
    ]


def a_first_run_script() -> list[LLMResponse]:
    return [submitting_the_profile(), *exploring()]


@dataclass
class Rig:
    """One pipeline run's stand-ins, and where it keeps its state."""

    tmp_path: Path
    script: list[LLMResponse]
    adapter: FakeAdapter = field(default_factory=FakeAdapter)
    max_usd: float = 10.0
    kind: str = "local"
    llm: FakeLLM = field(init=False)
    tracer: RecordingTracer = field(default_factory=RecordingTracer)
    runner: FakeRunner = field(default_factory=FakeRunner)
    service: FakeService = field(default_factory=FakeService)
    source_control: FakeSourceControl = field(default_factory=FakeSourceControl)
    store: Workspace = field(init=False)

    def __post_init__(self) -> None:
        self.llm = FakeLLM(self.script)
        self.store = Workspace(self.tmp_path / "home", "things-0123456789ab")

    @property
    def repo(self) -> Path:
        return self.tmp_path / "repo"

    def deps(self) -> RunDeps:
        shared = PipelineDeps(
            adapter=self.adapter,
            agents=AgentDeps(client=self.llm, tracer=self.tracer, budget=Budget(self.max_usd)),
            models=MODELS,
            store=self.store,
            service_runner=self.runner,
            service_client=self.service,
            toolbox=FakeToolbox(self.service),
        )
        return RunDeps(shared=shared, source_control=self.source_control)

    def request(self) -> RunRequest:
        if not self.repo.exists():
            a_source_tree(self.repo)
        return RunRequest(
            source=a_source(self.repo, kind=self.kind), run_dir=self.store.new_run_dir()
        )

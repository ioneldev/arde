"""The blind judge, run with a scripted model and a scripted service.

The service answers as the shop fixture does, from recorded replies. A spy writes the trace's
events and the requests sent into one list, so a test can see which came first.
"""

import json
from dataclasses import dataclass, field
from pathlib import Path

from pipeline_support import MODELS, FakeAdapter, FakeRunner, FakeService, FakeToolbox
from recording_tracer import RecordingTracer
from scripted_model import calling, tool_call, tool_results_of
from shop_facts import BAD_PIECES, LISTING, NOTHING, TEA

from arde.adapters.llm.fake import FakeLLM
from arde.adapters.workspace import Workspace
from arde.application.agent_loop import AgentDeps
from arde.application.agents.docs_writer import GENERATED_MARKER
from arde.application.agents.judge import (
    SYSTEM_PROMPT,
    JudgeDocs,
    JudgeRequest,
    JudgeResult,
    RetestRequest,
    read_judge_docs,
    retest_mismatch,
    run_judge,
)
from arde.application.budget import Budget
from arde.application.judge_scoring import Mismatch, Prediction, RequiredItems, RequiredParam
from arde.application.pipeline_deps import PipelineDeps
from arde.application.ports import (
    HttpRequest,
    LLMRequest,
    LLMResponse,
    ProbeFailed,
    ServiceHandle,
    ToolCall,
)
from arde.domain.facts import ProbeRecord

SERVICE = ServiceHandle(base_url="http://127.0.0.1:9", port=9)
API_PAGE = f"{GENERATED_MARKER}\n\n# API reference\n\n## GET /items/{{sku}}\n\nPrice some pieces.\n"
OVERVIEW_PAGE = f"{GENERATED_MARKER}\n\n# Overview\n\nThe shop prices tea and mugs.\n"
SPEC = '{"openapi":"3.1.0","info":{"title":"shop API","version":"1.0"},"paths":{}}'
DOCS = JudgeDocs(
    pages={"docs/overview.md": OVERVIEW_PAGE, "docs/api.md": API_PAGE},
    spec_path="docs/swagger.json",
    spec=SPEC,
)
QUOTE_ROUTE = "GET /items/{sku}"
NOTHING_REQUIRED = RequiredItems()
EVERYTHING_THE_SHOP_HAS = RequiredItems(
    routes=("GET /", QUOTE_ROUTE),
    params=(
        RequiredParam(route=QUOTE_ROUTE, name="pieces", location="query"),
        RequiredParam(route=QUOTE_ROUTE, name="sku", location="path"),
    ),
    error_codes=("bad_pieces", "unknown_sku"),
)
EVERY_SHOP_ITEM = (
    "route GET /",
    "route GET /items/{sku}",
    "param pieces (query) of GET /items/{sku}",
    "param sku (path) of GET /items/{sku}",
    "error code bad_pieces",
    "error code unknown_sku",
)


@dataclass
class Spy:
    """The trace's events and the requests sent, in the order they happened."""

    happened: list[tuple[str, object]] = field(default_factory=list)


class SpyTracer(RecordingTracer):
    def __init__(self, spy: Spy) -> None:
        super().__init__()
        self._spy = spy

    def event(self, kind: str, **fields: object) -> None:
        super().event(kind, **fields)
        self._spy.happened.append(("event", kind))


@dataclass
class ShopService:
    """Answers the shop's recorded replies, and 404 with no body to anything else."""

    spy: Spy
    replies: tuple[ProbeRecord, ...] = (TEA, NOTHING, BAD_PIECES, LISTING)
    failing: frozenset[str] = frozenset()

    def send_request(self, service: ServiceHandle, request: HttpRequest) -> ProbeRecord:
        self.spy.happened.append(("sent", f"{request.method} {request.path_and_query}"))
        if request.path_and_query in self.failing:
            raise ProbeFailed("GET /slow got no response: timed out.", next_action="Read the log")
        for reply in self.replies:
            if (reply.method, reply.path_and_query) == (request.method, request.path_and_query):
                return reply
        return ProbeRecord(
            kind="probe",
            method=request.method,
            path_and_query=request.path_and_query,
            status=404,
            content_type="text/plain; charset=utf-8",
            body="404 page not found\n",
        )


@dataclass
class Judging:
    script: list[LLMResponse]
    spy: Spy = field(default_factory=Spy)
    llm: FakeLLM = field(init=False)
    tracer: SpyTracer = field(init=False)
    service: ShopService = field(init=False)

    def __post_init__(self) -> None:
        self.llm = FakeLLM(self.script)
        self.tracer = SpyTracer(self.spy)
        self.service = ShopService(self.spy)

    def deps(self, tmp_path: Path) -> PipelineDeps:
        return PipelineDeps(
            adapter=FakeAdapter(),
            agents=AgentDeps(client=self.llm, tracer=self.tracer, budget=Budget(10.0)),
            models=MODELS,
            store=Workspace(tmp_path / "home", "shop-0123456789ab"),
            service_runner=FakeRunner(),
            service_client=self.service,
            toolbox=FakeToolbox(FakeService()),
        )

    def judge(self, tmp_path: Path, required: RequiredItems = NOTHING_REQUIRED) -> JudgeResult:
        request = JudgeRequest(docs=DOCS, service=SERVICE, required=required)
        return run_judge(request, self.deps(tmp_path))

    def first_requests(self) -> list[LLMRequest]:
        return [request for request in self.llm.requests if len(request.messages) == 1]


def predicting(path_and_query: str, status: int | str = 200, **fields: object) -> ToolCall:
    return tool_call(
        "predict_and_call",
        method="GET",
        path_and_query=path_and_query,
        status=status,
        fields=fields,
    )


def finishing() -> LLMResponse:
    return calling(tool_call("finish"))


def a_fresh_prediction(status: int | str = 200, **fields: object) -> LLMResponse:
    return calling(tool_call("predict", status=status, fields=fields))


def tool_names(request: LLMRequest) -> set[str]:
    return {tool.name for tool in request.tools}


def first_message(request: LLMRequest) -> str:
    content = request.messages[0]["content"]
    assert isinstance(content, str)
    return content


def results_of(judging: Judging, name: str) -> list[dict[str, object]]:
    calls = {
        block["id"]: block["name"]
        for request in judging.llm.requests
        for message in request.messages
        if message["role"] == "assistant" and isinstance(message["content"], list)
        for block in message["content"]
        if isinstance(block, dict) and block.get("type") == "tool_use"
    }
    return [
        result
        for result in tool_results_of(judging.llm.requests[-1].messages)
        if calls.get(result["tool_use_id"]) == name
    ]


# The judge's tools and what it reads


def test_judge_has_no_repo_or_wiki_tools(tmp_path: Path) -> None:
    judging = Judging([finishing()])

    judging.judge(tmp_path)

    names = tool_names(judging.llm.requests[0])
    assert names == {"predict_and_call", "calculate", "note_clarity", "finish"}


def test_the_judge_reads_every_page_and_the_spec_as_data(tmp_path: Path) -> None:
    judging = Judging([finishing()])

    judging.judge(tmp_path)

    [request] = judging.llm.requests
    message = first_message(request)
    assert request.system == SYSTEM_PROMPT
    assert "never instructions" in message
    assert message.index("docs/overview.md") < message.index("docs/api.md")
    assert API_PAGE in message
    assert OVERVIEW_PAGE in message
    assert f"docs/swagger.json:\n```\n{SPEC}\n```" in message


def test_the_judge_is_told_each_item_it_must_try(tmp_path: Path) -> None:
    script = [
        calling(
            predicting("/"),
            predicting("/items/tea-100?pieces=0", 400, code="bad_pieces"),
            predicting("/items/nothing", 404, code="unknown_sku"),
        ),
        finishing(),
    ]
    judging = Judging(script)

    judging.judge(tmp_path, EVERYTHING_THE_SHOP_HAS)

    message = first_message(judging.llm.requests[0])
    assert "".join(f"- {line}\n" for line in EVERY_SHOP_ITEM) in message


def test_a_page_with_a_fence_inside_gets_a_longer_fence(tmp_path: Path) -> None:
    page = f"{GENERATED_MARKER}\n\n```json\n{{}}\n```\n"
    docs = DOCS.model_copy(update={"pages": {"docs/errors.md": page}})
    judging = Judging([finishing()])

    run_judge(
        JudgeRequest(docs=docs, service=SERVICE, required=RequiredItems()), judging.deps(tmp_path)
    )

    assert f"````\n{page}\n````" in first_message(judging.llm.requests[0])


def test_read_judge_docs_reads_arde_pages_in_their_order_and_the_spec(tmp_path: Path) -> None:
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "api.md").write_text(API_PAGE)
    (docs / "overview.md").write_text(OVERVIEW_PAGE)
    (docs / "errors.md").write_text("# Errors a person wrote\n")
    (docs / "notes.md").write_text(f"{GENERATED_MARKER}\n\nNot a page ARDE writes.\n")
    (docs / "swagger.json").write_text(SPEC)

    read = read_judge_docs(tmp_path, tmp_path / "docs" / "swagger.json")

    assert read == DOCS
    assert list(read.pages) == ["docs/overview.md", "docs/api.md"]


# A prediction, and the reply


def test_prediction_recorded_before_request_sent(tmp_path: Path) -> None:
    judging = Judging([calling(predicting("/items/tea-100", sku="tea-100")), finishing()])

    judging.judge(tmp_path)

    happened = judging.spy.happened
    predicted = happened.index(("event", "prediction"))
    sent = happened.index(("sent", "GET /items/tea-100"))
    assert predicted < sent
    [prediction] = judging.tracer.events_of("prediction")
    assert prediction == {
        "role": "judge",
        "request": "GET /items/tea-100",
        "status": 200,
        "fields": {"sku": "tea-100"},
        "retest": False,
    }


def test_the_judge_is_shown_the_reply_and_that_it_matched(tmp_path: Path) -> None:
    judging = Judging([calling(predicting("/items/tea-100", pieces=1)), finishing()])

    judging.judge(tmp_path)

    [result] = tool_results_of(judging.llm.requests[1].messages)
    assert result["is_error"] is False
    assert result["content"] == (
        f"Your prediction matched.\n\nHTTP 200\nContent-Type: application/json\n\n{TEA.body}"
    )


def test_the_judge_is_shown_what_it_got_wrong(tmp_path: Path) -> None:
    script = [calling(predicting("/items/tea-100", pieces=2)), finishing(), a_fresh_prediction()]
    judging = Judging(script)

    judging.judge(tmp_path)

    [result] = tool_results_of(judging.llm.requests[1].messages)
    content = result["content"]
    assert isinstance(content, str)
    assert content.startswith(
        "Your prediction did not match: pieces: predicted 2, the reply holds 1.\n\nHTTP 200"
    )


def test_a_reply_body_past_the_limit_is_shown_cut(tmp_path: Path) -> None:
    long = TEA.model_copy(update={"path_and_query": "/items/long", "body": "x" * 25_000})
    judging = Judging([calling(predicting("/items/long")), finishing()])
    judging.service.replies = (long,)

    judging.judge(tmp_path)

    [result] = tool_results_of(judging.llm.requests[1].messages)
    content = result["content"]
    assert isinstance(content, str)
    assert content.endswith("[ARDE showed the first 20000 of the 25000 characters of this body.]")


def test_a_prediction_that_breaks_a_rule_goes_back_to_the_judge(tmp_path: Path) -> None:
    posting = tool_call(
        "predict_and_call", method="POST", path_and_query="/x", status=200, fields={}
    )
    judging = Judging([calling(posting), finishing()])

    judging.judge(tmp_path)

    [result] = tool_results_of(judging.llm.requests[1].messages)
    assert result["is_error"] is True
    assert ("sent", "POST /x") not in judging.spy.happened


def test_a_request_that_gets_no_reply_goes_back_to_the_judge_and_tries_nothing(
    tmp_path: Path,
) -> None:
    script = [
        calling(predicting("/items/slow")),
        finishing(),
        calling(predicting("/items/tea-100")),
        finishing(),
    ]
    judging = Judging(script)
    judging.service.failing = frozenset({"/items/slow"})

    result = judging.judge(tmp_path, RequiredItems(routes=(QUOTE_ROUTE,)))

    failed, refused = tool_results_of(judging.llm.requests[2].messages)
    assert failed["is_error"] is True
    assert "timed out" in str(failed["content"])
    assert str(refused["content"]).endswith("\n- route GET /items/{sku}")
    assert result.findings == ()


# Arithmetic and clarity


def test_calculate_answers_the_value_and_refuses_anything_else(tmp_path: Path) -> None:
    script = [
        calling(
            tool_call("calculate", expression="round(450 * 3 / 7, 2)"),
            tool_call("calculate", expression="__import__('os')"),
        ),
        finishing(),
    ]
    judging = Judging(script)

    judging.judge(tmp_path)

    value, refused = tool_results_of(judging.llm.requests[1].messages)
    assert (value["content"], value["is_error"]) == ("192.86", False)
    assert refused["is_error"] is True
    assert str(refused["content"]).startswith("Not calculated: it holds __import__('os')")


def test_clarity_notes_never_block(tmp_path: Path) -> None:
    note = "The API reference names charge_cents but not how it is computed."
    script = [calling(tool_call("note_clarity", note=note)), finishing()]
    judging = Judging(script)

    result = judging.judge(tmp_path)

    assert result == JudgeResult(findings=(), confirmed=(), noise=(), clarity_notes=(note,))
    assert judging.tracer.events_of("clarity") == [{"role": "judge", "summary": note}]


# Coverage, then finish


def test_finish_refused_until_required_items_tried(tmp_path: Path) -> None:
    script = [
        finishing(),
        calling(predicting("/items/tea-100?pieces=2", charge_cents=900)),
        finishing(),
        calling(
            predicting("/"),
            predicting("/items/tea-100?pieces=0", 400, code="bad_pieces"),
            predicting("/items/nothing", 404, code="unknown_sku"),
        ),
        finishing(),
    ]
    tea_for_two = TEA.model_copy(
        update={
            "path_and_query": "/items/tea-100?pieces=2",
            "body": '{"issued_by":"shop","sku":"tea-100","pieces":2,"charge_cents":900}\n',
        }
    )
    judging = Judging(script)
    judging.service.replies = (*judging.service.replies, tea_for_two)

    result = judging.judge(tmp_path, EVERYTHING_THE_SHOP_HAS)

    assert result.findings == ()
    first, second = [
        str(block["content"])
        for request in (judging.llm.requests[1], judging.llm.requests[3])
        for block in tool_results_of(request.messages)[-1:]
    ]
    assert first.endswith("".join(f"\n- {line}" for line in EVERY_SHOP_ITEM))
    assert second.endswith("\n- route GET /\n- error code bad_pieces\n- error code unknown_sku")
    assert len(judging.llm.requests) == len(script)


def test_finish_is_accepted_at_once_when_nothing_is_required(tmp_path: Path) -> None:
    judging = Judging([finishing()])

    assert judging.judge(tmp_path) == JudgeResult(findings=(), confirmed=(), noise=())


# Unknown


def test_unknown_on_required_item_is_mismatch(tmp_path: Path) -> None:
    script = [calling(predicting("/items/tea-100", "unknown")), finishing(), a_fresh_prediction()]
    judging = Judging(script)

    result = judging.judge(tmp_path, RequiredItems(routes=(QUOTE_ROUTE,)))

    [mismatch] = judging.tracer.events_of("mismatch")
    assert mismatch["detail"] == (
        "status: predicted unknown, the service answered 200, and the request tries a required item"
    )
    assert len(judging.first_requests()) == 2
    assert [m.detail for m in result.noise] == [str(mismatch["detail"])]


def test_unknown_elsewhere_is_trace_note(tmp_path: Path) -> None:
    script = [
        calling(predicting("/nothing/here", "unknown"), predicting("/items/tea-100")),
        finishing(),
    ]
    judging = Judging(script)

    result = judging.judge(tmp_path, RequiredItems(routes=(QUOTE_ROUTE,)))

    assert result == JudgeResult(findings=(), confirmed=(), noise=())
    assert judging.tracer.events_of("mismatch") == []
    [note] = judging.tracer.events_of("judge_note")
    assert note == {
        "role": "judge",
        "request": "GET /nothing/here",
        "summary": "The docs gave no status to predict for GET /nothing/here, which tries no "
        "required item, and the service answered 404.",
    }
    assert len(judging.first_requests()) == 1


def test_unknown_with_a_wrong_field_elsewhere_is_still_a_mismatch(tmp_path: Path) -> None:
    script = [
        calling(predicting("/nothing/here", "unknown", code="none")),
        finishing(),
        a_fresh_prediction(404),
    ]
    judging = Judging(script)

    result = judging.judge(tmp_path)

    assert [m.detail for m in result.noise] == [
        'code: predicted "none", and the reply body is not JSON'
    ]
    assert len(judging.tracer.events_of("judge_note")) == 1


# Re-tests


def test_mismatch_cleared_when_fresh_retest_is_right(tmp_path: Path) -> None:
    script = [
        calling(predicting("/items/tea-100", charge_cents=400)),
        finishing(),
        a_fresh_prediction(200, charge_cents=450),
    ]
    judging = Judging(script)

    result = judging.judge(tmp_path)

    assert result.findings == ()
    assert [m.prediction.request.path_and_query for m in result.noise] == ["/items/tea-100"]
    [noise] = judging.tracer.events_of("judge_noise")
    assert noise == {
        "role": "judge",
        "request": "GET /items/tea-100",
        "detail": "charge_cents: predicted 400, the reply holds 450",
    }


def test_mismatch_kept_when_retest_also_wrong(tmp_path: Path) -> None:
    script = [
        calling(predicting("/items/tea-100", charge_cents=400)),
        finishing(),
        a_fresh_prediction(200, charge_cents=405),
    ]
    judging = Judging(script)

    result = judging.judge(tmp_path)

    [finding] = result.findings
    assert (finding.check, finding.owner, finding.subject) == (
        "judge",
        "explore",
        "GET /items/tea-100",
    )
    assert finding.message == (
        "Reading only the docs and the spec, the judge predicted status 200 with charge_cents = "
        f"400, and the service answered 200 with the body {json.dumps(TEA.body)}. What differs: "
        "charge_cents: predicted 400, the reply holds 450. A fresh reading of the same docs "
        "predicted it wrong too."
    )
    assert "Probe" in finding.next_action
    [confirmed] = judging.tracer.events_of("judge_confirmed")
    assert confirmed == {
        "role": "judge",
        "request": "GET /items/tea-100",
        "detail": "charge_cents: predicted 405, the reply holds 450",
    }
    assert [m.detail for m in result.confirmed] == [
        "charge_cents: predicted 400, the reply holds 450"
    ]


def test_an_unknown_status_in_the_retest_confirms_the_mismatch(tmp_path: Path) -> None:
    script = [
        calling(predicting("/items/tea-100", charge_cents=400)),
        finishing(),
        a_fresh_prediction("unknown", charge_cents=450),
    ]
    judging = Judging(script)

    assert len(judging.judge(tmp_path).findings) == 1


def test_retest_context_sees_only_docs_and_request(tmp_path: Path) -> None:
    script = [
        calling(tool_call("note_clarity", note="DISTINCT-NOTE")),
        calling(predicting("/items/tea-100", charge_cents=4321)),
        finishing(),
        calling(tool_call("calculate", expression="450 * 1")),
        a_fresh_prediction(200, charge_cents=450),
    ]
    judging = Judging(script)

    judging.judge(tmp_path)

    retest = judging.first_requests()[1]
    assert retest.system == SYSTEM_PROMPT
    assert tool_names(retest) == {"calculate", "predict"}
    message = first_message(retest)
    assert "GET /items/tea-100" in message
    assert API_PAGE in message and OVERVIEW_PAGE in message and SPEC in message
    for unseen in ("4321", "DISTINCT-NOTE", '"issued_by"', "predict_and_call", "finish"):
        assert unseen not in message


def test_the_retest_records_its_prediction_before_it_sends_the_request_again(
    tmp_path: Path,
) -> None:
    script = [
        calling(predicting("/items/tea-100", charge_cents=400)),
        finishing(),
        a_fresh_prediction(200, charge_cents=450),
    ]
    judging = Judging(script)

    judging.judge(tmp_path)

    happened = judging.spy.happened
    sent = [index for index, item in enumerate(happened) if item == ("sent", "GET /items/tea-100")]
    predicted = [index for index, item in enumerate(happened) if item == ("event", "prediction")]
    assert len(sent) == len(predicted) == 2
    assert predicted[1] < sent[1]
    assert judging.tracer.events_of("prediction")[1]["retest"] is True


def test_each_request_predicted_wrong_is_retested_once(tmp_path: Path) -> None:
    script = [
        calling(
            predicting("/items/tea-100", charge_cents=400),
            predicting("/items/tea-100", charge_cents=401),
        ),
        finishing(),
        a_fresh_prediction(200, charge_cents=450),
    ]
    judging = Judging(script)

    result = judging.judge(tmp_path)

    assert len(judging.first_requests()) == 2
    assert len(result.noise) == 1


def test_retest_mismatch_says_whether_a_fresh_reading_is_wrong_too(tmp_path: Path) -> None:
    prediction = Prediction(
        method="GET", path_and_query="/items/tea-100", status=200, fields={"pieces": 3}
    )
    mismatch = Mismatch(prediction=prediction, actual=TEA, detail="pieces: predicted 3, ...")
    request = RetestRequest(docs=DOCS, service=SERVICE, mismatch=mismatch)

    right = Judging([a_fresh_prediction(200, pieces=1)])
    wrong = Judging([a_fresh_prediction(404)])

    assert retest_mismatch(request, right.deps(tmp_path)) is False
    assert retest_mismatch(request, wrong.deps(tmp_path)) is True


def test_the_judge_is_charged_to_its_own_role(tmp_path: Path) -> None:
    judging = Judging([finishing()])
    deps = judging.deps(tmp_path)

    run_judge(JudgeRequest(docs=DOCS, service=SERVICE, required=RequiredItems()), deps)

    assert deps.agents.budget.totals()["judge"].calls == 1

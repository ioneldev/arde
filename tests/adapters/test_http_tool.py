"""The HTTP tool against a stand-in service: a small http.server on a free port, one per test.

The model chooses the method and the path, so neither may reach another host or change what the
service holds, and the service chooses every byte of the reply.
"""

import http.client
import http.server
import socket
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass

import pytest
from pydantic import ValidationError

from arde.adapters import http_tool
from arde.adapters.http_tool import (
    RECORDED_BODY_LIMIT_BYTES,
    SHOWN_BODY_LIMIT_CHARS,
    http_probe_tool,
    local_service_client,
    send_request,
)
from arde.application.ports import (
    HttpRequest,
    ProbeFailed,
    ServiceError,
    ServiceHandle,
    ToolOutcome,
)
from arde.application.probe_log import ProbeLog
from arde.domain.errors import ArdeError
from arde.domain.facts import ProbeRecord

LARGE_BODY = "x" * (SHOWN_BODY_LIMIT_CHARS + 1000)


@dataclass(frozen=True)
class Reply:
    status: int
    body: bytes = b""
    headers: tuple[tuple[str, str], ...] = ()


TEXT = ("Content-Type", "text/plain; charset=utf-8")
REPLIES = {
    "/items?page=2": Reply(200, b'{"items":[1,2]}', (("Content-Type", "application/json"),)),
    "/frozen": Reply(405, b"method not allowed\n", (TEXT, ("Allow", "GET, HEAD"))),
    "//items/1": Reply(307, b"see /items/1\n", (TEXT, ("Location", "/items/1"))),
    "/large": Reply(200, LARGE_BODY.encode(), (TEXT,)),
    "/too-large": Reply(200, b"y" * (RECORDED_BODY_LIMIT_BYTES + 1), (TEXT,)),
    "/escape": Reply(200, b"\x1b]0;OWNED\x07 ok \xff", (TEXT,)),
    "/bare": Reply(200, b"ok"),
}
NOT_FOUND = Reply(404, b"404 page not found\n", (TEXT,))


@dataclass(frozen=True)
class Drip:
    at_once: bytes
    byte_by_byte: bytes


# Each byte comes well inside the time one read may wait, so only a deadline on the whole
# exchange stops the probe. Forty bytes take two seconds.
DRIP_GAP_S = 0.05
DRIPS = {
    "/drip-body": Drip(b"HTTP/1.0 200 OK\r\nContent-Length: 40\r\n\r\n", b"x" * 40),
    "/drip-headers": Drip(b"", b"HTTP/1.0 200 OK\r\nContent-Length: 2\r\n\r\nok"),
}


@dataclass(frozen=True)
class Seen:
    method: str
    target: str
    host: str


class StandInService(http.server.ThreadingHTTPServer):
    def __init__(self) -> None:
        super().__init__(("127.0.0.1", 0), StandInHandler)
        self.seen: list[Seen] = []

    @property
    def handle(self) -> ServiceHandle:
        port = self.server_address[1]
        return ServiceHandle(base_url=f"http://127.0.0.1:{port}", port=port)


class StandInHandler(http.server.BaseHTTPRequestHandler):
    server: StandInService

    def do_GET(self) -> None:
        self.answer(with_body=True)

    def do_HEAD(self) -> None:
        self.answer(with_body=False)

    def answer(self, *, with_body: bool) -> None:
        # http.server turns a target that starts with // into /, so it comes from the request line.
        target = self.requestline.split(" ")[1]
        self.server.seen.append(Seen(self.command, target, self.headers.get("Host", "")))
        if target == "/hang-up":
            return
        if target in DRIPS:
            self.drip(DRIPS[target])
            return
        if target == "/slow":
            time.sleep(1)
        reply = REPLIES.get(target, NOT_FOUND)
        self.send_response(reply.status)
        for name, value in reply.headers:
            self.send_header(name, value)
        self.send_header("Content-Length", str(len(reply.body)))
        self.end_headers()
        if with_body:
            self.wfile.write(reply.body)

    def drip(self, drip: Drip) -> None:
        try:
            self.wfile.write(drip.at_once)
            for byte in drip.byte_by_byte:
                time.sleep(DRIP_GAP_S)
                self.wfile.write(bytes([byte]))
        # The probe gave up and closed the connection, as it should.
        except (BrokenPipeError, ConnectionResetError):
            pass

    def log_message(self, format: str, *args: object) -> None:
        pass


def started() -> Iterator[StandInService]:
    server = StandInService()
    # shutdown waits for the loop's next poll, which by default comes half a second later.
    thread = threading.Thread(target=server.serve_forever, args=(0.01,), daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()


@pytest.fixture
def service() -> Iterator[StandInService]:
    yield from started()


@pytest.fixture
def other_service() -> Iterator[StandInService]:
    yield from started()


def probe(
    service: StandInService,
    path_and_query: str,
    *,
    method: str = "GET",
    log: ProbeLog | None = None,
) -> ToolOutcome:
    tool = http_probe_tool(service.handle, log or ProbeLog())
    return tool.handler({"method": method, "path_and_query": path_and_query})


def a_closed_port() -> ServiceHandle:
    with socket.socket() as reserved:
        reserved.bind(("127.0.0.1", 0))
        port = reserved.getsockname()[1]
    return ServiceHandle(base_url=f"http://127.0.0.1:{port}", port=port)


def test_http_probe_records_status_type_and_body(service: StandInService) -> None:
    log = ProbeLog()

    outcome = probe(service, "/items?page=2", log=log)

    assert outcome == ToolOutcome(
        content='HTTP 200\nContent-Type: application/json\n\n{"items":[1,2]}'
    )
    assert log.find("GET", "/items?page=2") == ProbeRecord(
        kind="probe",
        method="GET",
        path_and_query="/items?page=2",
        status=200,
        content_type="application/json",
        body='{"items":[1,2]}',
    )


def test_the_tool_text_shows_what_a_405_allows(service: StandInService) -> None:
    assert probe(service, "/frozen").content == (
        "HTTP 405\nContent-Type: text/plain; charset=utf-8\nAllow: GET, HEAD\n\n"
        "method not allowed\n"
    )


def test_http_probe_does_not_follow_redirects(service: StandInService) -> None:
    log = ProbeLog()

    outcome = probe(service, "//items/1", log=log)

    assert outcome.content == (
        "HTTP 307\nContent-Type: text/plain; charset=utf-8\nLocation: /items/1\n\nsee /items/1\n"
    )
    assert [seen.target for seen in service.seen] == ["//items/1"]
    assert log.find("GET", "//items/1") is not None


@pytest.mark.parametrize(
    "path_and_query",
    [
        "http://127.0.0.1:{port}/items?page=2",
        "127.0.0.1:{port}/items?page=2",
        "@127.0.0.1:{port}/items?page=2",
        "items?page=2",
        "",
    ],
)
def test_http_probe_only_reaches_given_service(
    service: StandInService, other_service: StandInService, path_and_query: str
) -> None:
    aimed_elsewhere = path_and_query.format(port=other_service.handle.port)

    with pytest.raises(ValidationError, match="starts with /"):
        probe(service, aimed_elsewhere)

    assert service.seen == other_service.seen == []


def test_a_path_that_starts_with_two_slashes_and_a_host_stays_on_the_service(
    service: StandInService, other_service: StandInService
) -> None:
    aimed_elsewhere = f"//127.0.0.1:{other_service.handle.port}/items?page=2"

    probe(service, aimed_elsewhere)

    assert other_service.seen == []
    assert service.seen == [Seen("GET", aimed_elsewhere, f"127.0.0.1:{service.handle.port}")]


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE", "OPTIONS", "get"])
def test_a_method_other_than_get_or_head_is_refused_before_anything_is_sent(
    service: StandInService, method: str
) -> None:
    with pytest.raises(ValidationError):
        probe(service, "/items?page=2", method=method)

    assert service.seen == []


@pytest.mark.parametrize(
    "path_and_query", ["/a b", "/a\r\nX-Injected: 1", "/caf\u00e9", "/a\x00", "/a\tb", "/a\x7f"]
)
def test_a_space_a_control_character_or_a_letter_outside_ascii_must_be_percent_encoded(
    service: StandInService, path_and_query: str
) -> None:
    with pytest.raises(ValidationError, match="percent-encoded"):
        probe(service, path_and_query)

    assert service.seen == []


def test_plus_and_percent_2b_go_out_untouched(service: StandInService) -> None:
    probe(service, "/search?q=a+b%2Bc&q=%20")

    assert [seen.target for seen in service.seen] == ["/search?q=a+b%2Bc&q=%20"]


def test_head_is_sent_as_head_and_records_no_body(service: StandInService) -> None:
    log = ProbeLog()

    outcome = probe(service, "/items?page=2", method="HEAD", log=log)

    assert outcome.content == "HTTP 200\nContent-Type: application/json\n\n(no body)"
    assert [seen.method for seen in service.seen] == ["HEAD"]
    probe_made = log.find("HEAD", "/items?page=2")
    assert probe_made is not None
    assert probe_made.body == ""


def test_body_truncated_for_model_but_not_in_log(service: StandInService) -> None:
    log = ProbeLog()

    outcome = probe(service, "/large", log=log)

    assert outcome.content == (
        f"HTTP 200\nContent-Type: text/plain; charset=utf-8\n\n"
        f"{LARGE_BODY[:SHOWN_BODY_LIMIT_CHARS]}\n"
        f"[ARDE showed the first {SHOWN_BODY_LIMIT_CHARS} of the {len(LARGE_BODY)} characters "
        "of this body.]"
    )
    probe_made = log.find("GET", "/large")
    assert probe_made is not None
    assert probe_made.body == LARGE_BODY


def test_control_characters_in_a_body_are_shown_escaped_and_recorded_as_sent(
    service: StandInService,
) -> None:
    log = ProbeLog()

    outcome = probe(service, "/escape", log=log)

    assert outcome.content.endswith("\n\n\\x1b]0;OWNED\\x07 ok \ufffd")
    assert "\x1b" not in outcome.content
    probe_made = log.find("GET", "/escape")
    assert probe_made is not None
    assert probe_made.body == "\x1b]0;OWNED\x07 ok \ufffd"


def test_a_reply_with_no_content_type_records_an_empty_one(service: StandInService) -> None:
    log = ProbeLog()

    outcome = probe(service, "/bare", log=log)

    assert outcome.content == "HTTP 200\n\nok"
    probe_made = log.find("GET", "/bare")
    assert probe_made is not None
    assert probe_made.content_type == ""


def test_a_body_too_large_to_record_is_a_tool_error_and_nothing_is_recorded(
    service: StandInService,
) -> None:
    log = ProbeLog()

    outcome = probe(service, "/too-large", log=log)

    assert outcome.is_error
    assert f"over {RECORDED_BODY_LIMIT_BYTES} bytes" in outcome.content
    assert log.find("GET", "/too-large") is None


def test_a_service_that_does_not_answer_in_time_is_a_tool_error(
    service: StandInService, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(http_tool, "PROBE_TIMEOUT_S", 0.2)
    log = ProbeLog()

    outcome = probe(service, "/slow", log=log)

    assert outcome.is_error
    assert "GET /slow got no response" in outcome.content
    assert log.find("GET", "/slow") is None


@pytest.mark.parametrize("target", ["/drip-body", "/drip-headers"])
def test_a_service_that_drips_its_reply_is_stopped_at_the_deadline(
    service: StandInService, monkeypatch: pytest.MonkeyPatch, target: str
) -> None:
    monkeypatch.setattr(http_tool, "PROBE_TIMEOUT_S", 0.3)
    log = ProbeLog()
    started_at = time.monotonic()

    outcome = probe(service, target, log=log)

    assert time.monotonic() - started_at < 1
    assert outcome.is_error
    assert f"GET {target} got no response" in outcome.content
    assert log.find("GET", target) is None


def test_a_service_that_hangs_up_without_a_reply_is_a_tool_error(service: StandInService) -> None:
    outcome = probe(service, "/hang-up")

    assert outcome.is_error
    assert "GET /hang-up got no response" in outcome.content


def test_a_failure_with_no_message_is_named_by_its_kind(
    service: StandInService, monkeypatch: pytest.MonkeyPatch
) -> None:
    def reset_with_no_message(connection: http.client.HTTPConnection) -> None:
        raise ConnectionResetError()

    monkeypatch.setattr(http.client.HTTPConnection, "getresponse", reset_with_no_message)

    outcome = probe(service, "/items?page=2")

    assert outcome == ToolOutcome(
        content="GET /items?page=2 got no response: ConnectionResetError.", is_error=True
    )


def test_a_service_that_is_not_listening_is_a_tool_error() -> None:
    tool = http_probe_tool(a_closed_port(), ProbeLog())

    outcome = tool.handler({"method": "GET", "path_and_query": "/"})

    assert outcome.is_error
    assert "GET / got no response" in outcome.content


def test_a_failed_request_is_an_arde_error_that_names_the_next_action() -> None:
    with pytest.raises(ProbeFailed) as failed:
        send_request(a_closed_port(), HttpRequest(method="GET", path_and_query="/"))

    assert isinstance(failed.value, ServiceError)
    assert isinstance(failed.value, ArdeError)
    assert "service.log" in failed.value.next_action


def test_send_request_and_the_local_client_return_what_the_service_sent(
    service: StandInService,
) -> None:
    request = HttpRequest(method="GET", path_and_query="/frozen")
    sent = ProbeRecord(
        kind="probe",
        method="GET",
        path_and_query="/frozen",
        status=405,
        content_type="text/plain; charset=utf-8",
        body="method not allowed\n",
    )

    assert send_request(service.handle, request) == sent
    assert local_service_client().send_request(service.handle, request) == sent


def test_the_probe_tool_is_strict_and_offers_get_and_head_alone(service: StandInService) -> None:
    definition = http_probe_tool(service.handle, ProbeLog()).definition

    assert (definition.name, definition.strict) == ("http_probe", True)
    assert definition.input_schema["additionalProperties"] is False
    properties = definition.input_schema["properties"]
    assert isinstance(properties, dict)
    assert properties["method"]["enum"] == ["GET", "HEAD"]

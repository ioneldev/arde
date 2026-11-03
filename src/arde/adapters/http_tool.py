"""Requests to a running service: the http_probe tool, and the client checks use.

A model gives a method and a path with its query, and code sends it to the service's own address,
so no input can name another host. http.client sends the path as written, so + and %2B arrive as
the model wrote them, and it never follows a redirect.
"""

import http.client
from dataclasses import dataclass
from urllib.parse import urlsplit

from arde.adapters.repo_tools import clean_repo_text
from arde.application.ports import (
    HttpRequest,
    ProbeFailed,
    ServiceClient,
    ServiceHandle,
    Tool,
    ToolDef,
    ToolOutcome,
)
from arde.application.probe_log import ProbeLog
from arde.application.tool_schema import strict_tool_schema
from arde.domain.facts import ProbeRecord

# Each wait on the socket, not the whole request: http.client has no overall deadline.
PROBE_TIMEOUT_S: float = 10
# A recorded body is kept whole, so a larger one is refused rather than cut.
RECORDED_BODY_LIMIT_BYTES = 1024 * 1024
# How much of a body the model sees. The probe log keeps all of it.
SHOWN_BODY_LIMIT_CHARS = 20_000
# What a model needs to read a status: what a 405 allows, and where a redirect points.
_SHOWN_HEADERS = ("Content-Type", "Allow", "Location")

HTTP_PROBE = ToolDef(
    name="http_probe",
    description=(
        "Sends one GET or HEAD request to the running service and shows the status, the "
        "Content-Type, Allow and Location headers, and the body. Give the path and query alone, "
        "such as /items?page=2, because the address is fixed, and percent-encode any space or "
        f"character outside ASCII. A redirect is shown, not followed. Shows at most the first "
        f"{SHOWN_BODY_LIMIT_CHARS} characters of a body."
    ),
    input_schema=strict_tool_schema(HttpRequest),
    strict=True,
)


@dataclass(frozen=True)
class _Reply:
    record: ProbeRecord
    # The shown headers the service sent, in the order of _SHOWN_HEADERS.
    headers: tuple[tuple[str, str], ...]


def send_request(service: ServiceHandle, request: HttpRequest) -> ProbeRecord:
    return _exchange(service, request).record


def http_probe_tool(service: ServiceHandle, log: ProbeLog) -> Tool:
    def probe(tool_input: dict[str, object]) -> ToolOutcome:
        # A ValidationError goes up to the agent loop, which sends it back to the model.
        request = HttpRequest.model_validate(tool_input)
        try:
            reply = _exchange(service, request)
        except ProbeFailed as failure:
            return ToolOutcome(content=clean_repo_text(str(failure)), is_error=True)
        log.record(reply.record)
        return ToolOutcome(content=clean_repo_text(_shown(reply)))

    return Tool(definition=HTTP_PROBE, handler=probe)


class LocalServiceClient(ServiceClient):
    def send_request(self, service: ServiceHandle, request: HttpRequest) -> ProbeRecord:
        return send_request(service, request)


# The return type is the static proof that LocalServiceClient satisfies the port.
def local_service_client() -> ServiceClient:
    return LocalServiceClient()


def _exchange(service: ServiceHandle, request: HttpRequest) -> _Reply:
    address = urlsplit(service.base_url)
    connection = http.client.HTTPConnection(address.netloc, timeout=PROBE_TIMEOUT_S)
    try:
        connection.request(request.method, address.path.rstrip("/") + request.path_and_query)
        response = connection.getresponse()
        body = response.read(RECORDED_BODY_LIMIT_BYTES + 1)
    except (OSError, http.client.HTTPException) as error:
        # A bare TimeoutError or ConnectionResetError has no message, only its kind.
        reason = str(error) or type(error).__name__
        raise _failed(request, f"got no response: {reason}") from error
    finally:
        connection.close()
    if len(body) > RECORDED_BODY_LIMIT_BYTES:
        raise _failed(
            request,
            f"answered {response.status} with a body over {RECORDED_BODY_LIMIT_BYTES} bytes, "
            "which ARDE does not record. Ask for less at a time",
        )
    record = ProbeRecord(
        kind="probe",
        method=request.method,
        path_and_query=request.path_and_query,
        status=response.status,
        content_type=response.getheader("Content-Type", ""),
        body=body.decode("utf-8", errors="replace"),
    )
    headers = tuple(
        (name, value) for name in _SHOWN_HEADERS if (value := response.getheader(name)) is not None
    )
    return _Reply(record=record, headers=headers)


def _failed(request: HttpRequest, what_happened: str) -> ProbeFailed:
    return ProbeFailed(
        f"{request.method} {request.path_and_query} {what_happened}.",
        next_action="Read service.log to see what the service did with the request",
    )


def _shown(reply: _Reply) -> str:
    headers = (f"{name}: {value}" for name, value in reply.headers)
    head = "\n".join([f"HTTP {reply.record.status}", *headers])
    body = reply.record.body
    if not body:
        return f"{head}\n\n(no body)"
    if len(body) <= SHOWN_BODY_LIMIT_CHARS:
        return f"{head}\n\n{body}"
    return (
        f"{head}\n\n{body[:SHOWN_BODY_LIMIT_CHARS]}\n[ARDE showed the first "
        f"{SHOWN_BODY_LIMIT_CHARS} of the {len(body)} characters of this body.]"
    )

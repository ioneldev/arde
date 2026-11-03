"""The output copy behaves as the input did.

No single comparison is enough: measured on a real service, the repo's own tests passed on two
changes of behaviour, the comparison of code caught both, and the same requests sent before and
after caught one, and only because one request asked for the exact limit that moved. So Behaviour
runs all three, and the language's allow-list of lines besides, which sees the directives the
comparison of code cannot.
"""

from collections.abc import Iterator, Sequence
from pathlib import Path

from pydantic import ValidationError

from arde.application.checks_runner import CheckContext, ServicePair
from arde.application.ports import (
    CheckResult,
    EditCheckRequest,
    HttpRequest,
    LanguageAdapter,
    ProbeFailed,
    RenderManifest,
    ServiceClient,
    ServiceHandle,
)
from arde.domain.base import one_line
from arde.domain.facts import ProbeRecord, Wiki
from arde.domain.findings import Finding
from arde.domain.inventory import Inventory

BEHAVIOUR = "behaviour"
_SHOWN_CONTENT_TYPE_LENGTH = 100
_REPLY_CHANGED = (
    "This is a bug in ARDE: its edits changed what the service answers. Report it with the "
    "request named."
)

type _Reply = ProbeRecord | ProbeFailed


class Behaviour:
    name = BEHAVIOUR

    # last_run_manifest is the one the render was given: the lines it was free to take out.
    def __init__(
        self,
        adapter: LanguageAdapter,
        client: ServiceClient,
        *,
        last_run_manifest: RenderManifest,
    ) -> None:
        self._adapter = adapter
        self._client = client
        self._last_run_manifest = last_run_manifest

    def run(self, ctx: CheckContext) -> list[Finding]:
        edits = EditCheckRequest(
            before=ctx.input_repo,
            after=ctx.output_repo,
            render=ctx.render,
            manifest=self._last_run_manifest,
        )
        requests = requests_to_compare(ctx.wiki, ctx.inventory)
        return [
            *self._adapter.check_agent_edits(edits),
            *_failed_checks(
                self._adapter.run_checks(ctx.output_repo, ctx.profile), ctx.output_repo
            ),
            *compare_responses(ctx.services, requests, client=self._client),
        ]


def requests_to_compare(wiki: Wiki, inventory: Inventory) -> list[HttpRequest]:
    """Every request an agent recorded, then a GET for each route with no placeholder, each once.

    A path with a placeholder, such as /items/{sku}, names no one thing to ask for, so only the
    requests agents made reach those routes.
    """
    recorded = [
        HttpRequest.model_validate({"method": probe.method, "path_and_query": probe.path_and_query})
        for probe in wiki.probes
    ]
    plain = [_get(route.path) for route in inventory.routes if "{" not in route.path]
    every_request = [*recorded, *(request for request in plain if request is not None)]
    return list(dict.fromkeys(every_request))


def compare_responses(
    pair: ServicePair, requests: Sequence[HttpRequest], *, client: ServiceClient
) -> list[Finding]:
    """A finding for each request that the two services answer differently.

    The status, the Content-Type and the whole body must match. A request that neither answers is
    no difference.
    """
    findings: list[Finding] = []
    for request in requests:
        difference = _difference(
            _reply(client, pair.before, request), _reply(client, pair.after, request)
        )
        if difference is not None:
            shown = f"{request.method} {request.path_and_query}"
            findings.append(
                Finding(
                    check=BEHAVIOUR,
                    owner="code",
                    subject=shown,
                    message=f"{shown} {difference}",
                    next_action=_REPLY_CHANGED,
                )
            )
    return findings


# A route's path comes from the repo, and a request carries printable ASCII alone, with no space,
# so a path that would need escaping is left to the requests agents made.
def _get(path: str) -> HttpRequest | None:
    try:
        return HttpRequest(method="GET", path_and_query=path)
    except ValidationError:
        return None


def _reply(client: ServiceClient, service: ServiceHandle, request: HttpRequest) -> _Reply:
    try:
        return client.send_request(service, request)
    except ProbeFailed as failure:
        return failure


def _difference(before: _Reply, after: _Reply) -> str | None:
    if isinstance(before, ProbeFailed) or isinstance(after, ProbeFailed):
        return _difference_in_answering(before, after)
    if before.status != after.status:
        return f"was answered {before.status} before ARDE's edits and {after.status} after."
    if before.content_type != after.content_type:
        return (
            f"was answered with Content-Type {_shown(before.content_type)} before ARDE's edits "
            f"and {_shown(after.content_type)} after."
        )
    if before.body != after.body:
        return (
            f"was answered {after.status} with another body after ARDE's edits: "
            f"{len(before.body)} characters before, {len(after.body)} after."
        )
    return None


# Called when at least one side gave no usable reply.
def _difference_in_answering(before: _Reply, after: _Reply) -> str | None:
    if isinstance(before, ProbeRecord):
        return f"was answered {before.status} before ARDE's edits, and not after: {after}"
    if isinstance(after, ProbeRecord):
        return f"was not answered before ARDE's edits, and {after.status} after: {before}"
    return None


def _shown(content_type: str) -> str:
    return one_line(content_type, _SHOWN_CONTENT_TYPE_LENGTH) or "(none)"


_BROKEN = {
    "build": "The output copy no longer builds.",
    "vet": "The output copy no longer passes vet.",
    "tests": "The output copy no longer passes its tests.",
}


def _failed_checks(result: CheckResult, output_repo: Path) -> Iterator[Finding]:
    passed = {"build": result.build_ok, "vet": result.vet_ok, "tests": result.test_ok}
    for step, message in _BROKEN.items():
        if not passed[step]:
            yield Finding(
                check=BEHAVIOUR,
                owner="code",
                subject=step,
                message=message,
                next_action=(
                    f"This is a bug in ARDE: its edits broke the repo. Run the {step} in "
                    f"{output_repo} to see why, and report it."
                ),
            )

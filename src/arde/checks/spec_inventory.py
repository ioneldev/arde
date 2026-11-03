"""The spec and the wiki against the inventory, both ways.

Every route and param the spec names, and every field and error code the wiki names, must be one
the code holds. Every route the code registers must reach the spec, and every error code it sends
must reach the wiki. The service ignores a query param it does not read, so only this comparison
can find an invented one.
"""

from collections.abc import Iterable, Iterator, Sequence

from arde.application.checks_runner import CheckContext
from arde.checks.spec_operations import Operation, SpecParam, operations, read_spec
from arde.domain.facts import EndpointFact, FieldFact, PolymorphicFact, UnreachableFact, Wiki
from arde.domain.findings import Finding, Owner
from arde.domain.inventory import Inventory, Route

CHECK = "spec_matches_inventory"
# The method of a route that answers every verb. A fact never holds it: Explore writes one fact
# per verb it finds on the path.
ANY_METHOD = "ANY"


class SpecMatchesInventory:
    name = CHECK

    def run(self, ctx: CheckContext) -> list[Finding]:
        spec_operations = operations(read_spec(ctx.spec_path))
        found = [
            *_invented_routes(spec_operations, ctx.inventory.routes),
            *_missing_routes(ctx.inventory.routes, spec=spec_operations, wiki=ctx.wiki),
            *_invented_params(spec_operations, ctx.inventory),
            *_invented_fields(ctx.wiki, ctx.inventory),
            *_missing_error_codes(ctx.inventory, ctx.wiki),
            *_invented_error_codes(ctx.wiki, ctx.inventory),
        ]
        return sorted(set(found), key=lambda finding: (finding.subject, finding.message))


def _invented_routes(spec: Iterable[Operation], routes: Sequence[Route]) -> Iterator[Finding]:
    for operation in spec:
        if not any(
            _serves(route, method=operation.method, path=operation.path) for route in routes
        ):
            yield _finding(
                "explore",
                subject=operation.key,
                message=f"The spec documents {operation.key}, which the code does not register.",
                next_action=(
                    "Remove the endpoint fact for that route, or give it the route the code "
                    "registers, then run again."
                ),
            )


# A route with no function to carry an annotation gets facts and docs and no operation, so its
# absence from the spec is a known gap and not a finding.
def _missing_routes(
    routes: Iterable[Route], *, spec: Sequence[Operation], wiki: Wiki
) -> Iterator[Finding]:
    for route in routes:
        if route.handler_id is None or any(
            _serves(route, method=operation.method, path=operation.path) for operation in spec
        ):
            continue
        if _has_endpoint_fact(wiki, route):
            yield _lost_from_the_spec(route)
        else:
            yield _finding(
                "explore",
                subject=route.key,
                message=(
                    f"The code registers {route.key} at {route.file}:{route.line}, and the spec "
                    "has no operation for it."
                ),
                next_action="Probe the route and add an endpoint fact for it, then run again.",
            )


# The fact is there and its annotation did not reach the spec, which no agent can repair.
def _lost_from_the_spec(route: Route) -> Finding:
    handler = f"{route.handler_file}:{route.handler_line}"
    return _finding(
        "code",
        subject=route.key,
        message=(
            f"The wiki has a fact for {route.key}, registered at {route.file}:{route.line}, but "
            f"the spec has no operation for it, so the annotation above {handler} never reached "
            "the spec."
        ),
        next_action=(
            f"Find out why the spec generator does not read {route.handler_file}, such as a "
            "folder it skips by its name, then move the handler or report it, and run again."
        ),
    )


def _invented_params(spec: Iterable[Operation], inventory: Inventory) -> Iterator[Finding]:
    read_by_the_code = {param.name for param in inventory.params}
    for operation in spec:
        for param in operation.params:
            if param.location == "path":
                if not _in_the_path(param, operation.path):
                    yield _invented_param(
                        operation, param, why=f"which the path {operation.path} does not hold"
                    )
            elif param.name not in read_by_the_code:
                yield _invented_param(
                    operation,
                    param,
                    why=(
                        "which the code never reads. The service ignores a param it does not "
                        "read, so no request can show this"
                    ),
                )


# A wildcard that takes the rest of the path is written {name...}.
def _in_the_path(param: SpecParam, path: str) -> bool:
    return f"{{{param.name}}}" in path or f"{{{param.name}...}}" in path


def _invented_param(operation: Operation, param: SpecParam, *, why: str) -> Finding:
    return _finding(
        "explore",
        subject=f"{operation.key} {param.location} param {param.name}",
        message=f"The spec gives {operation.key} a {param.location} param {param.name}, {why}.",
        next_action=(
            "Remove the param from the endpoint fact, or give it the name the code reads, then "
            "run again."
        ),
    )


def _invented_fields(wiki: Wiki, inventory: Inventory) -> Iterator[Finding]:
    held = {(field.struct, field.name) for field in inventory.response_fields}
    for fact in wiki.facts:
        if isinstance(fact, FieldFact | PolymorphicFact) and (fact.struct, fact.field) not in held:
            yield _finding(
                "explore",
                subject=f"field {fact.struct}.{fact.field}",
                message=(
                    f"The wiki fact {fact.id} describes the field {fact.field} of {fact.struct}, "
                    "which no response struct of the code holds."
                ),
                next_action=(
                    "Remove the fact, or give it a struct and field the code declares, then run "
                    "again."
                ),
            )


def _missing_error_codes(inventory: Inventory, wiki: Wiki) -> Iterator[Finding]:
    named = _error_codes_named(wiki)
    for code in inventory.error_codes:
        if code.code not in named:
            yield _finding(
                "explore",
                subject=f"error code {code.code}",
                message=(
                    f"The code answers with the error code {code.code} ({code.status}), and no "
                    "fact in the wiki names it."
                ),
                next_action=(
                    "Probe a request that gets it and add it to the endpoint fact's responses, or "
                    "add an unreachable fact that quotes the code showing why no request can, "
                    "then run again."
                ),
            )


def _invented_error_codes(wiki: Wiki, inventory: Inventory) -> Iterator[Finding]:
    sent = {code.code for code in inventory.error_codes}
    for code in sorted(_error_codes_named(wiki) - sent):
        yield _finding(
            "explore",
            subject=f"error code {code}",
            message=f"The wiki names the error code {code}, which the code never sends.",
            next_action=(
                "Remove the code from the fact, or name one the code sends, then run again."
            ),
        )


def _error_codes_named(wiki: Wiki) -> set[str]:
    named: set[str] = set()
    for fact in wiki.facts:
        if isinstance(fact, EndpointFact):
            named.update(code for response in fact.responses for code in response.error_codes)
        elif isinstance(fact, UnreachableFact):
            named.add(fact.error_code)
    return named


def _has_endpoint_fact(wiki: Wiki, route: Route) -> bool:
    for fact in wiki.facts:
        if isinstance(fact, EndpointFact):
            method, path = fact.route.split(" ", 1)
            if _serves(route, method=method, path=path):
                return True
    return False


def _serves(route: Route, *, method: str, path: str) -> bool:
    return route.path == path and route.method in (method, ANY_METHOD)


def _finding(owner: Owner, *, subject: str, message: str, next_action: str) -> Finding:
    return Finding(
        check=CHECK, owner=owner, subject=subject, message=message, next_action=next_action
    )

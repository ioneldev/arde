"""Code's checks on the facts the explore agent submits, and on whether they cover the inventory.

A fact is accepted only with real evidence: a quote of code that is in the file, or a citation of
a probe made in this run. Every name it holds must be the inventory's, and every link must point
at a declaration, route or file that exists. Facts become annotations and docs, so this is where
the grounding of both is enforced.

Each problem is one line for the model to act on. A line holds names, which the model or the
inventory gave and the types keep to one short line, and never a quote or a reply, because text
read from the repo could hold instructions.
"""

import os
import re
from collections import Counter
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated

from pydantic import Field

from arde.application.fact_names import (
    ANY_METHOD,
    PARAM_TEXT,
    FactNames,
    build_files,
    can_hold,
    fact_routes,
    field_is_writable,
    path_wildcards,
    route_is_writable,
    struct_declaration,
)
from arde.application.ports import LanguageAdapter, SourceQuote
from arde.application.probe_log import ProbeLog
from arde.domain.base import Frozen, SingleLine, printable_name
from arde.domain.facts import (
    CodeQuote,
    EndpointFact,
    Fact,
    FieldFact,
    PolymorphicFact,
    ProbeCitation,
    ProbeRecord,
    ResponseFact,
    UnreachableFact,
    Wiki,
)
from arde.domain.inventory import Inventory, ResponseField, Route

# Far past what the agent's tools show of a file, so every quote the agent could have read is in.
QUOTED_BYTES_LIMIT = 1024 * 1024

GapNote = Annotated[SingleLine, Field(max_length=500)]
ERROR_STATUS_FLOOR = 400


class FactBatch(Frozen):
    """One call to add_facts: facts to add, or to replace by id, and ids of facts to delete."""

    upserts: tuple[Fact, ...] = ()
    deletes: tuple[str, ...] = ()
    # What the agent saw in the code and the inventory does not list, such as a route.
    suspected_gaps: tuple[GapNote, ...] = ()


# A dataclass, because it holds a probe log and an adapter, which are not data to validate.
@dataclass(frozen=True)
class FactContext:
    repo: Path
    inventory: Inventory
    # The probes made in this run, the only ones a fact submitted now may cite.
    probes: ProbeLog
    # The wiki's facts before this batch, by id.
    known_facts: Mapping[str, Fact]
    # Reads a quote with its file's syntax.
    language: LanguageAdapter


def validate_batch(batch: FactBatch, ctx: FactContext) -> list[str]:
    """Every problem with the batch, one line each. Empty means accepted."""
    check = _Check.of(batch, ctx)
    problems = list(_batch_problems(batch, ctx))
    for fact in batch.upserts:
        problems.extend(f"{fact.id}: {problem}" for problem in check.problems_of(fact))
    return problems


def quoted_places(source: str, quote: str) -> tuple[tuple[int, int], ...]:
    """Each place the quote appears in the source, overlapping ones included.

    Each run of whitespace in either counts as one space, because a model copying code turns tabs
    into spaces and drops indentation. Everything else must match exactly.
    """
    words = quote.split()
    if not words:
        return ()
    anywhere = re.compile("(?=(" + r"\s+".join(re.escape(word) for word in words) + "))")
    return tuple(found.span(1) for found in anywhere.finditer(source))


def repo_file(repo: Path, file: str) -> Path | None:
    """The regular file a path from the repo root names, or None when there is none inside it.

    Links are followed first, so a link that leads out of the repo names nothing.
    """
    real_repo = Path(os.path.realpath(repo))
    real_path = Path(os.path.realpath(repo / file))
    if real_path.is_relative_to(real_repo) and os.path.isfile(real_path):
        return real_path
    return None


# A root that cannot be listed has no files, so nothing found there can count.
def root_files(repo: Path) -> frozenset[str]:
    """The names of the regular files at the repo root, links followed."""
    try:
        with os.scandir(repo) as entries:
            return frozenset(entry.name for entry in entries if entry.is_file())
    except OSError:
        return frozenset()


def read_repo_source(repo: Path, file: str) -> str | None:
    path = repo_file(repo, file)
    if path is None:
        return None
    try:
        with path.open("rb") as handle:
            data = handle.read(QUOTED_BYTES_LIMIT)
    except OSError:
        return None
    # As read_file shows it: bad bytes replaced, and each line ended by LF alone.
    return data.decode("utf-8", errors="replace").replace("\r\n", "\n")


def _batch_problems(batch: FactBatch, ctx: FactContext) -> Iterator[str]:
    upserted = Counter(fact.id for fact in batch.upserts)
    for fact_id, count in upserted.items():
        if count > 1:
            yield (
                f"fact id {fact_id} appears {count} times in this batch: give each fact its own "
                "id, because each fact has one home"
            )
    for fact_id in batch.deletes:
        if fact_id not in ctx.known_facts:
            yield f"deletes {fact_id}, which is no fact of the wiki: delete only ids it holds"
    for fact_id in sorted(set(batch.deletes) & upserted.keys()):
        yield f"both deletes and upserts {fact_id}: do one or the other"


@dataclass
class _Check:
    ctx: FactContext
    names: FactNames
    # The error codes an unreachable fact exempts once the batch is applied.
    unreachable: frozenset[str]
    build_files: frozenset[str]
    root_files: frozenset[str]
    sources: dict[str, str | None] = field(default_factory=dict)

    @classmethod
    def of(cls, batch: FactBatch, ctx: FactContext) -> "_Check":
        after = {
            fact_id: fact
            for fact_id, fact in ctx.known_facts.items()
            if fact_id not in batch.deletes
        }
        after.update((fact.id, fact) for fact in batch.upserts)
        unreachable = frozenset(
            fact.error_code for fact in after.values() if isinstance(fact, UnreachableFact)
        )
        return cls(
            ctx=ctx,
            names=FactNames.of(ctx.inventory),
            unreachable=unreachable,
            build_files=build_files(ctx.inventory),
            root_files=root_files(ctx.repo),
        )

    def problems_of(self, fact: Fact) -> Iterator[str]:
        yield from self._evidence_problems(fact)
        yield from self._link_problems(fact)
        if isinstance(fact, EndpointFact):
            yield from self._endpoint_problems(fact)
        elif isinstance(fact, FieldFact | PolymorphicFact):
            yield from self._field_problems(fact)
        elif isinstance(fact, UnreachableFact):
            yield from self._unreachable_problems(fact)

    # Evidence

    def _evidence_problems(self, fact: Fact) -> Iterator[str]:
        if not fact.evidence:
            yield "has no evidence: add a quote of code, or cite a probe made in this run"
        for number, evidence in enumerate(fact.evidence, start=1):
            if isinstance(evidence, CodeQuote):
                problem = self._quote_problem(evidence)
            else:
                problem = self._citation_problem(evidence)
            if problem is not None:
                yield f"evidence {number} {problem}"

    def _quote_problem(self, quote: CodeQuote) -> str | None:
        file = printable_name(quote.file)
        if not quote.quote.strip():
            return "quotes nothing: quote the code the fact rests on"
        source = self._source(quote.file)
        if source is None:
            return (
                f"quotes {file}, which is not a file inside the repo: name a file by its path "
                "from the repo root, as list_files shows it"
            )
        places = quoted_places(source, quote.quote)
        if not places:
            return (
                f"quotes text that is not in {file}: copy it as read_file shows it, without the "
                "line numbers"
            )
        content = self.ctx.language.quote_content(
            SourceQuote(
                file=quote.file,
                source=source,
                places=places,
                build_files=self.build_files,
                root_files=self.root_files,
            )
        )
        match content:
            case "comment":
                return (
                    f"quotes {file} where it is only comment text, which is not evidence: quote "
                    "the code itself, with the comment beside it if it helps"
                )
            case "string literal":
                return (
                    f"quotes {file} where it is only the text of one string literal, which is "
                    "not evidence: quote the code that uses it"
                )
            case "not source":
                return (
                    f"quotes {file}, which is not source code ARDE reads, so it is not evidence: "
                    "quote source code, or cite a probe"
                )
            case "outside the build":
                return (
                    f"quotes {file}, which is not part of the service's build, so it is not "
                    "evidence about the service: quote a file the service is built from"
                )
            case "not a path in the repo":
                return (
                    f"quotes {file}, which is not a path in the repo as list_files writes it: "
                    "name the file from the repo root, with / between folders and no ./, // or .."
                )
        return None

    def _citation_problem(self, citation: ProbeCitation) -> str | None:
        if self._recorded(citation) is not None:
            return None
        request = printable_name(f"{citation.method} {citation.path_and_query}")
        return (
            f"cites {request}, which no probe made in this run: call http_probe with it first, "
            "then cite it exactly as it was sent"
        )

    def _recorded(self, citation: ProbeCitation) -> ProbeRecord | None:
        return self.ctx.probes.find(citation.method, citation.path_and_query)

    def _source(self, file: str) -> str | None:
        if file not in self.sources:
            self.sources[file] = read_repo_source(self.ctx.repo, file)
        return self.sources[file]

    # Links

    def _link_problems(self, fact: Fact) -> Iterator[str]:
        links = fact.links
        if not (links.declarations or links.routes or links.files):
            yield (
                "has no links: link the declarations, routes or files it depends on, so that it "
                "goes stale when they change"
            )
        declared = set(self.ctx.inventory.declarations)
        for declaration in links.declarations:
            if declaration not in declared:
                yield (
                    f"links declaration {printable_name(declaration)}, which is not a declaration "
                    "of the service: use an id from the declarations list, as file::kind name"
                )
        routes = {route.key for route in self.ctx.inventory.routes}
        for route in links.routes:
            if route not in routes:
                yield (
                    f"links route {printable_name(route)}, which is not a route of the service: "
                    "use a route as the inventory lists it"
                )
        for file in links.files:
            if repo_file(self.ctx.repo, file) is None:
                yield f"links file {printable_name(file)}, which is not a file inside the repo"

    # Endpoints

    def _endpoint_problems(self, fact: EndpointFact) -> Iterator[str]:
        method, path = fact.route.split(" ", 1)
        route = self._served_route(fact.route)
        if method == ANY_METHOD:
            yield (
                f"describes route {fact.route}, but a fact names a concrete method, never ANY: "
                "probe the path to see which methods it serves, then write a fact for each"
            )
        elif route is None:
            yield (
                f"describes route {fact.route}, which the service does not serve: describe only "
                "routes the schema lists"
            )
        elif route.key not in fact.links.routes:
            yield (
                f"does not link route {route.key}: add it to links.routes, so that the fact goes "
                "stale when the route changes"
            )
        yield from self._param_problems(fact, path=path)
        yield from self._response_problems(fact)

    def _served_route(self, fact_route: str) -> Route | None:
        if fact_route not in self.names.routes:
            return None
        serving = [route for route in self.ctx.inventory.routes if fact_route in fact_routes(route)]
        exact = [route for route in serving if route.key == fact_route]
        return (exact or serving)[0]

    def _param_problems(self, fact: EndpointFact, *, path: str) -> Iterator[str]:
        wildcards = path_wildcards(path)
        read = {param.name for param in self.ctx.inventory.params}
        for param in fact.params:
            if param.location != "path":
                if param.name not in read:
                    yield (
                        f"param {param.name} ({param.location}) is not a param the service "
                        "reads: describe only params the inventory lists"
                    )
            elif param.name not in wildcards:
                yield (
                    f"path param {param.name} is not part of the path {path}: a path param "
                    "names a part of the path in braces"
                )
            elif not param.required:
                yield (
                    f"path param {param.name} must be required, because a request cannot leave "
                    "out part of its path"
                )
        described = {param.name for param in fact.params if param.location == "path"}
        for name in wildcards:
            if name not in described:
                yield (
                    f"the path {path} holds {{{name}}}, and no path param describes it: add a "
                    f"path param named {name}"
                )

    # A code quote backs no status: one quote anywhere in the fact would back any status at all.
    def _response_problems(self, fact: EndpointFact) -> Iterator[str]:
        cited = [
            record
            for evidence in fact.evidence
            if isinstance(evidence, ProbeCitation)
            and (record := self._recorded(evidence)) is not None
        ]
        for response in fact.responses:
            status = response.status
            shown = [record for record in cited if record.status == status]
            if not shown and not self._sent_with_a_listed_code(response):
                yield _unbacked_status(status)
            for code in response.error_codes:
                yield from self._error_code_problems(code, status=status, shown=shown)
            if not self._names_a_reply_struct(response.declared_type, bare_is_known=True):
                yield (
                    f"response {status} has type {response.declared_type}, which names no struct "
                    "the service replies with: name one of the inventory's structs, with its "
                    "package"
                )

    # A status no request reaches, such as a 500, can be shown by no probe. The code names the
    # status it sends with each error code, so that status backs a response that lists the code.
    def _sent_with_a_listed_code(self, response: ResponseFact) -> bool:
        return response.status >= ERROR_STATUS_FLOOR and any(
            error.code in response.error_codes
            and self.ctx.language.http_status(error.status) == response.status
            for error in self.ctx.inventory.error_codes
        )

    def _error_code_problems(
        self, code: str, *, status: int, shown: list[ProbeRecord]
    ) -> Iterator[str]:
        if code not in self.names.error_codes:
            yield (
                f"response {status} lists error code {code}, which the service does not have: "
                "list only error codes of the inventory"
            )
        elif code not in self.unreachable and not any(
            holds_code(record.body, code) for record in shown
        ):
            yield (
                f"response {status} lists error code {code}, and no cited probe returned "
                f"{status} with {code} in its body: cite such a probe, or add an unreachable "
                f"fact for {code} in this batch or an earlier one"
            )

    # A type with no package may be a type of the language, which no inventory lists.
    def _names_a_reply_struct(self, type_name: str, *, bare_is_known: bool) -> bool:
        package, _, struct = type_name.lstrip("[]*").rpartition(".")
        if not package and bare_is_known:
            return True
        return any(
            reply.struct == struct and package in ("", reply.package)
            for reply in self.ctx.inventory.response_fields
        )

    # Fields

    def _field_problems(self, fact: FieldFact | PolymorphicFact) -> Iterator[str]:
        named = _fields_named(self.ctx.inventory, struct=fact.struct, field=fact.field)
        if not named:
            yield (
                f"describes field {fact.struct}.{fact.field}, which is no field of a struct the "
                "service replies with: describe only fields the inventory lists"
            )
        elif len(named) > 1 and _linked_struct(fact, named) is None:
            choices = ", ".join(struct_declaration(reply) for reply in named)
            yield (
                f"describes field {fact.struct}.{fact.field}, which {len(named)} structs of that "
                f"name hold: link exactly one of {choices}"
            )
        if isinstance(fact, PolymorphicFact):
            for variant in fact.variants:
                if not self._names_a_reply_struct(variant, bare_is_known=False):
                    yield f"variant {variant} names no struct the service replies with"

    # Unreachable error codes

    def _unreachable_problems(self, fact: UnreachableFact) -> Iterator[str]:
        code = printable_name(fact.error_code)
        if fact.error_code not in self.names.error_codes:
            yield f"says error code {code} cannot be reached, and the service has no such code"
        if not any(isinstance(evidence, CodeQuote) for evidence in fact.evidence):
            yield (
                f"says error code {code} cannot be reached and quotes no code: quote the code "
                "that shows no request reaches it"
            )


def _unbacked_status(status: int) -> str:
    if status < ERROR_STATUS_FLOOR:
        return (
            f"response {status} is returned by no probe the fact cites: call http_probe with a "
            f"request that gets {status}, then cite that probe"
        )
    return (
        f"response {status} is returned by no probe the fact cites, and lists no error code the "
        f"code sends with {status}: cite a probe that returned {status}, or list the inventory's "
        f"error code that the code sends with {status}"
    )


# Bounded by characters that cannot be part of a code, so bad_pieces does not show bad.
def holds_code(body: str, code: str) -> bool:
    return re.search(rf"(?<![\w.-]){re.escape(code)}(?![\w.-])", body) is not None


def _fields_named(inventory: Inventory, *, struct: str, field: str) -> list[ResponseField]:
    return [
        reply
        for reply in inventory.response_fields
        if (reply.struct, reply.name) == (struct, field)
    ]


def _linked_struct(
    fact: FieldFact | PolymorphicFact, named: list[ResponseField]
) -> ResponseField | None:
    linked = [reply for reply in named if struct_declaration(reply) in fact.links.declarations]
    return linked[0] if len(linked) == 1 else None


# Coverage


def coverage_gaps(wiki: Wiki, inventory: Inventory) -> list[str]:
    """Each item of the inventory that no fact of the wiki covers, one line each.

    An item no fact can hold is left out, because no fact could ever cover it.
    """
    endpoints = [fact for fact in wiki.facts if isinstance(fact, EndpointFact)]
    described = {fact.route for fact in endpoints}
    params = {param.name for fact in endpoints for param in fact.params}
    exempt = {
        code for fact in endpoints for response in fact.responses for code in response.error_codes
    } | {fact.error_code for fact in wiki.facts if isinstance(fact, UnreachableFact)}
    field_facts = [fact for fact in wiki.facts if isinstance(fact, FieldFact)]
    return [
        *(
            f"route {route.key} has no endpoint fact"
            for route in inventory.routes
            if route_is_writable(route) and described.isdisjoint(fact_routes(route))
        ),
        *(
            f"param {param.name} is in no endpoint fact"
            for param in inventory.params
            if can_hold(PARAM_TEXT, param.name) and param.name not in params
        ),
        *(
            f"error code {error.code} is in no endpoint response and has no unreachable fact"
            for error in inventory.error_codes
            if error.code not in exempt
        ),
        *(
            f"field {reply.struct}.{reply.name} in {reply.file} has no field fact"
            for reply in inventory.response_fields
            if field_is_writable(reply) and not _is_described(reply, field_facts, inventory)
        ),
    ]


def _is_described(reply: ResponseField, facts: list[FieldFact], inventory: Inventory) -> bool:
    named = _fields_named(inventory, struct=reply.struct, field=reply.name)
    return any(
        (fact.struct, fact.field) == (reply.struct, reply.name)
        and (len(named) == 1 or _linked_struct(fact, named) == reply)
        for fact in facts
    )

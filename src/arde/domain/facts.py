"""The wiki: checked statements about one service, each with evidence and links."""

from collections import Counter
from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from arde.domain.base import (
    AttributeValue,
    CodeIdentifier,
    ErrorCodeName,
    Frozen,
    NonBlankLine,
    ParamName,
    QualifiedTypeName,
    RepoRelativePath,
    RoutePattern,
    SingleLine,
)

Topic = Literal[
    "overview",
    "architecture",
    "api",
    "business-rules",
    "errors",
    "configuration",
    "testing-and-shipping",
    "change-guide",
]


class CodeQuote(Frozen):
    kind: Literal["code"]
    file: RepoRelativePath
    # May span lines: code verifies the quote exists in the file as written.
    quote: str


class ProbeCitation(Frozen):
    """Points at a recorded probe. It holds no response, so the model never retypes one."""

    kind: Literal["probe"]
    method: str
    path_and_query: str


class ProbeRecord(Frozen):
    """What a probe really returned. Filled by code, never by a model."""

    kind: Literal["probe"]
    method: str
    path_and_query: str
    status: int
    content_type: str
    body: str


class Links(Frozen):
    declarations: tuple[str, ...] = ()
    routes: tuple[str, ...] = ()
    files: tuple[RepoRelativePath, ...] = ()


class FactBase(Frozen):
    id: str
    topic: Topic
    evidence: tuple[CodeQuote | ProbeCitation, ...]
    links: Links


class ParamFact(Frozen):
    name: ParamName
    location: Literal["path", "query", "header", "form"]
    declared_type: Literal["int", "number", "string", "bool"]
    required: bool
    description: NonBlankLine
    minimum: int | None
    maximum: int | None
    enum: tuple[AttributeValue, ...]
    default: AttributeValue | None


class ResponseFact(Frozen):
    status: int
    declared_type: QualifiedTypeName
    description: SingleLine
    error_codes: tuple[ErrorCodeName, ...]


class EndpointFact(FactBase):
    kind: Literal["endpoint"]
    route: RoutePattern
    summary: SingleLine
    params: tuple[ParamFact, ...]
    responses: tuple[ResponseFact, ...]


class FieldFact(FactBase):
    kind: Literal["field"]
    struct: CodeIdentifier
    field: CodeIdentifier
    description: SingleLine


class PolymorphicFact(FactBase):
    kind: Literal["polymorphic"]
    struct: CodeIdentifier
    field: CodeIdentifier
    variants: tuple[QualifiedTypeName, ...]


class RuleFact(FactBase):
    kind: Literal["rule"]
    statement: str


class UnreachableFact(FactBase):
    kind: Literal["unreachable"]
    error_code: str
    reason: str


Fact = Annotated[
    EndpointFact | FieldFact | PolymorphicFact | RuleFact | UnreachableFact,
    Field(discriminator="kind"),
]


class Wiki(Frozen):
    schema_version: Literal[1] = 1
    facts: tuple[Fact, ...]
    probes: tuple[ProbeRecord, ...] = ()

    # by_id would otherwise answer with whichever duplicate came first.
    @model_validator(mode="after")
    def _ids_are_unique(self) -> Self:
        counts = Counter(fact.id for fact in self.facts)
        repeated = sorted(fact_id for fact_id, count in counts.items() if count > 1)
        if repeated:
            raise ValueError(
                f"fact ids must be unique, repeated: {', '.join(repeated)}. "
                "Give each fact its own id"
            )
        return self

    # A citation with nothing behind it would claim evidence that no probe produced.
    @model_validator(mode="after")
    def _every_citation_resolves(self) -> Self:
        recorded = {(probe.method, probe.path_and_query) for probe in self.probes}
        citations = {
            (evidence.method, evidence.path_and_query)
            for fact in self.facts
            for evidence in fact.evidence
            if isinstance(evidence, ProbeCitation)
        }
        missing = sorted(citations - recorded)
        if missing:
            cited = ", ".join(f"{method} {path_and_query}" for method, path_and_query in missing)
            raise ValueError(f"no probe was recorded for: {cited}. Cite a probe that was made")
        return self

    def by_topic(self, topic: Topic) -> tuple[Fact, ...]:
        return tuple(fact for fact in self.facts if fact.topic == topic)

    def by_id(self, fact_id: str) -> Fact:
        for fact in self.facts:
            if fact.id == fact_id:
                return fact
        raise KeyError(fact_id)

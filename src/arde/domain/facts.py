"""The wiki: checked statements about one service, each with evidence and links."""

from collections import Counter
from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from arde.domain.base import (
    CodeIdentifier,
    ErrorCodeName,
    Frozen,
    ParamName,
    QualifiedTypeName,
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
    file: str
    # May span lines: code verifies the quote exists in the file as written.
    quote: str


class ProbeRecord(Frozen):
    kind: Literal["probe"]
    method: str
    path_and_query: str
    status: int
    content_type: str
    body: str


class Links(Frozen):
    declarations: tuple[str, ...] = ()
    routes: tuple[str, ...] = ()
    files: tuple[str, ...] = ()


class FactBase(Frozen):
    id: str
    topic: Topic
    evidence: tuple[CodeQuote | ProbeRecord, ...]
    links: Links


class ParamFact(Frozen):
    name: ParamName
    location: Literal["path", "query"]
    go_type: Literal["int", "string"]
    required: bool
    description: SingleLine
    minimum: int | None
    maximum: int | None
    enum: tuple[SingleLine, ...]
    default: SingleLine | None


class ResponseFact(Frozen):
    status: int
    go_type: QualifiedTypeName
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
    facts: tuple[Fact, ...]

    # by_id would otherwise answer with whichever duplicate came first.
    @model_validator(mode="after")
    def _ids_are_unique(self) -> Self:
        counts = Counter(fact.id for fact in self.facts)
        repeated = sorted(fact_id for fact_id, count in counts.items() if count > 1)
        if repeated:
            raise ValueError(f"fact ids must be unique, repeated: {', '.join(repeated)}")
        return self

    def by_topic(self, topic: Topic) -> tuple[Fact, ...]:
        return tuple(fact for fact in self.facts if fact.topic == topic)

    def by_id(self, fact_id: str) -> Fact:
        for fact in self.facts:
            if fact.id == fact_id:
                return fact
        raise KeyError(fact_id)

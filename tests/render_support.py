"""Facts about the shop fixture, and a render of them, for tests that need an annotated copy."""

import shutil
from collections.abc import Sequence
from pathlib import Path

from shop_support import SHOP, shop_profile

from arde.application.ports import LanguageAdapter, RenderManifest, RenderOutcome, RenderRequest
from arde.domain.facts import (
    CodeQuote,
    EndpointFact,
    Fact,
    FieldFact,
    Links,
    ParamFact,
    PolymorphicFact,
    ResponseFact,
    Wiki,
)

_QUOTE = CodeQuote(kind="code", file="web/handlers.go", quote="pieces := 1")
_QUOTE_REPLY = ResponseFact(
    status=200, declared_type="web.Quote", description="The price", error_codes=()
)

LISTING = EndpointFact(
    id="endpoint:GET /",
    topic="api",
    evidence=(_QUOTE,),
    links=Links(routes=("GET /",)),
    kind="endpoint",
    route="GET /",
    summary="List the items",
    params=(),
    responses=(
        ResponseFact(
            status=200, declared_type="web.Listing", description="The items", error_codes=()
        ),
    ),
)
QUOTING = EndpointFact(
    id="endpoint:GET /items/{sku}",
    topic="api",
    evidence=(_QUOTE,),
    links=Links(routes=("GET /items/{sku}",)),
    kind="endpoint",
    route="GET /items/{sku}",
    summary="Price some pieces",
    params=(
        ParamFact(
            name="pieces",
            location="query",
            declared_type="int",
            required=False,
            description="Pieces to price",
            minimum=1,
            maximum=50,
            enum=(),
            default="1",
        ),
    ),
    responses=(_QUOTE_REPLY,),
)
PIECES = FieldFact(
    id="field:Quote.Pieces",
    topic="api",
    evidence=(_QUOTE,),
    links=Links(declarations=("web/payloads.go::type Quote",)),
    kind="field",
    struct="Quote",
    field="Pieces",
    description="How many pieces were priced",
)
TRAITS = PolymorphicFact(
    id="polymorphic:Quote.Traits",
    topic="api",
    evidence=(_QUOTE,),
    links=Links(declarations=("web/payloads.go::type Quote",)),
    kind="polymorphic",
    struct="Quote",
    field="Traits",
    variants=("web.LeafTraits", "web.CupTraits"),
)
SHOP_FACTS: tuple[Fact, ...] = (LISTING, QUOTING, PIECES, TRAITS)


def copy_of_shop(destination: Path) -> Path:
    shutil.copytree(SHOP, destination)
    return destination


def render_shop(
    adapter: LanguageAdapter,
    repo: Path,
    *,
    facts: Sequence[Fact] = SHOP_FACTS,
    manifest: RenderManifest | None = None,
) -> RenderOutcome:
    """Renders the facts into repo the way the pipeline does, with the inventory read first."""
    request = RenderRequest(
        repo=repo,
        wiki=Wiki(facts=tuple(facts)),
        inventory=adapter.extract_inventory(repo, shop_profile()),
        manifest=manifest or RenderManifest(),
        main_file="main.go",
        title="Shop API",
    )
    return adapter.render_annotations(request)

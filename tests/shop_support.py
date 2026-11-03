"""What the tests know about the fixture service: where it is, how its profile reads it, and the
inventory the Go adapter extracts from it, which an integration test holds to the real one."""

from pathlib import Path

from arde.domain.inventory import ErrorCode, Inventory, Param, ResponseField, Route
from arde.domain.profile import ExtractionPattern, RepoProfile

SHOP = Path(__file__).resolve().parent / "fixtures" / "shop"

ROUTE_PATTERNS = (
    ExtractionPattern(kind="route", call=r"^app\.handle$", args={"path": 0, "handler": 1}),
    ExtractionPattern(kind="route", call=r"^app\.get$", args={"path": 0, "handler": 1}),
)
OTHER_PATTERNS = (
    ExtractionPattern(kind="param", call=r"^r\.URL\.Query\(\)\.Get$", args={"name": 0}),
    ExtractionPattern(kind="error", call=r"^refuse$", args={"code": 1, "status": 2}),
    ExtractionPattern(kind="env", call=r"^setting$", args={"name": 0}),
)


def shop_profile(**changes: object) -> RepoProfile:
    fields: dict[str, object] = {
        "main_package": ".",
        "main_file": "main.go",
        "port_env": "SHOP_PORT",
        "patterns": (*ROUTE_PATTERNS, *OTHER_PATTERNS),
    }
    return RepoProfile.model_validate({**fields, **changes})


def response_field(
    struct: str,
    name: str,
    *,
    json_name: str,
    declared_type: str,
    may_be_absent: bool = False,
    file: str = "web/payloads.go",
) -> ResponseField:
    return ResponseField(
        struct=struct,
        name=name,
        json_name=json_name,
        declared_type=declared_type,
        may_be_absent=may_be_absent,
        file=file,
        package="web",
    )


SHOP_INVENTORY = Inventory(
    routes=(
        Route(
            method="GET",
            path="/",
            handler="s.listing",
            file="web/server.go",
            line=23,
            handler_id="(*example.com/shop/web.server).listing",
            handler_file="web/handlers.go",
            handler_line=12,
            roots=(
                "(*example.com/shop/web.server).listing",
                "(example.com/shop/web.router).handle",
            ),
        ),
        Route(
            method="GET",
            path="/items/{sku}",
            handler="s.quote",
            file="web/server.go",
            line=24,
            handler_id="(*example.com/shop/web.server).quote",
            handler_file="web/handlers.go",
            handler_line=20,
            roots=("(*example.com/shop/web.server).quote", "(example.com/shop/web.router).get"),
        ),
    ),
    params=(Param(name="pieces"),),
    error_codes=(
        ErrorCode(code="bad_pieces", status="http.StatusBadRequest"),
        ErrorCode(code="unknown_sku", status="http.StatusNotFound"),
    ),
    response_fields=(
        response_field("CupTraits", "Millilitres", json_name="millilitres", declared_type="int"),
        response_field("ItemView", "PriceCents", json_name="price_cents", declared_type="int64"),
        response_field("ItemView", "SKU", json_name="sku", declared_type="string"),
        response_field("ItemView", "Title", json_name="title", declared_type="string"),
        response_field("LeafTraits", "Grams", json_name="grams", declared_type="int"),
        response_field("Listing", "Items", json_name="items", declared_type="[]ItemView"),
        response_field("Quote", "ChargeCents", json_name="charge_cents", declared_type="int64"),
        response_field("Quote", "Details", json_name="details", declared_type="any"),
        response_field(
            "Quote", "Note", json_name="note", declared_type="string", may_be_absent=True
        ),
        response_field("Quote", "Pieces", json_name="pieces", declared_type="int"),
        response_field("Quote", "SKU", json_name="sku", declared_type="string"),
        response_field("Quote", "Traits", json_name="traits", declared_type="any"),
        response_field("Stamp", "IssuedBy", json_name="issued_by", declared_type="string"),
        response_field(
            "problem", "Code", json_name="code", declared_type="string", file="web/failures.go"
        ),
    ),
    env_vars=("SHOP_PORT", "SHOP_VERBOSE"),
    make_targets=("lint", "serve", "smoke", "unit"),
    declarations=(
        "main.go::func main",
        "pricing/pricing.go::func Charge",
        "pricing/pricing_test.go::func TestChargeHasNoVolumeDiscount",
        "pricing/pricing_test.go::func TestChargeMultipliesTheUnitPrice",
        "settings.go::func setting",
        "web/failures.go::func refuse",
        "web/failures.go::type problem",
        "web/handlers.go::const maxPieces",
        "web/handlers.go::func server.listing",
        "web/handlers.go::func server.quote",
        "web/payloads.go::func respond",
        "web/payloads.go::type CupTraits",
        "web/payloads.go::type ItemView",
        "web/payloads.go::type LeafTraits",
        "web/payloads.go::type Listing",
        "web/payloads.go::type Quote",
        "web/payloads.go::type Stamp",
        "web/router.go::func router.get",
        "web/router.go::func router.handle",
        "web/router.go::type router",
        "web/server.go::func NewHandler",
        "web/server.go::func server.find",
        "web/server.go::type item",
        "web/server.go::type server",
        "web/server_test.go::func TestQuoteChargesThePiecesAtTheListedPrice",
        "web/server_test.go::type decodedQuote",
        "web/smoke_integration_test.go::func TestAnUnknownItemIsRefused",
    ),
)

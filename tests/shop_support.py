"""What the tests know about the fixture service: where it is and how its profile reads it."""

from pathlib import Path

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

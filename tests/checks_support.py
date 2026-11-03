"""What the checks tests share: a CheckContext built from small parts, and facts to put in it."""

import json
from pathlib import Path
from typing import Any

from arde.application.checks_runner import CheckContext, ServicePair
from arde.application.ports import RenderResult, ServiceHandle
from arde.domain.facts import (
    EndpointFact,
    Fact,
    FieldFact,
    Links,
    ParamFact,
    ProbeRecord,
    ResponseFact,
    Wiki,
)
from arde.domain.inventory import Inventory, Route
from arde.domain.profile import RepoProfile

BEFORE = ServiceHandle(base_url="http://127.0.0.1:1", port=1)
AFTER = ServiceHandle(base_url="http://127.0.0.1:2", port=2)


def a_route(key: str, **overrides: Any) -> Route:
    method, path = key.split(" ", 1)
    fields: dict[str, Any] = {
        "method": method,
        "path": path,
        "handler": "web.things",
        "file": "web/routes.go",
        "line": 10,
        "handler_id": "example.com/things/web.things",
        "handler_file": "web/handlers.go",
        "handler_line": 20,
    }
    return Route(**{**fields, **overrides})


def an_inventory(**overrides: Any) -> Inventory:
    fields: dict[str, Any] = {
        "routes": (),
        "params": (),
        "error_codes": (),
        "response_fields": (),
        "env_vars": (),
        "make_targets": (),
    }
    return Inventory(**{**fields, **overrides})


def a_param(name: str, **overrides: Any) -> ParamFact:
    fields: dict[str, Any] = {
        "name": name,
        "location": "query",
        "declared_type": "int",
        "required": False,
        "description": f"The {name}",
        "minimum": None,
        "maximum": None,
        "enum": (),
        "default": None,
    }
    return ParamFact(**{**fields, **overrides})


def a_response(status: int = 200, **overrides: Any) -> ResponseFact:
    fields: dict[str, Any] = {
        "status": status,
        "declared_type": "web.Thing",
        "description": "The thing",
        "error_codes": (),
    }
    return ResponseFact(**{**fields, **overrides})


def an_endpoint(route: str, **overrides: Any) -> EndpointFact:
    fields: dict[str, Any] = {
        "id": f"endpoint {route}",
        "topic": "api",
        "evidence": (),
        "links": Links(routes=(route,)),
        "kind": "endpoint",
        "route": route,
        "summary": "Things",
        "params": (),
        "responses": (a_response(),),
    }
    return EndpointFact(**{**fields, **overrides})


def a_field(struct: str, field: str) -> FieldFact:
    return FieldFact(
        id=f"field {struct}.{field}",
        topic="api",
        evidence=(),
        links=Links(),
        kind="field",
        struct=struct,
        field=field,
        description="A field",
    )


def a_wiki(*facts: Fact, probes: tuple[ProbeRecord, ...] = ()) -> Wiki:
    return Wiki(facts=facts, probes=probes)


def a_profile(**overrides: Any) -> RepoProfile:
    fields: dict[str, Any] = {
        "main_package": ".",
        "main_file": "main.go",
        "port_env": "PORT",
        "patterns": (),
    }
    return RepoProfile(**{**fields, **overrides})


def a_context(
    tmp_path: Path,
    *,
    spec: object = None,
    inventory: Inventory | None = None,
    wiki: Wiki | None = None,
    profile: RepoProfile | None = None,
    services: ServicePair | None = None,
) -> CheckContext:
    """A context whose spec file holds spec, and whose copies are two empty folders."""
    input_repo = tmp_path / "input"
    output_repo = tmp_path / "output"
    spec_path = output_repo / "docs" / "spec.json"
    spec_path.parent.mkdir(parents=True, exist_ok=True)
    input_repo.mkdir(exist_ok=True)
    spec_path.write_text(json.dumps(spec if spec is not None else a_spec()), encoding="utf-8")
    return CheckContext(
        input_repo=input_repo,
        output_repo=output_repo,
        profile=profile or a_profile(),
        inventory=inventory or an_inventory(),
        wiki=wiki or a_wiki(),
        render=RenderResult(added_lines={}, tag_changes={}, changed_files=()),
        spec_path=spec_path,
        services=services or ServicePair(before=BEFORE, after=AFTER),
    )


def a_spec(paths: dict[str, Any] | None = None, schemas: dict[str, Any] | None = None) -> Any:
    return {
        "openapi": "3.1.0",
        "info": {"title": "Things", "version": "0.0.0"},
        "paths": paths or {},
        "components": {"schemas": schemas or {}},
    }


def an_operation(
    *, params: list[Any] | None = None, responses: dict[str, Any] | None = None
) -> dict[str, Any]:
    return {
        "summary": "Things",
        "parameters": params or [],
        "responses": responses or {"200": {"description": "OK"}},
    }


def a_spec_param(name: str, location: str = "query", **schema: Any) -> dict[str, Any]:
    return {
        "name": name,
        "in": location,
        "description": f"The {name}",
        "required": location == "path",
        "schema": {"type": "integer", **schema},
    }

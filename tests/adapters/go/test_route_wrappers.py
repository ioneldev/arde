"""A function of the service that registers routes for its callers, read at each of its calls.

Each service here is one a bug hunter wrote against an earlier build, where the route came out
missing or with the wrong method and nothing said so.
"""

from pathlib import Path

import pytest

from arde.adapters.language.go.adapter import GoAdapter
from arde.adapters.language.go.inventory_builder import RouteNotUnderstoodError
from arde.domain.profile import ExtractionPattern, RepoProfile
from arde.domain.report import ExitCode

pytestmark = pytest.mark.integration


def a_service(root: Path, main: str) -> Path:
    (root / "go.mod").write_text("module example.com/wrapped\n\ngo 1.22\n", encoding="utf-8")
    (root / "main.go").write_text(main, encoding="utf-8")
    return root


def reading(*patterns: ExtractionPattern) -> RepoProfile:
    return RepoProfile(main_package=".", main_file="main.go", port_env="PORT", patterns=patterns)


def keys_of(adapter: GoAdapter, repo: Path, profile: RepoProfile) -> list[str]:
    return [route.key for route in adapter.extract_inventory(repo, profile).routes]


METHOD_AS_A_PARAMETER = """package main

import (
	"fmt"
	"net/http"
	"os"
)

type app struct {
	mux *http.ServeMux
}

func (a *app) handle(method, path string, h http.HandlerFunc) {
	a.mux.HandleFunc(method+" "+path, h)
}

func (a *app) routes() {
	a.handle("POST", "/orders", a.createOrder)
	a.handle("DELETE", "/orders/{id}", a.deleteOrder)
	a.mux.HandleFunc("GET /healthz", a.health)
}

func (a *app) createOrder(w http.ResponseWriter, r *http.Request) { fmt.Fprint(w, "created") }
func (a *app) deleteOrder(w http.ResponseWriter, r *http.Request) { fmt.Fprint(w, "deleted") }
func (a *app) health(w http.ResponseWriter, r *http.Request)      { fmt.Fprint(w, "ok") }

func main() {
	a := &app{mux: http.NewServeMux()}
	a.routes()
	http.ListenAndServe(":"+os.Getenv("PORT"), a.mux)
}
"""


def test_a_wrapper_that_takes_the_method_gives_each_route_its_method(
    adapter: GoAdapter, tmp_path: Path
) -> None:
    repo = a_service(tmp_path, METHOD_AS_A_PARAMETER)
    pattern = ExtractionPattern(
        kind="route", call=r"^a\.handle$", args={"method": 0, "path": 1, "handler": 2}
    )

    assert keys_of(adapter, repo, reading(pattern)) == [
        "GET /healthz",
        "POST /orders",
        "DELETE /orders/{id}",
    ]


def test_a_wrapper_that_takes_the_method_stops_the_run_when_the_pattern_does_not_name_it(
    adapter: GoAdapter, tmp_path: Path
) -> None:
    repo = a_service(tmp_path, METHOD_AS_A_PARAMETER)
    pattern = ExtractionPattern(kind="route", call=r"^a\.handle$", args={"path": 1, "handler": 2})

    with pytest.raises(RouteNotUnderstoodError, match=r"main\.go:14") as stopped:
        adapter.extract_inventory(repo, reading(pattern))

    assert "`method`" in stopped.value.next_action


def a_resource_registering(item_path: str, *, setup: str = "") -> str:
    return f"""package main

import (
	"fmt"
	"net/http"
	"os"
	"path"
)

var _ = path.Join

type crud struct {{
	list, create, get http.HandlerFunc
}}

type app struct {{
	mux *http.ServeMux
}}

func (a *app) resource(base string, h crud) {{
	{setup}
	a.mux.HandleFunc("GET "+base, h.list)
	a.mux.HandleFunc("POST "+base, h.create)
	a.mux.HandleFunc("GET "+{item_path}, h.get)
}}

func list(w http.ResponseWriter, r *http.Request)   {{ fmt.Fprint(w, "list") }}
func create(w http.ResponseWriter, r *http.Request) {{ fmt.Fprint(w, "create") }}
func get(w http.ResponseWriter, r *http.Request)    {{ fmt.Fprint(w, "get") }}

func main() {{
	a := &app{{mux: http.NewServeMux()}}
	a.resource("/books", crud{{list: list, create: create, get: get}})
	http.ListenAndServe(":"+os.Getenv("PORT"), a.mux)
}}
"""


@pytest.mark.parametrize(
    "resource",
    [
        a_resource_registering('path.Join(base, "{id}")'),
        a_resource_registering("item", setup='item := base + "/{id}"'),
    ],
    ids=["joined", "held in a local variable"],
)
def test_a_wrapper_that_adds_to_its_path_parameter_stops_the_run(
    adapter: GoAdapter, tmp_path: Path, resource: str
) -> None:
    repo = a_service(tmp_path, resource)
    pattern = ExtractionPattern(kind="route", call=r"^a\.resource$", args={"path": 0, "handler": 1})

    with pytest.raises(RouteNotUnderstoodError, match=r"main\.go:24") as stopped:
        adapter.extract_inventory(repo, reading(pattern))

    assert "builds its path" in str(stopped.value)
    assert stopped.value.exit_code == ExitCode.DISCOVERY_NEEDS_INPUT


GET_JSON = """package main

import (
	"encoding/json"
	"net/http"
	"os"
)

type app struct{ mux *http.ServeMux }

func (a *app) getJSON(path string, h http.HandlerFunc) {
	a.mux.HandleFunc("GET "+path, func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		h(w, r)
	})
}

func (a *app) items(w http.ResponseWriter, r *http.Request) { json.NewEncoder(w).Encode([]int{1}) }

func main() {
	a := &app{mux: http.NewServeMux()}
	a.getJSON("/items", a.items)
	http.ListenAndServe(":"+os.Getenv("PORT"), a.mux)
}
"""


def test_the_method_a_wrapper_puts_before_its_path_is_the_routes_method(
    adapter: GoAdapter, tmp_path: Path
) -> None:
    repo = a_service(tmp_path, GET_JSON)
    pattern = ExtractionPattern(kind="route", call=r"^a\.getJSON$", args={"path": 0, "handler": 1})

    assert keys_of(adapter, repo, reading(pattern)) == ["GET /items"]


def test_the_method_a_wrapper_registers_wins_over_the_name_of_the_wrapper(
    adapter: GoAdapter, tmp_path: Path
) -> None:
    repo = a_service(
        tmp_path,
        """package main

import "net/http"

type gate struct{ mux *http.ServeMux }

func (g gate) post(path string, h http.HandlerFunc) {
	g.mux.HandleFunc("GET "+path, h)
}

func main() {
	gate{mux: http.NewServeMux()}.post("/items", parcels)
}

func parcels(w http.ResponseWriter, r *http.Request) {}
""",
    )
    pattern = ExtractionPattern(kind="route", call=r"\.post$", args={"path": 0, "handler": 1})

    assert keys_of(adapter, repo, reading(pattern)) == ["GET /items"]


MOUNT_API = """package main

import (
	"net/http"
	"os"
)

func list(w http.ResponseWriter, r *http.Request)   { w.Write([]byte("list")) }
func orders(w http.ResponseWriter, r *http.Request) { w.Write([]byte("orders")) }
func health(w http.ResponseWriter, r *http.Request) { w.Write([]byte("ok")) }

func mountAPI(mux *http.ServeMux, prefix string) {
	mux.HandleFunc("GET "+prefix+"items", list)
	mux.HandleFunc("GET "+prefix+"orders", orders)
}

func main() {
	mux := http.NewServeMux()
	mux.HandleFunc("GET /healthz", health)
	mountAPI(mux, "/api/")
	http.ListenAndServe(":"+os.Getenv("PORT"), mux)
}
"""


def test_a_wrapper_with_no_handler_parameter_stops_the_run_and_loses_no_route(
    adapter: GoAdapter, tmp_path: Path
) -> None:
    repo = a_service(tmp_path, MOUNT_API)
    pattern = ExtractionPattern(kind="route", call=r"^mountAPI$", args={"path": 1, "handler": 0})

    with pytest.raises(RouteNotUnderstoodError, match=r"main\.go:13"):
        adapter.extract_inventory(repo, reading(pattern))


def test_a_call_to_a_wrapper_whose_handler_is_a_mux_stops_the_run(
    adapter: GoAdapter, tmp_path: Path
) -> None:
    repo = a_service(
        tmp_path,
        MOUNT_API.replace('"GET "+prefix+"items"', "prefix").replace(
            '\tmux.HandleFunc("GET "+prefix+"orders", orders)\n', ""
        ),
    )
    pattern = ExtractionPattern(kind="route", call=r"^mountAPI$", args={"path": 1, "handler": 0})

    with pytest.raises(RouteNotUnderstoodError, match=r"main\.go:\d+") as stopped:
        adapter.extract_inventory(repo, reading(pattern))

    assert "registers no route" in str(stopped.value)

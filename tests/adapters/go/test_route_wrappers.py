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


def keys_of(adapter: GoAdapter, repo: Path, *, profile: RepoProfile) -> list[str]:
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

    assert keys_of(adapter, repo, profile=reading(pattern)) == [
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


def a_wrapper_that_writes(*, parameters: str, write: str, site: str, call: str) -> str:
    return f"""package main

import (
	"fmt"
	"net/http"
	"os"
	"strings"
)

var _ = strings.HasSuffix

type group struct {{
	mux    *http.ServeMux
	prefix string
}}

func (g *group) add({parameters}, h http.HandlerFunc) {{
	{write}
	g.mux.HandleFunc({site}, h)
}}

func items(w http.ResponseWriter, r *http.Request) {{ fmt.Fprint(w, "items") }}

func main() {{
	g := &group{{mux: http.NewServeMux(), prefix: "/api/v1"}}
	g.add({call}, items)
	http.ListenAndServe(":"+os.Getenv("PORT"), g.mux)
}}
"""


PATH_FIRST = ExtractionPattern(kind="route", call=r"^g\.add$", args={"path": 0, "handler": 1})
METHOD_FIRST = ExtractionPattern(
    kind="route", call=r"^g\.add$", args={"method": 0, "path": 1, "handler": 2}
)


# The reviewer's services: each gave GET /items while the service serves another route there.
@pytest.mark.parametrize(
    ("service", "pattern"),
    [
        (
            a_wrapper_that_writes(
                parameters="path string",
                write="path = g.prefix + path",
                site='"GET "+path',
                call='"/items"',
            ),
            PATH_FIRST,
        ),
        (
            a_wrapper_that_writes(
                parameters="path string",
                write='path = "/v1" + path',
                site='"GET "+path',
                call='"/items"',
            ),
            PATH_FIRST,
        ),
        (
            a_wrapper_that_writes(
                parameters="path string",
                write='if !strings.HasSuffix(path, "/") { path += "/" }',
                site='"GET "+path',
                call='"/items"',
            ),
            PATH_FIRST,
        ),
        (
            a_wrapper_that_writes(
                parameters="method, path string",
                write='method = "PUT"',
                site='method+" "+path',
                call='"GET", "/items"',
            ),
            METHOD_FIRST,
        ),
    ],
    ids=["a group prefix in a field", "a prefix written in", "a slash added", "the method set"],
)
def test_a_wrapper_that_writes_a_parameter_it_registers_stops_the_run_and_names_the_write(
    adapter: GoAdapter, tmp_path: Path, service: str, pattern: ExtractionPattern
) -> None:
    repo = a_service(tmp_path, service)

    with pytest.raises(RouteNotUnderstoodError, match=r"registered at main\.go:19:") as stopped:
        adapter.extract_inventory(repo, reading(pattern))

    assert "builds its path" in str(stopped.value)
    assert "written at main.go:18" in str(stopped.value)


def a_shop_that_registers_through(helper: str, *, use: str) -> str:
    return f"""package main

import (
	"fmt"
	"net/http"
	"os"
)

type app struct{{ mux *http.ServeMux }}

func (a *app) get(path string, h http.HandlerFunc) {{
	a.mux.HandleFunc("POST "+path, h)
}}

type registrar interface {{
	get(path string, h http.HandlerFunc)
}}

func health(w http.ResponseWriter, r *http.Request) {{ fmt.Fprint(w, "ok") }}
func items(w http.ResponseWriter, r *http.Request)  {{ fmt.Fprint(w, "items") }}

{helper}

func main() {{
	a := &app{{mux: http.NewServeMux()}}
	a.get("/healthz", health)
	{use}
	http.ListenAndServe(":"+os.Getenv("PORT"), a.mux)
}}
"""


GET_ON_A = ExtractionPattern(kind="route", call=r"^a\.get$", args={"path": 0, "handler": 1})
GET_ANYWHERE = ExtractionPattern(kind="route", call=r"\.get$", args={"path": 0, "handler": 1})
PASSES_IT_ON = 'func registerShop(get func(string, http.HandlerFunc)) { get("/items", items) }'
CALLS_THE_INTERFACE = 'func viaInterface(r registrar) { r.get("/orders", items) }'


# The reviewer's service lost /items and /direct, and my own lost /orders behind the interface, or
# read it as GET with a pattern that matches r.get while the wrapper registers POST.
@pytest.mark.parametrize(
    ("helper", "use", "pattern", "place", "said"),
    [
        (PASSES_IT_ON, "registerShop(a.get)", GET_ON_A, 27, "other than in a direct call"),
        (PASSES_IT_ON, 'g := a.get; g("/direct", items)', GET_ON_A, 27, "other than in a direct"),
        (CALLS_THE_INTERFACE, "viaInterface(a)", GET_ON_A, 22, "goes through an interface"),
        (CALLS_THE_INTERFACE, "viaInterface(a)", GET_ANYWHERE, 22, "goes through an interface"),
    ],
    ids=["passed as an argument", "held in a variable", "an interface", "an interface it matches"],
)
def test_a_wrapper_used_other_than_in_a_direct_call_stops_the_run_and_names_the_use(
    adapter: GoAdapter,
    tmp_path: Path,
    helper: str,
    use: str,
    pattern: ExtractionPattern,
    place: int,
    said: str,
) -> None:
    repo = a_service(tmp_path, a_shop_that_registers_through(helper, use=use))

    with pytest.raises(RouteNotUnderstoodError, match=f"registered at main.go:{place}:") as stopped:
        adapter.extract_inventory(repo, reading(pattern))

    assert said in str(stopped.value)
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

    assert keys_of(adapter, repo, profile=reading(pattern)) == ["GET /items"]


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

    assert keys_of(adapter, repo, profile=reading(pattern)) == ["GET /items"]


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


def hash_of(adapter: GoAdapter, repo: Path, *, key: str, profile: RepoProfile) -> str:
    routes = adapter.extract_inventory(repo, profile).routes
    return adapter.route_fingerprints(repo, routes, main_package=".").hashes[key]


def hash_moves(adapter: GoAdapter, repo: Path, *, key: str, edit: tuple[str, str]) -> bool:
    profile = reading(GET_ON_A)
    before = hash_of(adapter, repo, key=key, profile=profile)
    main = repo / "main.go"
    old, new = edit
    assert old in main.read_text()
    main.write_text(main.read_text().replace(old, new))
    return hash_of(adapter, repo, key=key, profile=profile) != before


# The reviewer's h4 and r8: the wrapper's own code, and the middleware the call hands it, shape
# every request the route answers.
LOADS_JSON = """package main

import (
	"encoding/json"
	"net/http"
	"os"
)

type app struct{ mux *http.ServeMux }

func writeJSON(w http.ResponseWriter, v any) {
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(v)
}

func (a *app) get(path string, load func(*http.Request) any) {
	a.mux.HandleFunc("GET "+path, func(w http.ResponseWriter, r *http.Request) {
		writeJSON(w, load(r))
	})
}

func (a *app) items(r *http.Request) any { return []string{"a"} }

func main() {
	a := &app{mux: http.NewServeMux()}
	a.get("/items", a.items)
	http.ListenAndServe(":"+os.Getenv("PORT"), a.mux)
}
"""

GUARDED = """package main

import (
	"fmt"
	"net/http"
	"os"
)

type app struct{ mux *http.ServeMux }

type middleware func(http.HandlerFunc) http.HandlerFunc

func (a *app) get(path string, h http.HandlerFunc, wrap ...middleware) {
	for _, m := range wrap {
		h = m(h)
	}
	a.mux.HandleFunc("GET "+path, h)
}

func requireAdmin(next http.HandlerFunc) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("X-Role") != "admin" {
			http.Error(w, "forbidden", http.StatusForbidden)
			return
		}
		next(w, r)
	}
}

func items(w http.ResponseWriter, r *http.Request) { fmt.Fprint(w, "items") }

func main() {
	a := &app{mux: http.NewServeMux()}
	a.get("/admin/items", items, requireAdmin)
	http.ListenAndServe(":"+os.Getenv("PORT"), a.mux)
}
"""


def test_an_edit_to_a_function_the_wrappers_site_calls_moves_the_route(
    adapter: GoAdapter, tmp_path: Path
) -> None:
    repo = a_service(tmp_path, LOADS_JSON)

    assert hash_moves(adapter, repo, key="GET /items", edit=('"application/json"', '"text/plain"'))


def test_an_edit_to_middleware_the_call_hands_the_wrapper_moves_the_route(
    adapter: GoAdapter, tmp_path: Path
) -> None:
    repo = a_service(tmp_path, GUARDED)

    assert hash_moves(adapter, repo, key="GET /admin/items", edit=('!= "admin"', '!= "root"'))


# The reviewer's mw1 stopped with advice to check positions that were right.
ADMIN_MOUNTED = """package main

import (
	"fmt"
	"net/http"
	"os"
)

type app struct{ mux *http.ServeMux }

func (a *app) handle(path string, h http.Handler) { a.mux.Handle(path, h) }

func items(w http.ResponseWriter, r *http.Request) { fmt.Fprint(w, "items") }

func main() {
	a := &app{mux: http.NewServeMux()}
	admin := http.NewServeMux()
	admin.HandleFunc("GET /admin/users", items)
	a.handle("GET /items", http.HandlerFunc(items))
	a.handle("/admin/", admin)
	http.ListenAndServe(":"+os.Getenv("PORT"), a.mux)
}
"""


def test_a_mux_handed_to_a_wrapper_that_registers_it_at_its_path_is_a_mount(
    adapter: GoAdapter, tmp_path: Path
) -> None:
    repo = a_service(tmp_path, ADMIN_MOUNTED)
    pattern = ExtractionPattern(kind="route", call=r"^a\.handle$", args={"path": 0, "handler": 1})

    assert keys_of(adapter, repo, profile=reading(pattern)) == ["GET /admin/users", "GET /items"]


def test_a_mux_handed_to_a_wrapper_that_does_more_stops_the_run_and_says_so(
    adapter: GoAdapter, tmp_path: Path
) -> None:
    repo = a_service(
        tmp_path, ADMIN_MOUNTED.replace("a.mux.Handle(path, h)", 'a.mux.Handle("GET "+path, h)')
    )
    pattern = ExtractionPattern(kind="route", call=r"^a\.handle$", args={"path": 0, "handler": 1})

    with pytest.raises(RouteNotUnderstoodError, match=r"main\.go:20:") as stopped:
        adapter.extract_inventory(repo, reading(pattern))

    assert "hands a mux" in str(stopped.value)
    assert "positions" not in stopped.value.next_action

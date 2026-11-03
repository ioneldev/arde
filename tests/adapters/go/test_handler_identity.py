"""Which function a route's docs belong on, and which functions its fingerprint covers.

The first services here are ones a bug hunter wrote against an earlier build, where a factory's
dependency was taken for the handler: the docs would have gone on it, and an edit to the factory
moved no hash. The rest are shapes that read right then and must stay right.
"""

from pathlib import Path

import pytest

from arde.adapters.language.go.adapter import GoAdapter
from arde.adapters.language.go.goinspect_client import GoinspectClient
from arde.domain.inventory import Route
from arde.domain.profile import RepoProfile

pytestmark = pytest.mark.integration

PROFILE = RepoProfile(main_package=".", main_file="main.go", port_env="PORT", patterns=())

STORE = """package main

import (
	"encoding/json"
	"net/http"
	"os"
)

type store struct{ items []string }

func renderError(w http.ResponseWriter, r *http.Request, err error) {
	http.Error(w, err.Error(), http.StatusInternalServerError)
}

type onError func(http.ResponseWriter, *http.Request, error)

func handleListItems(s *store, fail onError) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		if err := json.NewEncoder(w).Encode(s.items); err != nil {
			fail(w, r, err)
		}
	}
}

func handleClearItems(s *store, fail onError) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) { s.items = nil }
}

func main() {
	s := &store{items: []string{"a", "b"}}
	mux := http.NewServeMux()
	mux.Handle("GET /items", handleListItems(s, renderError))
	mux.Handle("DELETE /items", handleClearItems(s, renderError))
	http.ListenAndServe(":"+os.Getenv("PORT"), mux)
}
"""


def a_service(root: Path, main: str) -> Path:
    (root / "go.mod").write_text("module example.com/served\n\ngo 1.22\n", encoding="utf-8")
    (root / "main.go").write_text(main, encoding="utf-8")
    return root


def routes_by_key(adapter: GoAdapter, repo: Path) -> dict[str, Route]:
    return {route.key: route for route in adapter.extract_inventory(repo, PROFILE).routes}


def hashes_of(adapter: GoAdapter, repo: Path) -> dict[str, str]:
    routes = adapter.extract_inventory(repo, PROFILE).routes
    return adapter.route_fingerprints(repo, routes, main_package=".").hashes


def test_a_factory_given_a_dependency_is_the_anchor_and_not_the_dependency(
    adapter: GoAdapter, tmp_path: Path
) -> None:
    route = routes_by_key(adapter, a_service(tmp_path, STORE))["GET /items"]

    assert (route.handler, route.handler_id) == (
        "handleListItems",
        "example.com/served.handleListItems",
    )
    assert route.roots == (
        "example.com/served.handleListItems",
        "example.com/served.renderError",
    )


def test_an_edit_to_the_factory_moves_its_routes_hash(adapter: GoAdapter, tmp_path: Path) -> None:
    repo = a_service(tmp_path, STORE)
    before = hashes_of(adapter, repo)

    main = repo / "main.go"
    main.write_text(main.read_text().replace("Encode(s.items)", "Encode(s.items[1:])"))
    after = hashes_of(adapter, repo)

    assert before["GET /items"] != after["GET /items"]
    assert before["DELETE /items"] == after["DELETE /items"]


def test_two_factories_that_share_a_dependency_keep_two_anchors_and_two_hashes(
    adapter: GoAdapter, tmp_path: Path
) -> None:
    repo = a_service(tmp_path, STORE)
    routes = routes_by_key(adapter, repo)
    hashes = hashes_of(adapter, repo)

    assert routes["GET /items"].handler_id != routes["DELETE /items"].handler_id
    assert hashes["GET /items"] != hashes["DELETE /items"]


PAGES = """package main

import (
	"net/http"
	"os"
	"strings"
)

type app struct{ mux *http.ServeMux }

func notFound(w http.ResponseWriter, r *http.Request) { http.NotFound(w, r) }

func spa(pages map[string]string, fallback http.HandlerFunc) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		if strings.HasPrefix(r.URL.Path, "/assets/") {
			fallback(w, r)
			return
		}
		w.Write([]byte(pages["index"]))
	}
}

func routesPage(mux *http.ServeMux) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) { w.Write([]byte("routes")) }
}

func main() {
	a := &app{mux: http.NewServeMux()}
	a.mux.Handle("GET /", spa(map[string]string{"index": "<html>"}, notFound))
	a.mux.Handle("GET /debug/routes", routesPage(a.mux))
	http.ListenAndServe(":"+os.Getenv("PORT"), a.mux)
}
"""


# It reads like middleware, so the docs go on the fallback. For one route that leaves the spec
# right, because swag reads only the comment above the function.
def test_a_single_route_whose_factory_falls_back_on_a_handler_anchors_at_the_fallback(
    adapter: GoAdapter, tmp_path: Path
) -> None:
    route = routes_by_key(adapter, a_service(tmp_path, PAGES))["GET /"]

    assert (route.handler, route.handler_id) == ("notFound", "example.com/served.notFound")
    assert route.roots == ("example.com/served.notFound", "example.com/served.spa")


def test_a_mux_given_to_a_function_that_does_not_serve_it_is_a_route_of_that_function(
    adapter: GoAdapter, tmp_path: Path
) -> None:
    route = routes_by_key(adapter, a_service(tmp_path, PAGES))["GET /debug/routes"]

    assert route.handler_id == "example.com/served.routesPage"


SHAPES = """package main

import (
	"encoding/json"
	"log"
	"net/http"
	"os"
	"time"
)

type Book struct {
	ID string `json:"id"`
}

type store[T any] struct{ items []T }

func (s *store[T]) list(w http.ResponseWriter, r *http.Request) {
	json.NewEncoder(w).Encode(s.items)
}

func handleCount[T any](s *store[T]) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) { json.NewEncoder(w).Encode(len(s.items)) }
}

type base struct{}

func (base) ping(w http.ResponseWriter, r *http.Request) { w.Write([]byte("pong")) }

type server struct {
	base
	books *store[Book]
}

type middleware func(http.Handler) http.Handler

func chain(h http.Handler, mws ...middleware) http.Handler {
	for _, mw := range mws {
		h = mw(h)
	}
	return h
}

func logged(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		log.Println(r.Method, r.URL.Path)
		next.ServeHTTP(w, r)
	})
}

func (s *server) show(w http.ResponseWriter, r *http.Request) {
	json.NewEncoder(w).Encode(Book{ID: r.PathValue("id")})
}

type apiFunc func(w http.ResponseWriter, r *http.Request) error

func (f apiFunc) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	if err := f(w, r); err != nil {
		http.Error(w, err.Error(), 500)
	}
}

func (s *server) orders(w http.ResponseWriter, r *http.Request) error { return nil }

func (s *server) slow() http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) { w.Write([]byte("s")) }
}

func main() {
	s := &server{books: &store[Book]{}}
	api := http.NewServeMux()
	api.HandleFunc("GET /api/books", s.books.list)
	api.Handle("GET /api/books/count", handleCount(s.books))
	api.Handle("GET /api/books/{id}", chain(http.HandlerFunc(s.show), logged))
	api.HandleFunc("GET /api/ping", s.ping)
	api.Handle("GET /api/orders", apiFunc(s.orders))
	api.Handle("GET /api/slow", http.TimeoutHandler(s.slow(), time.Second, "slow"))
	root := http.NewServeMux()
	root.Handle("/api/", logged(api))
	http.ListenAndServe(":"+os.Getenv("PORT"), root)
}
"""


def test_the_shapes_that_read_right_before_keep_their_anchors(
    adapter: GoAdapter, tmp_path: Path
) -> None:
    routes = routes_by_key(adapter, a_service(tmp_path, SHAPES))

    assert {key: route.handler_id for key, route in routes.items()} == {
        "GET /api/books": "(*example.com/served.store[T]).list",
        "GET /api/books/count": "example.com/served.handleCount",
        "GET /api/books/{id}": "(*example.com/served.server).show",
        "GET /api/ping": "(example.com/served.base).ping",
        "GET /api/orders": "(*example.com/served.server).orders",
        "GET /api/slow": "(*example.com/served.server).slow",
    }


def test_middleware_of_the_service_is_inside_the_fingerprint_of_the_route_it_wraps(
    adapter: GoAdapter, tmp_path: Path
) -> None:
    repo = a_service(tmp_path, SHAPES)
    before = hashes_of(adapter, repo)

    main = repo / "main.go"
    main.write_text(main.read_text().replace("h = mw(h)", "h = mw(mw(h))"))
    after = hashes_of(adapter, repo)

    assert before["GET /api/books/{id}"] != after["GET /api/books/{id}"]
    assert before["GET /api/ping"] == after["GET /api/ping"]


def test_a_route_whose_handler_names_one_function_keeps_that_functions_fingerprint(
    adapter: GoAdapter, goinspect_binary: Path, tmp_path: Path
) -> None:
    repo = a_service(tmp_path, SHAPES)
    ping = "(example.com/served.base).ping"

    alone = GoinspectClient(goinspect_binary).fingerprints(repo, [ping], main_package=".")

    assert routes_by_key(adapter, repo)["GET /api/ping"].roots == (ping,)
    assert hashes_of(adapter, repo)["GET /api/ping"] == alone.fingerprints[ping]


# The reviewer's h2: each handler's code sits in a ServeHTTP that the static graph reaches only
# through http.Handler.
HANDLER_TYPES = """package main

import (
	"fmt"
	"net/http"
	"os"
)

type headerMiddleware struct{ next http.Handler }

func (m *headerMiddleware) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("X-Frame-Options", "DENY")
	m.next.ServeHTTP(w, r)
}

func secured(next http.Handler) http.Handler { return &headerMiddleware{next: next} }

type itemsHandler struct{ names []string }

func (h *itemsHandler) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	fmt.Fprint(w, h.names)
}

func newItemsHandler(names []string) http.Handler { return &itemsHandler{names: names} }

func listOrders(w http.ResponseWriter, r *http.Request) { fmt.Fprint(w, "orders") }

func main() {
	mux := http.NewServeMux()
	mux.Handle("GET /items", newItemsHandler([]string{"a"}))
	mux.Handle("GET /orders", secured(http.HandlerFunc(listOrders)))
	mux.Handle("GET /plain", &itemsHandler{names: []string{"b"}})
	http.ListenAndServe(":"+os.Getenv("PORT"), mux)
}
"""


def moved_by(adapter: GoAdapter, repo: Path, *, old: str, new: str) -> set[str]:
    before = hashes_of(adapter, repo)
    main = repo / "main.go"
    assert old in main.read_text()
    main.write_text(main.read_text().replace(old, new))
    after = hashes_of(adapter, repo)
    return {key for key in before if before[key] != after.get(key)}


def test_an_edit_to_a_handler_types_serve_method_moves_its_routes(
    adapter: GoAdapter, tmp_path: Path
) -> None:
    repo = a_service(tmp_path, HANDLER_TYPES)

    moved = moved_by(adapter, repo, old="fmt.Fprint(w, h.names)", new="fmt.Fprint(w, len(h.names))")

    assert moved == {"GET /items", "GET /plain"}


def test_an_edit_to_middleware_that_keeps_its_handler_in_a_struct_moves_its_route(
    adapter: GoAdapter, tmp_path: Path
) -> None:
    repo = a_service(tmp_path, HANDLER_TYPES)

    moved = moved_by(adapter, repo, old='"DENY"', new='"SAMEORIGIN"')

    assert moved == {"GET /orders"}


def test_a_value_that_serves_requests_itself_is_anchored_at_its_serve_method(
    adapter: GoAdapter, tmp_path: Path
) -> None:
    route = routes_by_key(adapter, a_service(tmp_path, HANDLER_TYPES))["GET /plain"]

    serve = "(*example.com/served.itemsHandler).ServeHTTP"
    assert (route.handler, route.handler_id, route.roots) == ("itemsHandler", serve, (serve,))


# The reviewer's h1, in both spellings, and h3: two routes, one anchor and two handlers.
def logging(*, call: str) -> str:
    return f"""package main

import (
	"fmt"
	"log"
	"net/http"
	"os"
)

func logged(next http.HandlerFunc) http.HandlerFunc {{
	return func(w http.ResponseWriter, r *http.Request) {{
		log.Println(r.Method, r.URL.Path)
		{call}
	}}
}}

func listItems(w http.ResponseWriter, r *http.Request)  {{ fmt.Fprint(w, "items") }}
func listOrders(w http.ResponseWriter, r *http.Request) {{ fmt.Fprint(w, "orders") }}

func main() {{
	mux := http.NewServeMux()
	mux.HandleFunc("GET /items", logged(listItems))
	mux.HandleFunc("GET /orders", logged(listOrders))
	http.ListenAndServe(":"+os.Getenv("PORT"), mux)
}}
"""


@pytest.mark.parametrize("call", ["next(w, r)", "next.ServeHTTP(w, r)"])
def test_middleware_anchors_each_route_at_its_own_handler_in_either_spelling(
    adapter: GoAdapter, tmp_path: Path, call: str
) -> None:
    routes = routes_by_key(adapter, a_service(tmp_path, logging(call=call)))

    assert routes["GET /items"].handler_id == "example.com/served.listItems"
    assert routes["GET /orders"].handler_id == "example.com/served.listOrders"


def test_middleware_that_keeps_its_handler_in_a_struct_anchors_at_the_handler(
    adapter: GoAdapter, tmp_path: Path
) -> None:
    route = routes_by_key(adapter, a_service(tmp_path, HANDLER_TYPES))["GET /orders"]

    assert route.handler_id == "example.com/served.listOrders"


TWO_APPS = """package main

import (
	"net/http"
	"os"
	"strings"
)

func spa(pages map[string]string, fallback http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if strings.HasPrefix(r.URL.Path, "/assets/") {
			fallback.ServeHTTP(w, r)
			return
		}
		w.Write([]byte(pages["index"]))
	})
}

func notFound(w http.ResponseWriter, r *http.Request) { http.NotFound(w, r) }

func main() {
	mux := http.NewServeMux()
	mux.Handle("GET /", spa(map[string]string{"index": "shop"}, http.HandlerFunc(notFound)))
	mux.Handle("GET /admin/", spa(map[string]string{"index": "admin"}, http.HandlerFunc(notFound)))
	mux.Handle("GET /help", http.HandlerFunc(notFound))
	mux.HandleFunc("GET /missing", notFound)
	http.ListenAndServe(":"+os.Getenv("PORT"), mux)
}
"""


# notFound alone, at /help and /missing, is notFound's own code, so it keeps the anchor beside the
# two apps that only fall back on it.
def test_routes_whose_different_handlers_share_an_anchor_get_none_and_name_each_other(
    adapter: GoAdapter, tmp_path: Path
) -> None:
    routes = routes_by_key(adapter, a_service(tmp_path, TWO_APPS))

    assert {key: route.handler_id for key, route in routes.items()} == {
        "GET /": None,
        "GET /admin/": None,
        "GET /help": "example.com/served.notFound",
        "GET /missing": "example.com/served.notFound",
    }
    assert routes["GET /"].shares_anchor_with == ("GET /admin/", "GET /help", "GET /missing")
    assert routes["GET /admin/"].shares_anchor_with == ("GET /", "GET /help", "GET /missing")
    assert routes["GET /help"].shares_anchor_with == ()


ONE_HANDLER_TWICE = """package main

import (
	"net/http"
	"os"
)

func notFound(w http.ResponseWriter, r *http.Request) { http.NotFound(w, r) }

func main() {
	mux := http.NewServeMux()
	mux.Handle("GET /help", http.HandlerFunc(notFound))
	mux.HandleFunc("GET /missing", notFound)
	http.ListenAndServe(":"+os.Getenv("PORT"), mux)
}
"""


def test_one_handler_at_two_paths_keeps_its_anchor(adapter: GoAdapter, tmp_path: Path) -> None:
    routes = routes_by_key(adapter, a_service(tmp_path, ONE_HANDLER_TWICE))

    assert {key: route.handler_id for key, route in routes.items()} == {
        "GET /help": "example.com/served.notFound",
        "GET /missing": "example.com/served.notFound",
    }
    assert routes["GET /help"].shares_anchor_with == ()


# The reviewer's mt: each mount gave a route row beside the mounted mux's own routes, and none of
# those rows is served.
MOUNTS = """package main

import (
	"log"
	"net/http"
	"os"
	"time"
)

type headerMiddleware struct{ next http.Handler }

func (m *headerMiddleware) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("X-Frame-Options", "DENY")
	m.next.ServeHTTP(w, r)
}

func secured(next http.Handler) http.Handler { return &headerMiddleware{next: next} }

func logged(next http.HandlerFunc) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) { log.Println(r.URL); next(w, r) }
}

func passed(next http.Handler) http.Handler { return http.HandlerFunc(next.ServeHTTP) }

func ok(w http.ResponseWriter, r *http.Request) { w.Write([]byte("ok")) }

func main() {
	root := http.NewServeMux()
	a, b, c := http.NewServeMux(), http.NewServeMux(), http.NewServeMux()
	d, e := http.NewServeMux(), http.NewServeMux()
	a.HandleFunc("GET /a/x", ok)
	b.HandleFunc("GET /b/x", ok)
	c.HandleFunc("GET /c/x", ok)
	d.HandleFunc("GET /d/x", ok)
	e.HandleFunc("GET /e/x", ok)
	root.Handle("/a/", secured(a))
	root.HandleFunc("/b/", logged(b.ServeHTTP))
	root.HandleFunc("/c/", c.ServeHTTP)
	root.Handle("/d/", passed(d))
	root.Handle("/e/", http.TimeoutHandler(e, time.Second, "slow"))
	http.ListenAndServe(":"+os.Getenv("PORT"), root)
}
"""


def test_a_mux_behind_middleware_or_through_its_serve_method_is_a_mount_and_no_route(
    adapter: GoAdapter, tmp_path: Path
) -> None:
    routes = routes_by_key(adapter, a_service(tmp_path, MOUNTS))

    assert sorted(routes) == ["GET /a/x", "GET /b/x", "GET /c/x", "GET /d/x", "GET /e/x"]


# The reviewer's h4: the page size is a constant at the registration, in no function's code.
PAGED = """package main

import (
	"net/http"
	"os"
)

func paged(size int, next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("X-Page-Size", string(rune('0'+size)))
		next.ServeHTTP(w, r)
	})
}

func orders(w http.ResponseWriter, r *http.Request) { w.Write([]byte("orders")) }

func main() {
	mux := http.NewServeMux()
	mux.Handle("GET /orders", paged(5, http.HandlerFunc(orders)))
	mux.HandleFunc("GET /plain", orders)
	http.ListenAndServe(":"+os.Getenv("PORT"), mux)
}
"""


def test_an_edit_to_a_constant_the_handler_holds_moves_its_route(
    adapter: GoAdapter, tmp_path: Path
) -> None:
    repo = a_service(tmp_path, PAGED)

    moved = moved_by(adapter, repo, old="paged(5,", new="paged(9,")

    assert moved == {"GET /orders"}


# The reviewer's c1 and c2: middleware that takes its settings first, and the same held in a local
# variable.
CURRIED = """package main

import (
	"fmt"
	"net/http"
	"os"
)

func requireRole(role string) func(http.Handler) http.Handler {
	return func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			if r.Header.Get("X-Role") != role {
				http.Error(w, "forbidden", http.StatusForbidden)
				return
			}
			next.ServeHTTP(w, r)
		})
	}
}

func listItems(w http.ResponseWriter, r *http.Request)  { fmt.Fprint(w, "items") }
func listOrders(w http.ResponseWriter, r *http.Request) { fmt.Fprint(w, "orders") }

func main() {
	mux := http.NewServeMux()
	admin := requireRole("admin")
	mux.Handle("GET /items", requireRole("admin")(http.HandlerFunc(listItems)))
	mux.Handle("GET /orders", requireRole("admin")(http.HandlerFunc(listOrders)))
	mux.Handle("GET /orders2", admin(http.HandlerFunc(listOrders)))
	http.ListenAndServe(":"+os.Getenv("PORT"), mux)
}
"""


def test_middleware_a_function_makes_anchors_each_route_at_its_own_handler(
    adapter: GoAdapter, tmp_path: Path
) -> None:
    routes = routes_by_key(adapter, a_service(tmp_path, CURRIED))

    role = "example.com/served.requireRole"
    assert {key: (route.handler_id, route.roots) for key, route in routes.items()} == {
        "GET /items": ("example.com/served.listItems", ("example.com/served.listItems", role)),
        "GET /orders": ("example.com/served.listOrders", ("example.com/served.listOrders", role)),
        "GET /orders2": (None, ("example.com/served.listOrders", role)),
    }


def test_an_edit_to_middleware_a_function_makes_moves_every_route_it_wraps(
    adapter: GoAdapter, tmp_path: Path
) -> None:
    repo = a_service(tmp_path, CURRIED)

    moved = moved_by(adapter, repo, old='"forbidden"', new='"denied"')

    assert moved == {"GET /items", "GET /orders", "GET /orders2"}


def test_an_edit_to_what_fills_a_middleware_variable_moves_the_route_called_through_it(
    adapter: GoAdapter, tmp_path: Path
) -> None:
    repo = a_service(tmp_path, CURRIED)

    moved = moved_by(
        adapter, repo, old='admin := requireRole("admin")', new='admin := requireRole("root")'
    )

    assert moved == {"GET /orders2"}


# The reviewer's g1 and g3: one handler at several paths, beside a wrapped use of it, and a method
# value registered from two methods whose receivers are two variables of one type.
HONEST_SHARING = """package main

import (
	"fmt"
	"net/http"
	"os"
)

type server struct{ mux *http.ServeMux }

func (s *server) items(w http.ResponseWriter, r *http.Request)  { fmt.Fprint(w, "items") }
func (s *server) health(w http.ResponseWriter, r *http.Request) { fmt.Fprint(w, "ok") }

func (s *server) publicRoutes() {
	s.mux.HandleFunc("GET /items", s.items)
	s.mux.HandleFunc("GET /healthz", s.health)
}

func (s *server) v2Routes() {
	s.mux.HandleFunc("GET /v2/items", s.items)
}

func registerHealth(mux *http.ServeMux, s *server) {
	mux.HandleFunc("GET /readyz", s.health)
}

func requireAuth(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("Authorization") == "" {
			http.Error(w, "unauthorized", http.StatusUnauthorized)
			return
		}
		next.ServeHTTP(w, r)
	})
}

func listItems(w http.ResponseWriter, r *http.Request) { fmt.Fprint(w, "items") }

func main() {
	s := &server{mux: http.NewServeMux()}
	s.publicRoutes()
	s.v2Routes()
	registerHealth(s.mux, s)
	s.mux.HandleFunc("GET /list", listItems)
	s.mux.HandleFunc("GET /v1/list", listItems)
	s.mux.Handle("GET /admin/list", requireAuth(http.HandlerFunc(listItems)))
	http.ListenAndServe(":"+os.Getenv("PORT"), s.mux)
}
"""


def test_a_handler_alone_keeps_its_anchor_beside_a_wrapped_use_of_it(
    adapter: GoAdapter, tmp_path: Path
) -> None:
    routes = routes_by_key(adapter, a_service(tmp_path, HONEST_SHARING))

    shown = {key: (route.handler_id, route.shares_anchor_with) for key, route in routes.items()}
    assert {key: shown[key] for key in ("GET /list", "GET /v1/list", "GET /admin/list")} == {
        "GET /list": ("example.com/served.listItems", ()),
        "GET /v1/list": ("example.com/served.listItems", ()),
        "GET /admin/list": (None, ("GET /list", "GET /v1/list")),
    }


def test_a_method_value_registered_from_two_methods_keeps_its_anchor(
    adapter: GoAdapter, tmp_path: Path
) -> None:
    routes = routes_by_key(adapter, a_service(tmp_path, HONEST_SHARING))

    shown = {key: (route.handler_id, route.shares_anchor_with) for key, route in routes.items()}
    items, health = "(*example.com/served.server).items", "(*example.com/served.server).health"
    assert {
        key: shown[key] for key in ("GET /items", "GET /v2/items", "GET /healthz", "GET /readyz")
    } == {
        "GET /items": (items, ()),
        "GET /v2/items": (items, ()),
        "GET /healthz": (health, ()),
        "GET /readyz": (health, ()),
    }

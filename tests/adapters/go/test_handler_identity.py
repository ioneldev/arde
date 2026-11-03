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


def test_a_factory_that_falls_back_on_a_handler_is_the_anchor(
    adapter: GoAdapter, tmp_path: Path
) -> None:
    route = routes_by_key(adapter, a_service(tmp_path, PAGES))["GET /"]

    assert (route.handler, route.handler_id) == ("spa", "example.com/served.spa")


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

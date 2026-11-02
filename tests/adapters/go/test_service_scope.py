"""One service out of a repo: two mains, mounted muxes, and the shapes a regex could not read."""

import os
import shutil
import socket
import subprocess
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
from shop_support import SHOP

from arde.adapters.language.go.adapter import GoAdapter
from arde.adapters.language.go.inventory_builder import RouteNotUnderstoodError
from arde.domain.inventory import INLINE_HANDLER, Inventory
from arde.domain.profile import ExtractionPattern, RepoProfile
from arde.domain.report import ExitCode

pytestmark = pytest.mark.integration

FIXTURES = SHOP.parent
PARCELS = FIXTURES / "parcels"
LIBRARY = FIXTURES / "library"

PARCEL_PATTERNS = (
    ExtractionPattern(
        kind="route", call=r"^switchboard\.HandleFunc$", args={"path": 0, "handler": 1}
    ),
    ExtractionPattern(kind="param", call=r"^r\.URL\.Query\(\)\.Get$", args={"name": 0}),
    ExtractionPattern(kind="error", call=r"^http\.Error$", args={"code": 1, "status": 2}),
)


def parcels_at(main_package: str, port_env: str) -> RepoProfile:
    return RepoProfile(
        main_package=main_package,
        main_file=f"{main_package.removeprefix('./')}/main.go",
        port_env=port_env,
        patterns=PARCEL_PATTERNS,
    )


TRACKER = parcels_at("./cmd/tracker", "TRACKER_PORT")
CONSOLE = parcels_at("./cmd/console", "CONSOLE_PORT")
LIBRARY_PROFILE = RepoProfile(
    main_package=".", main_file="main.go", port_env="LIBRARY_PORT", patterns=()
)


def keys_of(inventory: Inventory) -> list[str]:
    return [route.key for route in inventory.routes]


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def wait_for(port: int) -> None:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
            return
        except OSError:
            time.sleep(0.05)
    pytest.fail(f"nothing listened on port {port}")


@contextmanager
def serving(binary: Path, port_env: str) -> Iterator[int]:
    port = free_port()
    process = subprocess.Popen(
        [str(binary)],
        env={**os.environ, port_env: str(port)},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        wait_for(port)
        yield port
    finally:
        process.terminate()
        process.wait(timeout=10)


def status_of(port: int, *, method: str, path: str) -> int:
    request = urllib.request.Request(f"http://127.0.0.1:{port}{path}", method=method)
    try:
        with urllib.request.urlopen(request, timeout=5) as reply:
            return int(reply.status)
    except urllib.error.HTTPError as refused:
        return refused.code


def test_a_repo_with_two_mains_lists_the_routes_of_the_one_it_is_asked_for(
    adapter: GoAdapter,
) -> None:
    assert keys_of(adapter.extract_inventory(PARCELS, TRACKER)) == [
        "GET /parcels/{barcode}",
        "GET /rollcall",
    ]
    assert keys_of(adapter.extract_inventory(PARCELS, CONSOLE)) == ["POST /purge"]


def test_a_route_from_a_blank_import_counts_whatever_folder_it_is_in(adapter: GoAdapter) -> None:
    routes = adapter.extract_inventory(PARCELS, TRACKER).routes

    [rollcall] = [route for route in routes if route.path == "/rollcall"]
    assert (rollcall.file, rollcall.handler) == ("rollcall/rollcall.go", "answer")


def test_what_belongs_to_the_other_main_is_reported_and_is_not_in_the_inventory(
    adapter: GoAdapter,
) -> None:
    inventory = adapter.extract_inventory(PARCELS, TRACKER)

    assert inventory.env_vars == ("TRACKER_PORT",)
    assert inventory.params == ()
    assert inventory.error_codes == ()
    assert [field.struct for field in inventory.response_fields] == ["Parcel", "Parcel"]
    outside = "outside main_package ./cmd/tracker"
    assert inventory.dropped == (
        f"cmd/console/main.go:13: env {outside}",
        f"console/console.go:13: response field {outside}",
        f"console/console.go:20: route {outside}",
        f"console/console.go:21: param {outside}",
        f"console/console.go:22: error {outside}",
    )


def test_declarations_come_from_the_service_alone(adapter: GoAdapter) -> None:
    declarations = adapter.extract_inventory(PARCELS, TRACKER).declarations

    assert "ledger/ledger.go::func StatusOf" in declarations
    assert "routing/tracker.go::type Parcel" in declarations
    assert not [one for one in declarations if one.startswith(("console/", "cmd/console/"))]


def test_a_function_both_mains_use_moves_a_route_in_each(
    adapter: GoAdapter, tmp_path: Path
) -> None:
    repo = shutil.copytree(PARCELS, tmp_path / "parcels")
    routes = adapter.extract_inventory(repo, TRACKER).routes
    before = adapter.route_fingerprints(repo, routes, main_package="./cmd/tracker").hashes

    ledger = repo / "ledger" / "ledger.go"
    ledger.write_text(ledger.read_text().replace('"in transit"', '"delivered"'))
    after = adapter.route_fingerprints(repo, routes, main_package="./cmd/tracker").hashes

    assert before["GET /parcels/{barcode}"] != after["GET /parcels/{barcode}"]
    assert before["GET /rollcall"] == after["GET /rollcall"]


def test_the_other_mains_code_cannot_move_a_route_of_this_one(
    adapter: GoAdapter, tmp_path: Path
) -> None:
    repo = shutil.copytree(PARCELS, tmp_path / "parcels")
    routes = adapter.extract_inventory(repo, TRACKER).routes
    before = adapter.route_fingerprints(repo, routes, main_package="./cmd/tracker")

    (repo / "console" / "console.go").write_text("package console\n\nfunc broken( {\n")

    assert adapter.route_fingerprints(repo, routes, main_package="./cmd/tracker") == before


def test_a_function_written_in_place_has_no_id_and_says_so(adapter: GoAdapter) -> None:
    routes = adapter.extract_inventory(PARCELS, CONSOLE).routes

    assert (routes[0].handler, routes[0].handler_id) == (INLINE_HANDLER, None)
    assert adapter.route_fingerprints(PARCELS, routes, main_package="./cmd/console").unhashed == {
        "POST /purge": "inline"
    }


def test_the_running_service_answers_the_routes_listed_and_not_the_other_mains(
    adapter: GoAdapter,
) -> None:
    with (
        adapter.built_service(PARCELS, TRACKER) as binary,
        serving(binary, "TRACKER_PORT") as port,
    ):
        assert status_of(port, method="GET", path="/parcels/abc") == 200
        assert status_of(port, method="GET", path="/rollcall") == 204
        assert status_of(port, method="POST", path="/purge") == 404


def test_a_mounted_mux_is_no_route_and_its_routes_are_listed_whole(adapter: GoAdapter) -> None:
    assert keys_of(adapter.extract_inventory(LIBRARY, LIBRARY_PROFILE)) == [
        "POST /loans/{isbn}",
        "GET /opening-hours",
        "GET /stacks/shelves",
        "GET /stacks/shelves/{id}",
    ]


def test_every_route_behind_a_mount_has_its_function_and_its_hash(adapter: GoAdapter) -> None:
    routes = adapter.extract_inventory(LIBRARY, LIBRARY_PROFILE).routes

    hashes = adapter.route_fingerprints(LIBRARY, routes, main_package=".")

    assert hashes.unhashed == {}
    assert sorted(hashes.hashes) == sorted(route.key for route in routes)


def test_the_running_service_answers_every_route_behind_a_mount(adapter: GoAdapter) -> None:
    routes = adapter.extract_inventory(LIBRARY, LIBRARY_PROFILE).routes
    with (
        adapter.built_service(LIBRARY, LIBRARY_PROFILE) as binary,
        serving(binary, "LIBRARY_PORT") as port,
    ):
        statuses = {
            route.key: status_of(
                port,
                method=route.method,
                path=route.path.replace("{id}", "7").replace("{isbn}", "9"),
            )
            for route in routes
        }

    assert set(statuses.values()) == {200, 201}


def test_a_prefix_stripped_before_a_mount_stops_the_run(adapter: GoAdapter, tmp_path: Path) -> None:
    repo = shutil.copytree(LIBRARY, tmp_path / "library")
    main = repo / "main.go"
    main.write_text(
        main.read_text().replace(
            'front.Handle("/stacks/", stacks.Mux())',
            'front.Handle("/stacks/", http.StripPrefix("/stacks", stacks.Mux()))',
        )
    )

    with pytest.raises(RouteNotUnderstoodError, match=r"main\.go:\d+") as stopped:
        adapter.extract_inventory(repo, LIBRARY_PROFILE)

    assert stopped.value.exit_code == ExitCode.DISCOVERY_NEEDS_INPUT


@pytest.mark.skipif(shutil.which("cc") is None, reason="cgo needs a C compiler")
def test_a_service_with_a_cgo_file_keeps_its_inventory_and_its_hashes(
    adapter: GoAdapter, tmp_path: Path
) -> None:
    (tmp_path / "go.mod").write_text("module example.com/probe\n\ngo 1.22\n", encoding="utf-8")
    (tmp_path / "main.go").write_text(
        '// int seven(void) { return 7; }\nimport "C"\n'.join(["package main\n\n", ""])
        + 'import "net/http"\n\nfunc main() {\n\t_ = C.seven()\n'
        '\thttp.HandleFunc("/from-cgo", served)\n}\n\n'
        "func served(w http.ResponseWriter, r *http.Request) {}\n",
        encoding="utf-8",
    )
    pattern = ExtractionPattern(
        kind="route", call=r"^http\.HandleFunc$", args={"path": 0, "handler": 1}
    )
    profile = RepoProfile(main_package=".", main_file="main.go", port_env="P", patterns=(pattern,))

    inventory = adapter.extract_inventory(tmp_path, profile)

    assert keys_of(inventory) == ["ANY /from-cgo"]
    assert inventory.dropped == ()
    assert sorted(
        adapter.route_fingerprints(tmp_path, inventory.routes, main_package=".").hashes
    ) == ["ANY /from-cgo"]


CONSTANT_PATHS = """package main

import "net/http"

const apiRoot = "/api"

func main() {
	front := http.NewServeMux()
	back := http.NewServeMux()
	front.HandleFunc("GET /healthy", healthy)
	back.HandleFunc("GET "+apiRoot+"/stock", stock)
	back.HandleFunc("POST "+apiRoot+"/orders", orders)
	front.Handle("/api/", back)
	_ = http.ListenAndServe(":1", front)
}

func healthy(w http.ResponseWriter, _ *http.Request) {}

func stock(w http.ResponseWriter, _ *http.Request) {}

func orders(w http.ResponseWriter, _ *http.Request) {}
"""


@pytest.mark.parametrize(
    "call", [r"^(front|back)\.HandleFunc$", r"^front\.HandleFunc$", r"^nothing$"]
)
def test_narrowing_a_route_pattern_loses_no_route(
    adapter: GoAdapter, tmp_path: Path, call: str
) -> None:
    (tmp_path / "go.mod").write_text("module example.com/probe\n\ngo 1.22\n", encoding="utf-8")
    (tmp_path / "main.go").write_text(CONSTANT_PATHS, encoding="utf-8")
    pattern = ExtractionPattern(kind="route", call=call, args={"path": 0, "handler": 1})
    profile = RepoProfile(main_package=".", main_file="main.go", port_env="P", patterns=(pattern,))

    assert keys_of(adapter.extract_inventory(tmp_path, profile)) == [
        "POST /api/orders",
        "GET /api/stock",
        "GET /healthy",
    ]

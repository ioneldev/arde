"""Changes made to a copy of the shop fixture, each named for what it does to the service.

They are the changes the design measured its checks against, made on the shop: a comment
reworded, code that changes a contract or behaviour, a directive that changes the build while the
syntax tree stays the same, and a line, a tag, a file or a route that ARDE never writes. Each takes
the root of a copy and edits it in place. The measured changes the shop has nothing to make them
on are left out: a server timeout, a panic recovery, a rounding rule, a Dockerfile, an embedded
file, a cgo preamble and an example's expected output.
"""

import re
from collections.abc import Callable
from pathlib import Path

Mutation = Callable[[Path], None]


def _replace(path: Path, *, old: str, new: str) -> None:
    text = path.read_bytes().decode("utf-8")
    if old not in text:
        raise AssertionError(f"{path} no longer holds {old!r}, so this change cannot be made")
    path.write_bytes(text.replace(old, new, 1).encode("utf-8"))


def _append(path: Path, text: str) -> None:
    path.write_bytes(path.read_bytes() + text.encode("utf-8"))


# A comment


def reword_a_comment(repo: Path) -> None:
    _replace(
        repo / "web" / "failures.go",
        old="// refuse answers with a JSON body that names what went wrong.",
        new="// refuse answers with a JSON body naming what went wrong.",
    )


# The contract


def remove_the_listing_route(repo: Path) -> None:
    _replace(repo / "web" / "server.go", old='\tapp.handle("GET /{$}", s.listing)\n', new="")


def add_a_route(repo: Path) -> None:
    _replace(
        repo / "web" / "server.go",
        old='\tapp.get("/items/{sku}", s.quote)\n',
        new='\tapp.get("/items/{sku}", s.quote)\n\tapp.get("/health", s.listing)\n',
    )


def stop_reading_the_pieces_param(repo: Path) -> None:
    handlers = repo / "web" / "handlers.go"
    _replace(handlers, old='\t"strconv"\n', new="")
    _replace(
        handlers,
        old=(
            '\tif raw := r.URL.Query().Get("pieces"); raw != "" {\n'
            "\t\tparsed, err := strconv.Atoi(raw)\n"
            "\t\tif err != nil || parsed < 1 || parsed > maxPieces {\n"
            '\t\t\trefuse(w, "bad_pieces", http.StatusBadRequest)\n'
            "\t\t\treturn\n"
            "\t\t}\n"
            "\t\tpieces = parsed\n"
            "\t}\n"
        ),
        new="",
    )


def read_a_new_param(repo: Path) -> None:
    _replace(
        repo / "web" / "handlers.go",
        old="\tentry, ok := s.find(",
        new='\t_ = r.URL.Query().Get("lang")\n\tentry, ok := s.find(',
    )


def add_a_response_field(repo: Path) -> None:
    _replace(
        repo / "web" / "payloads.go",
        old='\tPieces      int    `json:"pieces"`\n',
        new='\tPieces      int    `json:"pieces"`\n\tBrand       string `json:"brand"`\n',
    )


def lower_the_pieces_limit(repo: Path) -> None:
    _replace(repo / "web" / "handlers.go", old="maxPieces = 50", new="maxPieces = 5")


def raise_the_pieces_limit(repo: Path) -> None:
    _replace(repo / "web" / "handlers.go", old="maxPieces = 50", new="maxPieces = 100")


def change_the_default_pieces(repo: Path) -> None:
    _replace(repo / "web" / "handlers.go", old="\tpieces := 1\n", new="\tpieces := 2\n")


def rename_an_error_code(repo: Path) -> None:
    _replace(repo / "web" / "handlers.go", old='"bad_pieces"', new='"invalid_pieces"')


# Behaviour


def charge_for_one_piece_only(repo: Path) -> None:
    _replace(
        repo / "web" / "handlers.go",
        old="pricing.Charge(entry.priceCents, pieces)",
        new="pricing.Charge(entry.priceCents, 1)",
    )


# The largest order the shop takes is refused, and no test asks for it.
def refuse_the_largest_order(repo: Path) -> None:
    _replace(repo / "web" / "handlers.go", old="parsed > maxPieces", new="parsed >= maxPieces")


# The replies stay the same, and every quote's query reaches the service's log.
def log_the_query(repo: Path) -> None:
    handlers = repo / "web" / "handlers.go"
    _replace(handlers, old='\t"net/http"\n', new='\t"log"\n\t"net/http"\n')
    _replace(
        handlers,
        old="\tentry, ok := s.find(",
        new='\tlog.Printf("quote %s", r.URL.RawQuery)\n\tentry, ok := s.find(',
    )


# The data


def change_a_price(repo: Path) -> None:
    _replace(repo / "web" / "server.go", old="priceCents: 1200", new="priceCents: 1300")


def add_an_item(repo: Path) -> None:
    _replace(
        repo / "web" / "server.go",
        old="\t}}\n",
        new=(
            '\t\t{sku: "pot-white", title: "White teapot", priceCents: 2500, '
            "traits: CupTraits{Millilitres: 900}},\n\t}}\n"
        ),
    )


# Internal changes


def rename_a_local_variable(repo: Path) -> None:
    handlers = repo / "web" / "handlers.go"
    text = handlers.read_text(encoding="utf-8")
    handlers.write_text(re.sub(r"\bentry\b", "found", text), encoding="utf-8")


def extract_a_helper_with_the_same_logic(repo: Path) -> None:
    handlers = repo / "web" / "handlers.go"
    _replace(handlers, old="parsed < 1 || parsed > maxPieces", new="!withinLimit(parsed)")
    _append(
        handlers,
        "\nfunc withinLimit(pieces int) bool {\n\treturn pieces >= 1 && pieces <= maxPieces\n}\n",
    )


# The pipeline and the tests


def add_a_make_target(repo: Path) -> None:
    _append(repo / "Makefile", "\nvet:\n\tgo vet ./...\n")


def add_a_test(repo: Path) -> None:
    (repo / "pricing" / "extra_test.go").write_text(
        'package pricing\n\nimport "testing"\n\nfunc TestExtra(t *testing.T) {}\n',
        encoding="utf-8",
    )


def raise_the_go_version(repo: Path) -> None:
    _replace(repo / "go.mod", old="go 1.22", new="go 1.23")


# Directives, which change the build while the syntax tree stays the same


# The service then reads its patterns as Go 1.21 did, and every route answers 404.
def read_routes_the_old_way(repo: Path) -> None:
    _replace(
        repo / "main.go",
        old="// Command shop serves",
        new="//go:debug httpmuxgo121=1\n\n// Command shop serves",
    )


def leave_the_pricing_tests_out(repo: Path) -> None:
    _replace(
        repo / "pricing" / "pricing_test.go",
        old="package pricing",
        new="//go:build ignore\n\npackage pricing",
    )


# A directive with no effect on the build or the tests.
def silence_a_linter(repo: Path) -> None:
    _replace(repo / "web" / "failures.go", old="func refuse(", new="//nolint:unused\nfunc refuse(")


# What ARDE never writes


def write_a_comment_arde_did_not_render(repo: Path) -> None:
    _replace(
        repo / "web" / "server.go",
        old="func (s *server) find(",
        new="// find looks an item up by its SKU.\nfunc (s *server) find(",
    )


def add_another_tag_key(repo: Path) -> None:
    _replace(
        repo / "web" / "payloads.go",
        old='\tSKU        string `json:"sku"`\n',
        new='\tSKU        string `json:"sku" validate:"x"`\n',
    )


def rename_a_json_key(repo: Path) -> None:
    _replace(
        repo / "web" / "payloads.go",
        old='\tTitle      string `json:"title"`\n',
        new='\tTitle      string `json:"name"`\n',
    )


def add_a_go_file(repo: Path) -> None:
    (repo / "web" / "extra.go").write_text(
        'package web\n\nconst extraNote = "extra"\n', encoding="utf-8"
    )


# Layout, which gofmt would change back


def misalign_a_field(repo: Path) -> None:
    _replace(
        repo / "web" / "payloads.go",
        old='\tTitle      string `json:"title"`\n',
        new='\tTitle  string `json:"title"`\n',
    )


def space_out_an_operator(repo: Path) -> None:
    _replace(
        repo / "pricing" / "pricing.go",
        old="unitCents * int64(pieces)",
        new="unitCents  *  int64(pieces)",
    )


EVERY_MUTATION: tuple[Mutation, ...] = (
    reword_a_comment,
    remove_the_listing_route,
    add_a_route,
    stop_reading_the_pieces_param,
    read_a_new_param,
    add_a_response_field,
    lower_the_pieces_limit,
    raise_the_pieces_limit,
    change_the_default_pieces,
    rename_an_error_code,
    charge_for_one_piece_only,
    refuse_the_largest_order,
    log_the_query,
    change_a_price,
    add_an_item,
    rename_a_local_variable,
    extract_a_helper_with_the_same_logic,
    add_a_make_target,
    add_a_test,
    raise_the_go_version,
    read_routes_the_old_way,
    leave_the_pricing_tests_out,
    silence_a_linter,
    write_a_comment_arde_did_not_render,
    add_another_tag_key,
    rename_a_json_key,
    add_a_go_file,
    misalign_a_field,
    space_out_an_operator,
)

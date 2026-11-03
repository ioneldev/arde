"""The renderer against real Go: positions from goinspect, and gofmt on files that were clean."""

import subprocess
from pathlib import Path
from typing import Any

import pytest
from shop_support import SHOP, shop_profile

from arde.adapters.language.go.adapter import GoAdapter
from arde.adapters.language.go.errors import NoMainFunctionError, RenderError
from arde.adapters.repo_links import LinkLeavesRepoError
from arde.application.ports import RenderManifest, RenderOutcome, RenderRequest
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
from arde.domain.inventory import Inventory
from arde.domain.profile import RepoProfile

pytestmark = pytest.mark.integration

QUOTE = CodeQuote(kind="code", file="web/handlers.go", quote="pieces := 1")
PIECES = ParamFact(
    name="pieces",
    location="query",
    declared_type="int",
    required=False,
    description="Pieces to price",
    minimum=1,
    maximum=50,
    enum=(),
    default="1",
)


def an_endpoint(route: str, **overrides: Any) -> EndpointFact:
    fields: dict[str, Any] = {
        "id": f"endpoint:{route}",
        "topic": "api",
        "evidence": (QUOTE,),
        "links": Links(routes=(route,)),
        "kind": "endpoint",
        "route": route,
        "summary": f"Answer {route}",
        "params": (),
        "responses": (
            ResponseFact(
                status=200, declared_type="web.Quote", description="The reply", error_codes=()
            ),
        ),
    }
    return EndpointFact(**{**fields, **overrides})


def a_field_fact(struct: str, field: str, description: str) -> FieldFact:
    return FieldFact(
        id=f"field:{struct}.{field}",
        topic="api",
        evidence=(QUOTE,),
        links=Links(declarations=(f"web/payloads.go::type {struct}",)),
        kind="field",
        struct=struct,
        field=field,
        description=description,
    )


TRAITS = PolymorphicFact(
    id="polymorphic:Quote.Traits",
    topic="api",
    evidence=(QUOTE,),
    links=Links(declarations=("web/payloads.go::type Quote",)),
    kind="polymorphic",
    struct="Quote",
    field="Traits",
    variants=("web.LeafTraits", "web.CupTraits"),
)

LISTING = an_endpoint("GET /", summary="List the items")
QUOTING = an_endpoint("GET /items/{sku}", summary="Price some pieces", params=(PIECES,))
PIECES_COMMENT = a_field_fact("Quote", "Pieces", "How many pieces were priced")
SHOP_FACTS: tuple[Fact, ...] = (LISTING, QUOTING, PIECES_COMMENT, TRAITS)


def a_request(repo: Path, *facts: Fact, **changes: Any) -> RenderRequest:
    fields: dict[str, Any] = {
        "repo": repo,
        "wiki": Wiki(facts=facts),
        "inventory": changes.pop("inventory", None),
        "manifest": RenderManifest(),
        "main_file": "main.go",
        "title": "Shop API",
    }
    return RenderRequest.model_validate({**fields, **changes})


class Renderer:
    """Renders a repo the way the pipeline does: the inventory first, then the facts."""

    def __init__(self, adapter: GoAdapter, profile: RepoProfile) -> None:
        self.adapter = adapter
        self.profile = profile

    def inventory(self, repo: Path) -> Inventory:
        return self.adapter.extract_inventory(repo, self.profile)

    def render(self, repo: Path, *facts: Fact, **changes: Any) -> RenderOutcome:
        inventory = changes.pop("inventory", None) or self.inventory(repo)
        request = a_request(repo, *facts, inventory=inventory, **changes)
        return self.adapter.render_annotations(request)


@pytest.fixture
def renderer(adapter: GoAdapter) -> Renderer:
    return Renderer(adapter, shop_profile())


def text_of(repo: Path, name: str) -> str:
    return (repo / name).read_bytes().decode("utf-8")


def lines_above(text: str, anchor: str, count: int) -> list[str]:
    lines = text.split("\n")
    index = next(i for i, line in enumerate(lines) if line.startswith(anchor))
    return lines[index - count : index]


def go_files(repo: Path) -> dict[str, bytes]:
    return {str(path.relative_to(repo)): path.read_bytes() for path in sorted(repo.rglob("*.go"))}


def unformatted(repo: Path) -> str:
    return subprocess.run(
        ["gofmt", "-l", "."], cwd=repo, capture_output=True, text=True, check=True
    ).stdout


# Placing blocks


def test_block_placed_above_func_found_by_ast(renderer: Renderer, shop: Path) -> None:
    handlers = shop / "web" / "handlers.go"
    # A raw string holding the function's first line, where a search by text would land first.
    decoy = (
        "const sample = `\nfunc (s *server) quote(w http.ResponseWriter, r *http.Request) {`\n\n"
    )
    handlers.write_text(
        handlers.read_text().replace("const maxPieces", decoy + "const maxPieces"),
        encoding="utf-8",
    )

    renderer.render(shop, *SHOP_FACTS)

    lines = text_of(shop, "web/handlers.go").split("\n")
    function = lines.index("func (s *server) quote(w http.ResponseWriter, r *http.Request) {")
    assert lines[function - 5 : function] == [
        "// @Summary Price some pieces",
        "// @Produce json",
        '// @Param pieces query int false "Pieces to price" minimum(1) maximum(50) default(1)',
        '// @Success 200 {object} web.Quote "The reply"',
        "// @Router /items/{sku} [get]",
    ]
    assert sum(line.startswith("// @Router /items/") for line in lines) == 1


def test_the_general_block_goes_above_func_main(renderer: Renderer, shop: Path) -> None:
    renderer.render(shop, *SHOP_FACTS)

    assert lines_above(text_of(shop, "main.go"), "func main()", 2) == [
        "// @title Shop API",
        "// @version 0.0.0",
    ]


def test_a_main_file_without_func_main_stops_the_render(renderer: Renderer, shop: Path) -> None:
    with pytest.raises(NoMainFunctionError, match=r"settings\.go"):
        renderer.render(shop, *SHOP_FACTS, main_file="settings.go")


def test_render_twice_changes_nothing(renderer: Renderer, shop: Path) -> None:
    first = renderer.render(shop, *SHOP_FACTS)
    after_first = go_files(shop)

    second = renderer.render(shop, *SHOP_FACTS, manifest=first.manifest)

    assert go_files(shop) == after_first
    assert second.result.changed_files == ()
    assert second.manifest == first.manifest


def test_human_comments_kept(renderer: Renderer, shop: Path) -> None:
    handlers = shop / "web" / "handlers.go"
    handlers.write_text(
        handlers.read_text()
        .replace("func (s *server) quote(", "// quote prices some pieces.\nfunc (s *server) quote(")
        .replace("\tpieces := 1\n", "\t// one piece unless asked\n\tpieces := 1\n"),
        encoding="utf-8",
    )

    renderer.render(shop, *SHOP_FACTS)

    text = text_of(shop, "web/handlers.go")
    assert lines_above(text, "func (s *server) quote(", 6)[0] == "// quote prices some pieces."
    assert "\t// one piece unless asked\n\tpieces := 1\n" in text


def test_existing_block_replaced_not_duplicated(renderer: Renderer, shop: Path) -> None:
    first = renderer.render(shop, *SHOP_FACTS)
    renamed = QUOTING.model_copy(update={"summary": "Quote a price"})

    renderer.render(shop, LISTING, renamed, PIECES_COMMENT, TRAITS, manifest=first.manifest)

    text = text_of(shop, "web/handlers.go")
    assert "// @Summary Quote a price" in text
    assert "Price some pieces" not in text
    assert text.count("// @Router /items/{sku} [get]") == 1


def test_block_with_no_fact_deleted(renderer: Renderer, shop: Path) -> None:
    first = renderer.render(shop, *SHOP_FACTS)
    server = shop / "web" / "server.go"
    server.write_text(server.read_text().replace("s.quote)", "s.listing)"), encoding="utf-8")

    renderer.render(shop, LISTING, PIECES_COMMENT, TRAITS, manifest=first.manifest)

    text = text_of(shop, "web/handlers.go")
    assert "func (s *server) quote(" in text
    assert lines_above(text, "func (s *server) quote(", 1) == [""]
    assert [line for line in text.split("\n") if line.startswith("// @Router")] == [
        "// @Router / [get]"
    ]


def test_a_route_a_factory_builds_is_anchored_at_the_factory(
    adapter: GoAdapter, tmp_path: Path
) -> None:
    repo = a_service(tmp_path, FACTORY)
    renderer = Renderer(adapter, BARE_PROFILE)

    renderer.render(repo, an_endpoint("GET /items", summary="List the items"))

    assert lines_above(text_of(repo, "main.go"), "func listItems(", 1) == [
        "// @Router /items [get]"
    ]


def test_routes_sharing_an_anchor_get_no_swag_block(adapter: GoAdapter, tmp_path: Path) -> None:
    repo = a_service(tmp_path, SHARED_ANCHOR)
    renderer = Renderer(adapter, BARE_PROFILE)
    routes = renderer.inventory(repo).routes
    assert all(route.handler_id is None and route.shares_anchor_with for route in routes)

    outcome = renderer.render(repo, an_endpoint("GET /"), an_endpoint("GET /admin/"))

    rendered = [line for line in text_of(repo, "main.go").split("\n") if line.startswith("// @")]
    assert rendered == ["// @title Shop API", "// @version 0.0.0"]
    assert outcome.result.changed_files == ("main.go",)


def test_a_handler_written_in_place_gets_no_swag_block(adapter: GoAdapter, tmp_path: Path) -> None:
    repo = a_service(tmp_path, INLINE)
    renderer = Renderer(adapter, BARE_PROFILE)

    renderer.render(repo, an_endpoint("GET /health"))

    text = text_of(repo, "main.go")
    assert "// @title Shop API" in text
    assert "@Router" not in text


def test_a_handler_answering_several_verbs_gets_one_router_line_per_verb(
    adapter: GoAdapter, tmp_path: Path
) -> None:
    repo = a_service(tmp_path, ANY_METHOD)
    renderer = Renderer(adapter, BARE_PROFILE)

    renderer.render(repo, an_endpoint("GET /ping"), an_endpoint("POST /ping"))

    assert lines_above(text_of(repo, "main.go"), "func ping(", 2) == [
        "// @Router /ping [get]",
        "// @Router /ping [post]",
    ]


def test_a_fact_for_a_route_the_inventory_lacks_stops_the_render(
    renderer: Renderer, shop: Path
) -> None:
    with pytest.raises(RenderError, match="GET /gone"):
        renderer.render(shop, an_endpoint("GET /gone"))


# Fields and tags


def test_field_description_written_above_field(renderer: Renderer, shop: Path) -> None:
    renderer.render(shop, *SHOP_FACTS)

    assert lines_above(text_of(shop, "web/payloads.go"), "\tPieces ", 1) == [
        "\t// How many pieces were priced"
    ]


def test_a_changed_field_description_replaces_the_old_one(renderer: Renderer, shop: Path) -> None:
    first = renderer.render(shop, *SHOP_FACTS)
    reworded = a_field_fact("Quote", "Pieces", "The number of pieces in the quote")

    second = renderer.render(shop, LISTING, QUOTING, reworded, TRAITS, manifest=first.manifest)

    text = text_of(shop, "web/payloads.go")
    assert lines_above(text, "\tPieces ", 2)[1] == "\t// The number of pieces in the quote"
    assert "How many pieces" not in text
    assert "// The number of pieces in the quote" in second.manifest.lines["web/payloads.go"]


def test_a_persons_identical_field_comment_is_not_written_twice(
    renderer: Renderer, shop: Path
) -> None:
    payloads = shop / "web" / "payloads.go"
    payloads.write_text(
        payloads.read_text().replace("\tPieces ", "\t// How many pieces were priced\n\tPieces ", 1),
        encoding="utf-8",
    )

    outcome = renderer.render(shop, *SHOP_FACTS)

    assert text_of(shop, "web/payloads.go").count("// How many pieces were priced") == 1
    assert "// How many pieces were priced" not in outcome.manifest.lines.get("web/payloads.go", ())


def a_second_quote_struct(shop: Path) -> None:
    (shop / "pricing" / "quote.go").write_text(
        "package pricing\n\n// Quote is a price before tax.\ntype Quote struct {\n"
        '\tPieces int `json:"pieces"`\n}\n',
        encoding="utf-8",
    )
    handlers = shop / "web" / "handlers.go"
    handlers.write_text(
        handlers.read_text().replace(
            "\tpieces := 1\n", "\t_ = pricing.Quote{Pieces: 1}\n\tpieces := 1\n"
        ),
        encoding="utf-8",
    )


def test_the_link_to_its_struct_says_which_of_two_like_named_fields_a_fact_means(
    renderer: Renderer, shop: Path
) -> None:
    a_second_quote_struct(shop)

    renderer.render(shop, *SHOP_FACTS)

    assert "How many pieces" in text_of(shop, "web/payloads.go")
    assert "How many pieces" not in text_of(shop, "pricing/quote.go")


def test_a_fact_whose_links_do_not_say_which_struct_stops_the_render(
    renderer: Renderer, shop: Path
) -> None:
    a_second_quote_struct(shop)
    unlinked = PIECES_COMMENT.model_copy(update={"links": Links(routes=("GET /items/{sku}",))})

    with pytest.raises(RenderError, match="2 structs of that name"):
        renderer.render(shop, LISTING, QUOTING, unlinked)


def test_a_field_on_the_line_of_its_struct_stops_the_render(renderer: Renderer, shop: Path) -> None:
    (shop / "web" / "badge.go").write_text(
        'package web\n\ntype Badge struct{ Label string `json:"label"` }\n', encoding="utf-8"
    )
    label = a_field_fact("Badge", "Label", "What the badge says").model_copy(
        update={"links": Links(declarations=("web/badge.go::type Badge",))}
    )

    with pytest.raises(RenderError, match="on the line of its struct"):
        renderer.render(shop, label)


def test_a_polymorphic_field_with_no_tag_stops_the_render(renderer: Renderer, shop: Path) -> None:
    payloads = shop / "web" / "payloads.go"
    payloads.write_text(
        payloads.read_text().replace('\tTraits      any    `json:"traits"`\n', "\tTraits any\n"),
        encoding="utf-8",
    )

    with pytest.raises(RenderError, match="no tag in backquotes"):
        renderer.render(shop, *SHOP_FACTS)


def test_oneof_tag_added_to_polymorphic_field(renderer: Renderer, shop: Path) -> None:
    renderer.render(shop, *SHOP_FACTS)

    text = text_of(shop, "web/payloads.go")
    assert 'json:"traits" oneOf:"web.LeafTraits,web.CupTraits"`' in text


# swag finds a type by its package's name, so a variant named bare would be looked up in the
# package of the file that holds the tag. Measured on the first real run: the variants lived in
# another package, and swag stopped.
def test_a_variant_named_bare_is_written_with_its_package(renderer: Renderer, shop: Path) -> None:
    bare = TRAITS.model_copy(update={"variants": ("LeafTraits", "*CupTraits")})

    renderer.render(shop, LISTING, QUOTING, PIECES_COMMENT, bare)

    assert 'oneOf:"web.LeafTraits,web.CupTraits"`' in text_of(shop, "web/payloads.go")


def test_binding_optional_added_to_omitempty_fields_only(renderer: Renderer, shop: Path) -> None:
    renderer.render(shop, *SHOP_FACTS)

    text = text_of(shop, "web/payloads.go")
    assert '`json:"note,omitempty" binding:"optional"`' in text
    assert text.count("binding:") == 1


def test_a_tag_whose_fact_is_gone_loses_its_pair(renderer: Renderer, shop: Path) -> None:
    first = renderer.render(shop, *SHOP_FACTS)

    renderer.render(shop, LISTING, QUOTING, PIECES_COMMENT, manifest=first.manifest)

    assert "oneOf" not in text_of(shop, "web/payloads.go")


def tags_of(text: str) -> list[str]:
    return [line.split("`")[1] for line in text.split("\n") if line.count("`") == 2]


def test_json_tag_untouched(renderer: Renderer, shop: Path) -> None:
    before = tags_of(text_of(shop, "web/payloads.go"))

    renderer.render(shop, *SHOP_FACTS)

    after = tags_of(text_of(shop, "web/payloads.go"))
    assert len(after) == len(before)
    assert [new for old, new in zip(before, after, strict=True) if new != old] == [
        'json:"note,omitempty" binding:"optional"',
        'json:"traits" oneOf:"web.LeafTraits,web.CupTraits"',
    ]


# Line endings and layout


def test_crlf_file_stays_crlf(renderer: Renderer, shop: Path) -> None:
    handlers = shop / "web" / "handlers.go"
    handlers.write_bytes(handlers.read_bytes().replace(b"\n", b"\r\n"))

    renderer.render(shop, *SHOP_FACTS)

    raw = handlers.read_bytes()
    assert b"@Router" in raw
    assert raw.count(b"\n") == raw.count(b"\r\n")


def test_lf_file_stays_lf(renderer: Renderer, shop: Path) -> None:
    renderer.render(shop, *SHOP_FACTS)

    raw = (shop / "web" / "handlers.go").read_bytes()
    assert b"@Router" in raw
    assert b"\r" not in raw


def test_no_trailing_newline_preserved(renderer: Renderer, shop: Path) -> None:
    handlers = shop / "web" / "handlers.go"
    handlers.write_bytes(handlers.read_bytes().rstrip(b"\n"))

    renderer.render(shop, *SHOP_FACTS)

    raw = handlers.read_bytes()
    assert b"@Router" in raw
    assert not raw.endswith(b"\n")


def test_gofmt_clean_file_stays_gofmt_clean(renderer: Renderer, shop: Path) -> None:
    assert unformatted(shop) == ""

    outcome = renderer.render(shop, *SHOP_FACTS)

    assert "web/payloads.go" in outcome.result.changed_files
    assert unformatted(shop) == ""


def test_unformatted_file_is_not_reformatted(renderer: Renderer, shop: Path) -> None:
    payloads = shop / "web" / "payloads.go"
    odd = '\tTitle  string `json:"title"`\n'
    payloads.write_text(
        payloads.read_text().replace('\tTitle      string `json:"title"`\n', odd), encoding="utf-8"
    )

    renderer.render(shop, *SHOP_FACTS)

    assert "\t// How many pieces were priced" in text_of(shop, "web/payloads.go")
    assert odd in text_of(shop, "web/payloads.go")
    assert "web/payloads.go" in unformatted(shop)


def test_a_directive_stays_last_and_the_file_stays_clean(renderer: Renderer, shop: Path) -> None:
    handlers = shop / "web" / "handlers.go"
    handlers.write_text(
        handlers.read_text().replace(
            "func (s *server) quote(", "//nolint:funlen\nfunc (s *server) quote("
        ),
        encoding="utf-8",
    )
    first = renderer.render(shop, *SHOP_FACTS)
    after_first = go_files(shop)

    renderer.render(shop, *SHOP_FACTS, manifest=first.manifest)

    assert go_files(shop) == after_first
    assert unformatted(shop) == ""
    assert lines_above(text_of(shop, "web/handlers.go"), "func (s *server) quote(", 1) == [
        "//nolint:funlen"
    ]


# What the render reports


def test_the_rendered_repo_compares_as_comment_only(
    adapter: GoAdapter, renderer: Renderer, shop: Path
) -> None:
    outcome = renderer.render(shop, *SHOP_FACTS)

    assert set(outcome.result.changed_files) == {"main.go", "web/handlers.go", "web/payloads.go"}
    assert [
        name
        for name in outcome.result.changed_files
        if not adapter.is_comment_only(SHOP / name, shop / name)
    ] == []


def test_the_result_lists_each_line_the_render_added(renderer: Renderer, shop: Path) -> None:
    outcome = renderer.render(shop, *SHOP_FACTS)

    assert outcome.result.added_lines["main.go"] == ("// @title Shop API", "// @version 0.0.0")
    assert outcome.result.added_lines["web/payloads.go"] == ("// How many pieces were priced",)
    assert outcome.result.tag_changes["web/payloads.go"] == (
        'Note string `json:"note,omitempty" binding:"optional"`',
        'Traits any `json:"traits" oneOf:"web.LeafTraits,web.CupTraits"`',
    )
    assert "web/handlers.go" not in outcome.result.tag_changes


def test_the_manifest_holds_every_comment_line_arde_wrote(renderer: Renderer, shop: Path) -> None:
    outcome = renderer.render(shop, *SHOP_FACTS)

    assert outcome.manifest.lines["web/payloads.go"] == ("// How many pieces were priced",)
    assert outcome.manifest.lines["main.go"] == ("// @title Shop API", "// @version 0.0.0")
    assert len(outcome.manifest.lines["web/handlers.go"]) == 9


def test_a_link_out_of_the_repo_stops_the_render_before_it_reads(
    renderer: Renderer, shop: Path, tmp_path: Path
) -> None:
    outside = tmp_path / "outside.go"
    outside.write_text("package web\n", encoding="utf-8")
    inventory = renderer.inventory(shop)
    (shop / "web" / "linked.go").symlink_to(outside)

    with pytest.raises(LinkLeavesRepoError, match=r"linked\.go"):
        renderer.render(shop, *SHOP_FACTS, inventory=inventory)


# Small services


BARE_PROFILE = RepoProfile(main_package=".", main_file="main.go", port_env="PORT", patterns=())


def a_service(root: Path, main: str) -> Path:
    (root / "go.mod").write_text("module example.com/served\n\ngo 1.22\n", encoding="utf-8")
    (root / "main.go").write_text(main, encoding="utf-8")
    return root


FACTORY = """package main

import (
	"encoding/json"
	"net/http"
	"os"
)

func listItems(items []string) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		_ = json.NewEncoder(w).Encode(items)
	}
}

func main() {
	mux := http.NewServeMux()
	mux.Handle("GET /items", listItems([]string{"a"}))
	_ = http.ListenAndServe(":"+os.Getenv("PORT"), mux)
}
"""

SHARED_ANCHOR = """package main

import (
	"net/http"
	"os"
)

func spa(page string, fallback http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/missing" {
			fallback.ServeHTTP(w, r)
			return
		}
		_, _ = w.Write([]byte(page))
	})
}

func notFound(w http.ResponseWriter, r *http.Request) { http.NotFound(w, r) }

func main() {
	mux := http.NewServeMux()
	mux.Handle("GET /", spa("shop", http.HandlerFunc(notFound)))
	mux.Handle("GET /admin/", spa("admin", http.HandlerFunc(notFound)))
	_ = http.ListenAndServe(":"+os.Getenv("PORT"), mux)
}
"""

INLINE = """package main

import (
	"net/http"
	"os"
)

func main() {
	mux := http.NewServeMux()
	mux.HandleFunc("GET /health", func(w http.ResponseWriter, r *http.Request) {})
	_ = http.ListenAndServe(":"+os.Getenv("PORT"), mux)
}
"""

ANY_METHOD = """package main

import (
	"net/http"
	"os"
)

func ping(w http.ResponseWriter, r *http.Request) { _, _ = w.Write([]byte("pong")) }

func main() {
	mux := http.NewServeMux()
	mux.HandleFunc("/ping", ping)
	_ = http.ListenAndServe(":"+os.Getenv("PORT"), mux)
}
"""

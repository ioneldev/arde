"""The spec and the wiki against the inventory: nothing invented, nothing the code has left out.

The server ignores a query param it does not read, so only this comparison can catch an invented
one. The live check cannot.
"""

from pathlib import Path

import pytest
from annotated_support import annotated_spec
from checks_support import (
    a_context,
    a_field,
    a_response,
    a_route,
    a_spec,
    a_spec_param,
    a_wiki,
    an_endpoint,
    an_inventory,
    an_operation,
)

from arde.checks.spec_inventory import SpecMatchesInventory
from arde.checks.spec_operations import SpecUnreadable
from arde.domain.facts import Links, PolymorphicFact, UnreachableFact, Wiki
from arde.domain.findings import Finding
from arde.domain.inventory import ErrorCode, Inventory, Param, ResponseField

THINGS = a_route("GET /things")
ONE_THING = a_route("GET /things/{id}", handler_id="example.com/things/web.thing")
INVENTORY = an_inventory(
    routes=(THINGS, ONE_THING),
    params=(Param(name="count"),),
    error_codes=(ErrorCode(code="no_thing", status="http.StatusNotFound"),),
    response_fields=(
        ResponseField(
            struct="Thing",
            name="Colour",
            json_name="colour",
            declared_type="string",
            may_be_absent=False,
            file="web/payloads.go",
            package="example.com/things/web",
        ),
    ),
)
NOT_FOUND = a_response(404, declared_type="web.Problem", error_codes=("no_thing",))
WIKI = a_wiki(
    an_endpoint("GET /things"),
    an_endpoint("GET /things/{id}", responses=(a_response(), NOT_FOUND)),
    a_field("Thing", "Colour"),
)
SPEC = a_spec(
    {
        "/things": {"get": an_operation(params=[a_spec_param("count", minimum=1)])},
        "/things/{id}": {"get": an_operation(params=[a_spec_param("id", "path")])},
    }
)


def findings(
    tmp_path: Path, *, spec: object = SPEC, inventory: Inventory = INVENTORY, wiki: Wiki = WIKI
) -> list[Finding]:
    context = a_context(tmp_path, spec=spec, inventory=inventory, wiki=wiki)
    return SpecMatchesInventory().run(context)


def subjects(found: list[Finding]) -> list[str]:
    return [finding.subject for finding in found]


def test_a_spec_and_wiki_that_match_the_code_give_no_findings(tmp_path: Path) -> None:
    assert findings(tmp_path) == []


def test_invented_route_in_spec_flagged(tmp_path: Path) -> None:
    spec = a_spec({**SPEC["paths"], "/gone": {"get": an_operation()}})

    [finding] = findings(tmp_path, spec=spec)

    assert finding.subject == "GET /gone"
    assert finding.owner == "explore"
    assert finding.check == "spec_matches_inventory"
    assert "does not register" in finding.message
    assert finding.next_action


def test_route_missing_from_spec_flagged(tmp_path: Path) -> None:
    wiki = a_wiki(an_endpoint("GET /things/{id}", responses=(a_response(), NOT_FOUND)))
    spec = a_spec({"/things/{id}": SPEC["paths"]["/things/{id}"]})

    [finding] = findings(tmp_path, spec=spec, wiki=wiki)

    assert finding.subject == "GET /things"
    assert finding.owner == "explore"
    assert "web/routes.go:10" in finding.message


# The spec tool skips a folder named docs and still succeeds, so a route whose handler lives in one
# is lost with its fact in the wiki. No agent can repair that, so code owns it.
def test_route_with_a_fact_but_no_operation_is_owned_by_code(tmp_path: Path) -> None:
    hidden = a_route("GET /notes", handler_file="web/docs/notes.go", handler_line=7)
    inventory = INVENTORY.model_copy(update={"routes": (*INVENTORY.routes, hidden)})
    wiki = a_wiki(*WIKI.facts, an_endpoint("GET /notes"))

    [finding] = findings(tmp_path, inventory=inventory, wiki=wiki)

    assert finding.subject == "GET /notes"
    assert finding.owner == "code"
    assert "web/docs/notes.go:7" in finding.message


@pytest.mark.integration
def test_a_handler_in_a_docs_folder_is_reported_missing_from_the_real_spec(
    swag_binary: Path, annotated: Path, tmp_path: Path
) -> None:
    add_notes_in_a_docs_folder(annotated)
    spec = annotated_spec(annotated, swag=swag_binary)
    sighting = a_route("GET /sightings/{id}", handler_id="example.com/aviary/birds.sighting")
    notes = a_route(
        "GET /notes",
        handler_id="example.com/aviary/birds/docs.Notes",
        handler_file="birds/docs/notes.go",
        handler_line=11,
    )
    wiki = a_wiki(
        an_endpoint("GET /sightings/{id}", responses=(a_response(declared_type="birds.Sighting"),)),
        an_endpoint("GET /notes", responses=(a_response(declared_type="docs.Note"),)),
    )

    found = findings(
        tmp_path, spec=spec, inventory=an_inventory(routes=(sighting, notes)), wiki=wiki
    )

    assert "/notes" not in spec["paths"]
    assert subjects(found) == ["GET /notes"]
    assert found[0].owner == "code"


def test_a_route_with_no_function_to_annotate_is_a_known_gap(tmp_path: Path) -> None:
    inline = a_route("GET /health", handler="(inline)", handler_id=None, handler_file=None)
    inventory = INVENTORY.model_copy(update={"routes": (*INVENTORY.routes, inline)})

    assert findings(tmp_path, inventory=inventory) == []


# A fact never holds ANY: Explore writes one per verb it finds on the path.
def test_an_any_route_is_matched_by_an_operation_on_its_path(tmp_path: Path) -> None:
    inventory = INVENTORY.model_copy(update={"routes": (a_route("ANY /things"), ONE_THING)})

    assert findings(tmp_path, inventory=inventory) == []


def test_an_any_route_with_no_operation_on_its_path_is_missing(tmp_path: Path) -> None:
    spec = a_spec({"/things/{id}": SPEC["paths"]["/things/{id}"]})
    inventory = INVENTORY.model_copy(update={"routes": (a_route("ANY /things"), ONE_THING)})

    assert subjects(findings(tmp_path, spec=spec, inventory=inventory)) == ["ANY /things"]


def test_invented_param_flagged_though_server_ignores_it(tmp_path: Path) -> None:
    things = an_operation(params=[a_spec_param("count"), a_spec_param("colour")])
    spec = a_spec({**SPEC["paths"], "/things": {"get": things}})

    [finding] = findings(tmp_path, spec=spec)

    assert finding.subject == "GET /things query param colour"
    assert finding.owner == "explore"


def test_a_path_param_must_be_a_part_of_its_path(tmp_path: Path) -> None:
    one_thing = an_operation(params=[a_spec_param("id", "path"), a_spec_param("key", "path")])
    spec = a_spec({**SPEC["paths"], "/things/{id}": {"get": one_thing}})

    assert subjects(findings(tmp_path, spec=spec)) == ["GET /things/{id} path param key"]


def test_invented_field_in_wiki_flagged(tmp_path: Path) -> None:
    variants = PolymorphicFact(
        id="variants",
        topic="api",
        evidence=(),
        links=Links(),
        kind="polymorphic",
        struct="Thing",
        field="Shape",
        variants=("web.Round", "web.Square"),
    )
    wiki = a_wiki(*WIKI.facts, a_field("Thing", "Weight"), a_field("Gadget", "Colour"), variants)

    found = findings(tmp_path, wiki=wiki)

    assert subjects(found) == ["field Gadget.Colour", "field Thing.Shape", "field Thing.Weight"]
    assert {finding.owner for finding in found} == {"explore"}


def test_error_code_missing_from_wiki_flagged(tmp_path: Path) -> None:
    wiki = a_wiki(an_endpoint("GET /things"), an_endpoint("GET /things/{id}"))

    [finding] = findings(tmp_path, wiki=wiki)

    assert finding.subject == "error code no_thing"
    assert finding.owner == "explore"


def test_an_unreachable_fact_names_its_error_code(tmp_path: Path) -> None:
    unreachable = UnreachableFact(
        id="never",
        topic="errors",
        evidence=(),
        links=Links(),
        kind="unreachable",
        error_code="no_thing",
        reason="Nothing returns it",
    )
    wiki = a_wiki(an_endpoint("GET /things"), an_endpoint("GET /things/{id}"), unreachable)

    assert findings(tmp_path, wiki=wiki) == []


def test_an_error_code_the_code_never_sends_is_flagged(tmp_path: Path) -> None:
    gone = a_response(410, declared_type="web.Problem", error_codes=("no_thing", "thing_gone"))
    wiki = a_wiki(an_endpoint("GET /things", responses=(gone,)), *WIKI.facts[1:])

    assert subjects(findings(tmp_path, wiki=wiki)) == ["error code thing_gone"]


def add_notes_in_a_docs_folder(repo: Path) -> None:
    notes = repo / "birds" / "docs" / "notes.go"
    notes.parent.mkdir()
    notes.write_text(NOTES_GO, encoding="utf-8")
    routes = repo / "birds" / "routes.go"
    text = routes.read_text(encoding="utf-8")
    text = text.replace('import "net/http"', NOTES_IMPORTS)
    text = text.replace(SIGHTING_ROUTE, f"{SIGHTING_ROUTE}\n\t{NOTES_ROUTE}")
    routes.write_text(text, encoding="utf-8")


NOTES_IMPORTS = 'import (\n\t"net/http"\n\n\t"example.com/aviary/birds/docs"\n)'
SIGHTING_ROUTE = 'mux.HandleFunc("GET /sightings/{id}", sighting)'
NOTES_ROUTE = 'mux.HandleFunc("GET /notes", docs.Notes)'
NOTES_GO = """package docs

import "net/http"

// Notes answers the notes on the sightings.
//
// @Summary List notes
// @Produce json
// @Success 200 {object} docs.Note
// @Router /notes [get]
func Notes(w http.ResponseWriter, r *http.Request) {}

// Note is one note.
type Note struct {
	Text string `json:"text"`
}
"""


def test_a_spec_that_is_not_json_stops_the_run_with_a_next_action(tmp_path: Path) -> None:
    context = a_context(tmp_path, inventory=INVENTORY, wiki=WIKI)
    context.spec_path.write_text("{not json", encoding="utf-8")

    with pytest.raises(SpecUnreadable) as unreadable:
        SpecMatchesInventory().run(context)

    assert str(context.spec_path) in str(unreadable.value)
    assert unreadable.value.next_action

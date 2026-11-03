"""The wiki between runs: one YAML file per topic, written the same way every time."""

from pathlib import Path

import pytest
import yaml

from arde.adapters.workspace import Workspace
from arde.application.wiki_store import WikiStoreError, load_wiki, save_wiki
from arde.domain.errors import ArdeError
from arde.domain.facts import (
    CodeQuote,
    EndpointFact,
    Links,
    ParamFact,
    ProbeCitation,
    ProbeRecord,
    ResponseFact,
    RuleFact,
    Wiki,
)

AWKWARD_TEXT = (
    "yes",
    "null",
    "1.0",
    "",
    " leading and trailing ",
    "a: b # c",
    "line one\n\tline two  \n",
    "crlf\r\nline",
    f"caf{chr(0xE9)} {chr(0x2028)} {chr(0x85)} {chr(0xFEFF)}",
    "lone \ud800 surrogate",
    "nul \x00 byte",
)
PROBE = ProbeRecord(
    kind="probe",
    method="GET",
    path_and_query="/items/tea-100?pieces=2",
    status=200,
    content_type="application/json",
    body='{"charge_cents":900}\n',
)
OTHER_PROBE = PROBE.model_copy(update={"method": "HEAD", "body": ""})


def a_rule(fact_id: str, *, statement: str = "A rule.", topic: str = "business-rules") -> RuleFact:
    return RuleFact(
        id=fact_id,
        topic=topic,
        kind="rule",
        evidence=(CodeQuote(kind="code", file="pricing/pricing.go", quote=statement or "x"),),
        links=Links(files=("pricing/pricing.go",)),
        statement=statement,
    )


def an_endpoint(fact_id: str = "endpoint.quote") -> EndpointFact:
    return EndpointFact(
        id=fact_id,
        topic="api",
        kind="endpoint",
        evidence=(ProbeCitation(kind="probe", method="GET", path_and_query=PROBE.path_and_query),),
        links=Links(routes=("GET /items/{sku}",)),
        route="GET /items/{sku}",
        summary="Price some pieces",
        params=(
            ParamFact(
                name="pieces",
                location="query",
                declared_type="int",
                required=False,
                description="How many",
                minimum=1,
                maximum=50,
                enum=(),
                default="1",
            ),
        ),
        responses=(
            ResponseFact(status=200, declared_type="web.Quote", description="", error_codes=()),
        ),
    )


def a_wiki() -> Wiki:
    rules = tuple(
        a_rule(f"rule.{number}", statement=text) for number, text in enumerate(AWKWARD_TEXT)
    )
    return Wiki(
        facts=(an_endpoint(), *reversed(rules), a_rule("overview.purpose", topic="overview")),
        probes=(PROBE, OTHER_PROBE),
    )


@pytest.fixture
def store(tmp_path: Path) -> Workspace:
    return Workspace(tmp_path / "home", "repo-id")


def wiki_files(store: Workspace) -> dict[str, bytes]:
    return {path.name: path.read_bytes() for path in sorted(store.wiki_dir.iterdir())}


def test_wiki_round_trip_is_byte_stable(store: Workspace) -> None:
    save_wiki(store, a_wiki())
    first = wiki_files(store)

    loaded = load_wiki(store)
    save_wiki(store, loaded)

    assert wiki_files(store) == first
    assert {fact.id: fact for fact in loaded.facts} == {fact.id: fact for fact in a_wiki().facts}
    assert set(loaded.probes) == {PROBE, OTHER_PROBE}


def test_one_yaml_file_per_topic_with_facts_sorted_by_id(store: Workspace) -> None:
    save_wiki(store, a_wiki())

    assert sorted(wiki_files(store)) == [
        "api.yaml",
        "business-rules.yaml",
        "overview.yaml",
        "probes.yaml",
    ]
    rules = yaml.safe_load((store.wiki_dir / "business-rules.yaml").read_text())
    assert rules["schema_version"] == 1
    assert [fact["id"] for fact in rules["facts"]] == sorted(f"rule.{n}" for n in range(11))


def test_the_same_wiki_in_another_order_writes_the_same_bytes(store: Workspace) -> None:
    wiki = a_wiki()
    save_wiki(store, wiki)
    first = wiki_files(store)

    shuffled = Wiki(facts=tuple(reversed(wiki.facts)), probes=tuple(reversed(wiki.probes)))
    save_wiki(store, shuffled)

    assert wiki_files(store) == first


def test_the_files_hold_only_ascii(store: Workspace) -> None:
    save_wiki(store, a_wiki())

    assert all(data.isascii() for data in wiki_files(store).values())


def test_a_topic_left_with_no_facts_loses_its_file(store: Workspace) -> None:
    save_wiki(store, a_wiki())

    save_wiki(store, Wiki(facts=(an_endpoint(),), probes=(PROBE,)))

    assert sorted(wiki_files(store)) == ["api.yaml", "probes.yaml"]
    assert [fact.id for fact in load_wiki(store).facts] == ["endpoint.quote"]


def test_no_wiki_yet_is_an_empty_wiki(store: Workspace) -> None:
    assert load_wiki(store) == Wiki(facts=())


def test_a_file_of_another_schema_version_means_a_first_run(store: Workspace) -> None:
    save_wiki(store, a_wiki())
    path = store.wiki_dir / "overview.yaml"
    path.write_text(path.read_text().replace("schema_version: 1", "schema_version: 2"))

    assert load_wiki(store) == Wiki(facts=())


def test_a_file_with_no_schema_version_means_a_first_run(store: Workspace) -> None:
    save_wiki(store, a_wiki())
    path = store.wiki_dir / "probes.yaml"
    data = yaml.safe_load(path.read_text())
    del data["schema_version"]
    path.write_text(yaml.safe_dump(data))

    assert load_wiki(store) == Wiki(facts=())


def test_a_fact_in_two_topic_files_is_refused(store: Workspace, tmp_path: Path) -> None:
    save_wiki(store, Wiki(facts=(a_rule("rule.same"),)))
    elsewhere = Workspace(tmp_path / "elsewhere", "repo-id")
    save_wiki(elsewhere, Wiki(facts=(a_rule("rule.same", topic="overview"),)))
    (elsewhere.wiki_dir / "overview.yaml").rename(store.wiki_dir / "overview.yaml")

    with pytest.raises(WikiStoreError, match=r"repeated: rule\.same"):
        load_wiki(store)


def test_a_fact_in_the_file_of_another_topic_is_refused(store: Workspace) -> None:
    save_wiki(store, Wiki(facts=(a_rule("rule.moved"),)))
    (store.wiki_dir / "business-rules.yaml").rename(store.wiki_dir / "overview.yaml")

    with pytest.raises(WikiStoreError, match=r"overview\.yaml holds rule\.moved, whose topic is"):
        load_wiki(store)


def test_a_citation_with_no_recorded_probe_is_refused(store: Workspace) -> None:
    save_wiki(store, Wiki(facts=(an_endpoint(),), probes=(PROBE,)))
    (store.wiki_dir / "probes.yaml").unlink()

    with pytest.raises(WikiStoreError, match="no probe was recorded"):
        load_wiki(store)


@pytest.mark.parametrize("text", ["facts: [\n", "- just a list\n", "schema_version: 1\nfacts: 7\n"])
def test_a_damaged_file_stops_the_run_and_says_how_to_start_again(
    store: Workspace, text: str
) -> None:
    save_wiki(store, a_wiki())
    (store.wiki_dir / "api.yaml").write_text(text)

    with pytest.raises(WikiStoreError) as raised:
        load_wiki(store)

    assert isinstance(raised.value, ArdeError)
    assert "api.yaml" in str(raised.value)
    assert str(store.wiki_dir) in raised.value.next_action


def test_a_file_that_is_not_a_topic_is_left_alone(store: Workspace) -> None:
    save_wiki(store, a_wiki())
    (store.wiki_dir / "notes.txt").write_text("mine")

    save_wiki(store, Wiki(facts=()))

    assert wiki_files(store) == {"notes.txt": b"mine"}
    assert load_wiki(store) == Wiki(facts=())

"""What changed since the snapshot, which holds the last run's own comments, pages and spec."""

from pathlib import Path

from pipeline_support import ANNOTATION, MAIN, THINGS_FACT, THINGS_PROBE, FakeAdapter

from arde.application.agents.docs_writer import GENERATED_MARKER, README_LINK
from arde.application.changes import (
    SnapshotComparison,
    changed_fact_ids,
    changes_since_snapshot,
    paths_that_differ,
    restore_last_pages,
)
from arde.application.ports import RenderManifest, RouteHashes
from arde.domain.facts import Wiki
from arde.domain.profile import RepoProfile

PROFILE = RepoProfile(main_package=".", main_file="main.go", port_env="PORT", patterns=())
WIKI = Wiki(facts=(THINGS_FACT,), probes=(THINGS_PROBE,))


def a_tree(root: Path, files: dict[str, str]) -> Path:
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return root


def compared(tmp_path: Path, *, old: dict[str, str], new: dict[str, str]) -> SnapshotComparison:
    adapter = FakeAdapter()
    snapshot, repo = a_tree(tmp_path / "snapshot", old), a_tree(tmp_path / "repo", new)
    hashes = adapter.route_fingerprints(repo, (), main_package=".")
    return SnapshotComparison(
        snapshot=snapshot,
        repo=repo,
        profile=PROFILE,
        inventory=adapter.inventory,
        hashes=hashes,
        wiki=WIKI,
        manifest=RenderManifest(lines={"main.go": (ANNOTATION,)}),
    )


def annotated(main: str) -> str:
    return main.replace("func things", f"{ANNOTATION}\nfunc things")


def test_a_file_that_differs_only_in_arde_comments_is_unchanged(tmp_path: Path) -> None:
    comparison = compared(tmp_path, old={"main.go": annotated(MAIN)}, new={"main.go": MAIN})

    report = changes_since_snapshot(comparison, FakeAdapter()).report

    assert report is not None
    assert (report.changed_files, report.diff) == ((), "")


def test_a_readme_that_differs_only_in_arde_link_is_unchanged(tmp_path: Path) -> None:
    readme = "# Things\n"
    comparison = compared(
        tmp_path,
        old={"main.go": MAIN, "README.md": f"{readme}\n{README_LINK}\n"},
        new={"main.go": MAIN, "README.md": readme},
    )

    report = changes_since_snapshot(comparison, FakeAdapter()).report

    assert report is not None and report.changed_files == ()


def test_arde_pages_only_the_snapshot_holds_are_not_removed_files(tmp_path: Path) -> None:
    comparison = compared(
        tmp_path,
        old={"main.go": MAIN, "docs/api.md": "x", "gone.go": "package main\n"},
        new={"main.go": MAIN},
    )

    report = changes_since_snapshot(comparison, FakeAdapter()).report

    assert report is not None and report.changed_files == ("gone.go",)


def test_the_diff_shows_the_repos_change_without_the_lines_arde_wrote(tmp_path: Path) -> None:
    changed = MAIN.replace("func things() {}", "func things() { panic(1) }")
    comparison = compared(tmp_path, old={"main.go": annotated(MAIN)}, new={"main.go": changed})

    report = changes_since_snapshot(comparison, FakeAdapter()).report

    assert report is not None
    assert report.changed_files == ("main.go",)
    assert "+func things() { panic(1) }" in report.diff
    assert ANNOTATION not in report.diff


def test_a_route_whose_hash_moved_makes_its_facts_stale(tmp_path: Path) -> None:
    comparison = compared(tmp_path, old={"main.go": MAIN}, new={"main.go": MAIN})
    moved = RouteHashes(hashes={"GET /things": "new"}, unhashed={})
    comparison = SnapshotComparison(**{**comparison.__dict__, "hashes": moved})

    changes = changes_since_snapshot(comparison, FakeAdapter())

    assert changes.stale_ids == ("endpoint.things",)
    assert changes.report is not None and changes.report.changed_routes == ("GET /things",)


def test_the_last_runs_pages_come_back_where_the_repo_has_none(tmp_path: Path) -> None:
    snapshot = a_tree(
        tmp_path / "snapshot",
        {
            "docs/overview.md": f"{GENERATED_MARKER}\nlast run\n",
            "docs/errors.md": "a person's page\n",
            "docs/architecture.md": f"{GENERATED_MARKER}\nlast run\n",
        },
    )
    repo = a_tree(tmp_path / "repo", {"docs/architecture.md": "the repo's own\n"})

    restore_last_pages(snapshot, repo)

    assert (repo / "docs/overview.md").read_text() == f"{GENERATED_MARKER}\nlast run\n"
    assert not (repo / "docs/errors.md").exists()
    assert (repo / "docs/architecture.md").read_text() == "the repo's own\n"


def test_no_page_comes_back_through_a_linked_docs_folder(tmp_path: Path) -> None:
    snapshot = a_tree(tmp_path / "snapshot", {"docs/overview.md": f"{GENERATED_MARKER}\n"})
    repo = a_tree(tmp_path / "repo", {"web/main.go": "package web\n"})
    (repo / "docs").symlink_to("web")

    restore_last_pages(snapshot, repo)

    assert not (repo / "web" / "overview.md").exists()


def test_a_fact_kept_under_its_id_with_other_content_is_changed() -> None:
    reworded = THINGS_FACT.model_copy(update={"summary": "List them all"})

    assert changed_fact_ids(WIKI, WIKI) == frozenset()
    assert changed_fact_ids(WIKI, Wiki(facts=(reworded,), probes=WIKI.probes)) == {
        "endpoint.things"
    }


# A section notices those from the facts it rests on.
def test_a_fact_that_came_or_went_is_not_a_changed_one() -> None:
    other = THINGS_FACT.model_copy(update={"id": "x", "topic": "errors"})
    both = Wiki(facts=(THINGS_FACT, other), probes=WIKI.probes)

    assert changed_fact_ids(WIKI, both) == frozenset()
    assert changed_fact_ids(both, WIKI) == frozenset()


def test_a_fact_whose_probe_answers_anew_is_changed() -> None:
    reanswered = THINGS_PROBE.model_copy(update={"body": '{"things":[1]}\n'})

    assert changed_fact_ids(WIKI, Wiki(facts=WIKI.facts, probes=(reanswered,))) == {
        "endpoint.things"
    }


def test_paths_that_differ_names_added_removed_and_changed_files(tmp_path: Path) -> None:
    before = a_tree(tmp_path / "before", {"same": "1", "changed": "1", "removed": "1"})
    after = a_tree(tmp_path / "after", {"same": "1", "changed": "2", "added": "1"})

    assert paths_that_differ(before, after) == ("added", "changed", "removed")

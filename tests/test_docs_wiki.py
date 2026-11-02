"""The docs wiki must not rot: links resolve, named paths exist, the index lists every page."""

import os
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

ROOT_PAGES = ("AGENTS.md", "README.md")

# The wiki is exactly these locations. docs/superpowers and docs/research describe future paths
# on purpose, and a new docs folder stays out of scope until it is listed here.
DOCS_PAGE_GLOBS = ("docs/*.md", "docs/decisions/*.md", "docs/specs/*.md")

# Phase specs are written before their phase starts, so they name paths that do not exist yet.
# They still need working links and an index entry.
PHASE_SPEC_GLOB = "docs/specs/*.md"

REPO_PATH_PREFIXES = ("src/", "tools/", "docs/", "tests/")
EXTERNAL_LINK_PREFIXES = ("http://", "https://", "mailto:", "#")

REQUIRED_PAGES = (
    "AGENTS.md",
    "README.md",
    "docs/index.md",
    "docs/architecture.md",
    "docs/glossary.md",
    "docs/limitations.md",
)

FENCED_BLOCK = re.compile(r"^```.*?^```", re.MULTILINE | re.DOTALL)
INLINE_CODE = re.compile(r"`([^`\n]+)`")
MARKDOWN_LINK = re.compile(r'\[[^\]]*\]\(([^)\s]+)(?:\s+"[^"]*")?\)')


def test_required_pages_exist() -> None:
    missing = [page for page in REQUIRED_PAGES if not (ROOT / page).is_file()]
    assert missing == []


def test_every_markdown_link_resolves() -> None:
    assert broken_links(wiki_files(ROOT), ROOT) == []


def test_every_backticked_repo_path_exists() -> None:
    assert missing_repo_paths(path_checked_files(ROOT), ROOT) == []


def test_index_links_every_docs_page() -> None:
    assert pages_missing_from_index(ROOT) == []


# The repo is delivered as a link, and .env.local or .env.bak would hold the same key as .env.
def test_every_env_file_stays_out_of_git_except_the_example() -> None:
    ignored = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()

    assert ".env*" in ignored
    assert "!.env.example" in ignored


# The checks below prove each rule fires on a violation, so a green run above means something.


def test_a_link_to_a_missing_file_is_reported(tmp_path: Path) -> None:
    page = write(tmp_path, "README.md", "See [the guide](docs/guide.md).")

    assert broken_links([page], tmp_path) == ["README.md: link to docs/guide.md does not exist"]


def test_a_link_to_an_existing_file_is_accepted(tmp_path: Path) -> None:
    write(tmp_path, "docs/guide.md", "# Guide")
    page = write(tmp_path, "README.md", "See [the guide](docs/guide.md).")

    assert broken_links([page], tmp_path) == []


def test_links_are_resolved_from_the_folder_of_the_page(tmp_path: Path) -> None:
    write(tmp_path, "docs/glossary.md", "# Glossary")
    page = write(tmp_path, "docs/decisions/0001.md", "[terms](../glossary.md)")

    assert broken_links([page], tmp_path) == []


def test_a_fragment_does_not_hide_a_missing_file(tmp_path: Path) -> None:
    page = write(tmp_path, "README.md", "[x](docs/guide.md#install)")

    assert broken_links([page], tmp_path) == ["README.md: link to docs/guide.md does not exist"]


@pytest.mark.parametrize("target", ["https://example.com", "mailto:a@b.c", "#section"])
def test_external_links_and_anchors_are_not_checked(tmp_path: Path, target: str) -> None:
    page = write(tmp_path, "README.md", f"[x]({target})")

    assert broken_links([page], tmp_path) == []


def test_link_syntax_inside_code_is_not_a_link(tmp_path: Path) -> None:
    page = write(tmp_path, "README.md", "Use `[x](nope.md)` like this:\n\n```\n[y](nope.md)\n```\n")

    assert broken_links([page], tmp_path) == []


def test_a_backticked_path_that_does_not_exist_is_reported(tmp_path: Path) -> None:
    page = write(tmp_path, "AGENTS.md", "The renderer is `src/arde/render.py`.")

    assert missing_repo_paths([page], tmp_path) == [
        "AGENTS.md: `src/arde/render.py` does not exist"
    ]


def test_a_backticked_file_or_folder_that_exists_is_accepted(tmp_path: Path) -> None:
    write(tmp_path, "src/arde/domain/models.py", "")
    page = write(tmp_path, "AGENTS.md", "`src/arde/domain/models.py` and `src/arde/domain/`.")

    assert missing_repo_paths([page], tmp_path) == []


def test_a_path_inside_a_backticked_command_is_checked(tmp_path: Path) -> None:
    page = write(tmp_path, "AGENTS.md", "Run `cd tools/goinspect && go test ./...`.")

    assert missing_repo_paths([page], tmp_path) == ["AGENTS.md: `tools/goinspect` does not exist"]


def test_backticked_text_that_is_not_a_repo_path_is_ignored(tmp_path: Path) -> None:
    page = write(tmp_path, "AGENTS.md", "Run `make check`, set `ARDE_HOME`, read `~/docs/x`.")

    assert missing_repo_paths([page], tmp_path) == []


def test_a_docs_page_missing_from_the_index_is_reported(tmp_path: Path) -> None:
    write(tmp_path, "docs/index.md", "[glossary](glossary.md)")
    write(tmp_path, "docs/glossary.md", "# Glossary")
    write(tmp_path, "docs/decisions/0001-x.md", "# X")

    assert pages_missing_from_index(tmp_path) == ["docs/decisions/0001-x.md"]


def test_every_page_linked_from_the_index_is_accepted(tmp_path: Path) -> None:
    write(tmp_path, "docs/index.md", "[g](glossary.md) [x](decisions/0001-x.md)")
    write(tmp_path, "docs/glossary.md", "# Glossary")
    write(tmp_path, "docs/decisions/0001-x.md", "# X")

    assert pages_missing_from_index(tmp_path) == []


def test_the_wiki_covers_exactly_the_listed_locations(tmp_path: Path) -> None:
    covered = [
        "AGENTS.md",
        "README.md",
        "docs/decisions/0001-x.md",
        "docs/index.md",
        "docs/specs/phase-1.md",
    ]
    out_of_scope = [
        "docs/superpowers/plans/plan.md",
        "docs/research/spike/notes.md",
        "docs/guides/setup.md",
        "docs/decisions/nested/deep.md",
    ]
    for relative in covered + out_of_scope:
        write(tmp_path, relative, "# Page")

    assert [label(page, tmp_path) for page in wiki_files(tmp_path)] == covered


def test_pages_outside_the_listed_locations_need_no_index_entry(tmp_path: Path) -> None:
    write(tmp_path, "docs/index.md", "# Index")
    write(tmp_path, "docs/superpowers/plans/plan.md", "# Plan")
    write(tmp_path, "docs/research/spike/notes.md", "# Notes")
    write(tmp_path, "docs/guides/setup.md", "# Setup")

    assert pages_missing_from_index(tmp_path) == []


def test_a_phase_spec_missing_from_the_index_is_reported(tmp_path: Path) -> None:
    write(tmp_path, "docs/index.md", "# Index")
    write(tmp_path, "docs/specs/phase-1.md", "# Phase 1")

    assert pages_missing_from_index(tmp_path) == ["docs/specs/phase-1.md"]


def test_a_broken_link_in_a_phase_spec_is_reported(tmp_path: Path) -> None:
    write(tmp_path, "docs/specs/phase-1.md", "See [the plan](../plans/ghost.md).")

    assert broken_links(wiki_files(tmp_path), tmp_path) == [
        "docs/specs/phase-1.md: link to ../plans/ghost.md does not exist"
    ]


def test_a_future_path_in_a_phase_spec_is_accepted(tmp_path: Path) -> None:
    write(tmp_path, "docs/specs/phase-1.md", "Adds `src/arde/domain/` and `tools/goinspect/`.")

    assert missing_repo_paths(path_checked_files(tmp_path), tmp_path) == []


def test_the_same_future_path_in_a_decision_is_reported(tmp_path: Path) -> None:
    write(tmp_path, "docs/decisions/0001-x.md", "Adds `src/arde/domain/`.")

    assert missing_repo_paths(path_checked_files(tmp_path), tmp_path) == [
        "docs/decisions/0001-x.md: `src/arde/domain/` does not exist"
    ]


def wiki_files(root: Path) -> list[Path]:
    root_pages = [root / name for name in ROOT_PAGES if (root / name).is_file()]
    return root_pages + docs_pages(root)


def docs_pages(root: Path) -> list[Path]:
    return sorted({page for pattern in DOCS_PAGE_GLOBS for page in root.glob(pattern)})


def path_checked_files(root: Path) -> list[Path]:
    phase_specs = set(root.glob(PHASE_SPEC_GLOB))
    return [page for page in wiki_files(root) if page not in phase_specs]


def broken_links(files: list[Path], root: Path) -> list[str]:
    return [
        f"{label(page, root)}: link to {target} does not exist"
        for page in files
        for target in local_link_targets(page.read_text(encoding="utf-8"))
        if not (page.parent / target).exists()
    ]


def local_link_targets(markdown: str) -> list[str]:
    prose = INLINE_CODE.sub("", FENCED_BLOCK.sub("", markdown))
    local = [t for t in MARKDOWN_LINK.findall(prose) if not t.startswith(EXTERNAL_LINK_PREFIXES)]
    without_fragment = [target.split("#")[0] for target in local]
    return [target for target in without_fragment if target]


def missing_repo_paths(files: list[Path], root: Path) -> list[str]:
    return [
        f"{label(page, root)}: `{path}` does not exist"
        for page in files
        for path in backticked_repo_paths(page.read_text(encoding="utf-8"))
        if not (root / path).exists()
    ]


def backticked_repo_paths(markdown: str) -> list[str]:
    # Fenced blocks hold command output and examples, so only inline code is read.
    spans = INLINE_CODE.findall(FENCED_BLOCK.sub("", markdown))
    words = [word for span in spans for word in span.split()]
    return [word for word in words if word.startswith(REPO_PATH_PREFIXES)]


def pages_missing_from_index(root: Path) -> list[str]:
    index = root / "docs" / "index.md"
    linked = linked_pages(index)
    return [
        label(page, root)
        for page in docs_pages(root)
        if page != index and normalise(page) not in linked
    ]


def linked_pages(page: Path) -> set[Path]:
    targets = local_link_targets(page.read_text(encoding="utf-8"))
    return {normalise(page.parent / target) for target in targets}


def normalise(path: Path) -> Path:
    return Path(os.path.normpath(path))


def label(page: Path, root: Path) -> str:
    return page.relative_to(root).as_posix()


def write(root: Path, relative: str, text: str) -> Path:
    page = root / relative
    page.parent.mkdir(parents=True, exist_ok=True)
    page.write_text(text, encoding="utf-8")
    return page

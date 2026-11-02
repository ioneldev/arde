"""The comment-only rule against real goinspect, and over a whole repo the way ARDE will edit it."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest
from doubtful_edits import (
    DELIBERATELY_REAL_EDITS,
    EVERY_DOUBTFUL_EDIT,
    ORDINARY_EDITS,
    RAW_STRING_EDIT,
    TAG_EDITS,
    Edit,
)
from shop_support import SHOP

from arde.adapters.language.go.adapter import GoAdapter

pytestmark = pytest.mark.integration

GREETING = """package greet

// Hello says hello.
func Hello() string {
	return "hello"
}
"""


def pair(tmp_path: Path, edit: Edit) -> tuple[Path, Path]:
    old_file, new_file = tmp_path / "old" / edit.file_name, tmp_path / "new" / edit.file_name
    for path, text in ((old_file, edit.old), (new_file, edit.new)):
        path.parent.mkdir(exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return old_file, new_file


def greeting(new: str, name: str = "x.go") -> Edit:
    return Edit("greeting", GREETING, new, name)


@pytest.mark.parametrize("doubtful", EVERY_DOUBTFUL_EDIT, ids=lambda edit: edit.name)
def test_an_edit_that_may_change_behaviour_is_never_comment_only(
    adapter: GoAdapter, tmp_path: Path, doubtful: Edit
) -> None:
    assert not adapter.is_comment_only(*pair(tmp_path, doubtful))


@pytest.mark.parametrize("ordinary", ORDINARY_EDITS, ids=lambda edit: edit.name)
def test_an_edit_of_ordinary_comment_lines_is_comment_only(
    adapter: GoAdapter, tmp_path: Path, ordinary: Edit
) -> None:
    assert adapter.is_comment_only(*pair(tmp_path, ordinary))


@pytest.mark.parametrize("tags", TAG_EDITS, ids=lambda edit: edit.name)
def test_the_tags_ARDE_adds_are_comment_only_with_the_neighbours_realigned(
    adapter: GoAdapter, tmp_path: Path, tags: Edit
) -> None:
    assert adapter.is_comment_only(*pair(tmp_path, tags))


@pytest.mark.parametrize("real", DELIBERATELY_REAL_EDITS, ids=lambda edit: edit.name)
def test_these_stay_real_on_purpose(adapter: GoAdapter, tmp_path: Path, real: Edit) -> None:
    assert not adapter.is_comment_only(*pair(tmp_path, real))


def test_a_line_that_starts_with_slashes_inside_a_raw_string_is_real_because_the_trees_differ(
    adapter: GoAdapter, tmp_path: Path
) -> None:
    assert not adapter.is_comment_only(*pair(tmp_path, RAW_STRING_EDIT))


def test_a_changed_line_of_code_is_not_comment_only(adapter: GoAdapter, tmp_path: Path) -> None:
    files = pair(tmp_path, greeting(GREETING.replace('"hello"', '"hi"')))

    assert not adapter.is_comment_only(*files)


def test_a_new_comment_and_a_reformat_are_comment_only(adapter: GoAdapter, tmp_path: Path) -> None:
    reformatted = GREETING.replace("func Hello() string {", "// Added.\nfunc Hello()   string {")

    assert adapter.is_comment_only(*pair(tmp_path, greeting(reformatted)))


def test_a_file_that_does_not_parse_is_never_comment_only(
    adapter: GoAdapter, tmp_path: Path
) -> None:
    broken = "package p\n\n// one\nfunc (\n"

    assert not adapter.is_comment_only(
        *pair(tmp_path, Edit("broken", broken, broken.replace("one", "two")))
    )


def test_the_files_are_compared_whatever_their_names(adapter: GoAdapter, tmp_path: Path) -> None:
    old_file = tmp_path / "a" / "_hidden_test.go"
    new_file = tmp_path / "b" / "other.go"
    old_file.parent.mkdir()
    new_file.parent.mkdir()
    old_file.write_text(GREETING, encoding="utf-8")
    new_file.write_text(GREETING.replace('"hello"', '"hi"'), encoding="utf-8")

    assert not adapter.is_comment_only(old_file, new_file)


def test_a_file_that_is_missing_is_not_comment_only(adapter: GoAdapter, tmp_path: Path) -> None:
    present = tmp_path / "present.go"
    present.write_text(GREETING, encoding="utf-8")
    missing = tmp_path / "missing.go"

    assert not adapter.is_comment_only(present, missing)
    assert not adapter.is_comment_only(missing, present)


def test_a_folder_is_not_comment_only(adapter: GoAdapter, tmp_path: Path) -> None:
    present = tmp_path / "present.go"
    present.write_text(GREETING, encoding="utf-8")

    assert not adapter.is_comment_only(present, tmp_path)
    assert not adapter.is_comment_only(tmp_path, present)


@pytest.mark.parametrize("name", ["template.go.tmpl", "notes.md", "script.txt", "go"])
def test_a_file_that_is_not_go_source_is_not_comment_only(
    adapter: GoAdapter, tmp_path: Path, name: str
) -> None:
    files = pair(tmp_path, greeting(GREETING.replace("says hello", "greets"), name))

    assert not adapter.is_comment_only(*files)


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads any file")
def test_a_file_that_cannot_be_read_is_not_comment_only(adapter: GoAdapter, tmp_path: Path) -> None:
    old_file, new_file = pair(tmp_path, greeting(GREETING.replace("says hello", "greets")))
    new_file.chmod(0o000)
    try:
        assert not adapter.is_comment_only(old_file, new_file)
        assert not adapter.is_comment_only(new_file, old_file)
    finally:
        new_file.chmod(0o644)


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads any file")
def test_a_file_in_a_folder_that_cannot_be_entered_is_not_comment_only(
    adapter: GoAdapter, tmp_path: Path
) -> None:
    old_file, new_file = pair(tmp_path, greeting(GREETING.replace("says hello", "greets")))
    folder = new_file.parent
    folder.chmod(0o000)
    try:
        assert not adapter.is_comment_only(old_file, new_file)
        assert not adapter.is_comment_only(new_file, old_file)
    finally:
        folder.chmod(0o755)


# The whole repo, edited the way ARDE edits it: comment blocks above declarations, swag lines
# above handlers, and the allowed tags on fields with a tag and on fields without one. gofmt then
# realigns what the tags moved. Every changed file must still be comment-only, or a fresh clone
# of unchanged code would send the model a change report for nothing.

EDITS_ARDE_MAKES = {
    "main.go": [
        (
            "func main() {\n",
            "// main starts the shop on the port that SHOP_PORT names.\nfunc main() {\n",
        ),
        ("\tport := ", "\t// the port comes first, because the server needs it\n\tport := "),
    ],
    "settings.go": [
        ("// setting reads", "// setting is read at start.\n// setting reads"),
    ],
    "pricing/pricing.go": [
        ("func Charge(", "// @Summary The charge for some pieces\nfunc Charge("),
    ],
    "web/router.go": [
        (
            "type router struct {\n\tmux *http.ServeMux\n}",
            "type router struct {\n\t// mux is where every route lands.\n\tmux *http.ServeMux\n}",
        ),
    ],
    "web/server.go": [
        (
            "type item struct {\n\tsku        string\n\ttitle      string\n\tpriceCents int64\n}",
            "// item is one line of the price list.\ntype item struct {\n"
            '\tsku        string `binding:"optional"`\n\ttitle      string\n'
            '\tpriceCents int64  `oneOf:"450,1200"`\n}',
        ),
        (
            "func (s *server) find(",
            "// find looks an item up by its sku.\n//\n// @Description Looks an item up.\n"
            "func (s *server) find(",
        ),
    ],
    "web/handlers.go": [
        (
            "func (s *server) listing(",
            "// listing answers the front page.\n// @Summary List the items\n"
            "// @Router / [get]\nfunc (s *server) listing(",
        ),
        (
            "func (s *server) quote(",
            "// quote prices some pieces of one item.\n// @Summary Price an item\n"
            '// @Param pieces query int false "How many pieces" minimum(1) maximum(50) default(1)\n'
            "// @Router /items/{sku} [get]\nfunc (s *server) quote(",
        ),
        ("\tpieces := 1\n", "\t// one piece unless the caller asks for more\n\tpieces := 1\n"),
    ],
    "web/payloads.go": [
        (
            '\tNote        string `json:"note,omitempty"`',
            '\tNote        string `json:"note,omitempty" binding:"optional"`',
        ),
        (
            '\tDetails     any    `json:"details"`',
            '\tDetails     any    `json:"details" oneOf:"a,b"`',
        ),
        (
            "\tcacheKey    string\n",
            '\t// cacheKey is never sent.\n\tcacheKey    string `binding:"optional"`\n',
        ),
        (
            '\tIssuedBy string `json:"issued_by"`',
            '\t// IssuedBy names the service.\n\tIssuedBy string `json:"issued_by"`',
        ),
    ],
    "web/failures.go": [
        (
            'type problem struct {\n\tCode string `json:"code"`',
            "// problem is the body of a refusal.\ntype problem struct {\n"
            '\tCode string `json:"code" binding:"optional"`',
        ),
    ],
}


def annotate_the_way_arde_does(repo: Path) -> None:
    for name, replacements in EDITS_ARDE_MAKES.items():
        path = repo / name
        text = path.read_text(encoding="utf-8")
        for old, new in replacements:
            assert old in text, f"{old!r} is not in {name}"
            text = text.replace(old, new, 1)
        path.write_text(text, encoding="utf-8")
    subprocess.run(["gofmt", "-w", str(repo)], check=True)


def go_files(repo: Path) -> list[Path]:
    return sorted(path.relative_to(repo) for path in repo.rglob("*.go"))


def test_a_whole_repo_annotated_the_way_arde_does_is_comment_only_in_every_changed_file(
    adapter: GoAdapter, tmp_path: Path
) -> None:
    annotated = tmp_path / "annotated"
    shutil.copytree(SHOP, annotated)
    annotate_the_way_arde_does(annotated)

    changed = [
        name
        for name in go_files(SHOP)
        if (SHOP / name).read_bytes() != (annotated / name).read_bytes()
    ]

    assert len(changed) == len(EDITS_ARDE_MAKES), "every file in the plan was changed"
    assert [
        name for name in changed if not adapter.is_comment_only(SHOP / name, annotated / name)
    ] == []


def test_the_annotated_repo_is_still_real_when_one_line_of_code_changes_too(
    adapter: GoAdapter, tmp_path: Path
) -> None:
    annotated = tmp_path / "annotated"
    shutil.copytree(SHOP, annotated)
    annotate_the_way_arde_does(annotated)
    handlers = annotated / "web" / "handlers.go"
    handlers.write_text(handlers.read_text().replace("pieces := 1", "pieces := 2"))

    assert not adapter.is_comment_only(SHOP / "web" / "handlers.go", handlers)

"""Comments the compiler or a tool reads. An edit to one is a change, however it looks."""

from pathlib import Path

import pytest
from doubtful_edits import BOM, EVERY_DOUBTFUL_EDIT, ORDINARY_EDITS, DoubtfulEdit

from arde.adapters.language.go.directives import (
    anchored_directives,
    comment_edit_may_change_behaviour,
    holds_example,
    is_directive,
    may_import_c,
    read_source,
)

EVERY_FORM_THE_DESIGN_NAMES = (
    "//go:build ignore",
    "//go:debug x=1",
    "//go:embed a.txt",
    "//nolint:unused",
    "//line a.go:1",
    "//export F",
    "// +build x",
    "/*line a.go:1*/",
    "// #cgo LDFLAGS: -lm",
    "// Output: 1",
    "// Unordered output: 1",
)


@pytest.mark.parametrize("line", EVERY_FORM_THE_DESIGN_NAMES)
def test_directive_pattern_catches_all_spec_forms(line: str) -> None:
    assert is_directive(line)


@pytest.mark.parametrize("line", [f"\t{line}" for line in EVERY_FORM_THE_DESIGN_NAMES])
def test_a_directive_may_be_indented(line: str) -> None:
    assert is_directive(line)


@pytest.mark.parametrize("line", ["//GO:BUILD ignore", "// OUTPUT: 1", "//EXPORT F"])
def test_a_directive_is_matched_whatever_its_case(line: str) -> None:
    assert is_directive(line)


@pytest.mark.parametrize(
    "line",
    [
        "// ordinary comment",
        "// TODO: fix this",
        "//  see https://example.com/x",
        "// Outputs are written once",
        "x := 1 // go:build",
        "func main() {",
        "",
    ],
)
def test_an_ordinary_line_is_not_a_directive(line: str) -> None:
    assert not is_directive(line)


@pytest.mark.parametrize(
    "line",
    [
        "var X = 1 //go:noinline",
        "func /*line a.go:1*/ f() {}",
        "x := 1 // +build linux",
        "x := 1 // #cgo LDFLAGS: -lm",
    ],
)
def test_a_directive_form_in_the_middle_or_at_the_end_of_a_line_is_a_directive(line: str) -> None:
    assert is_directive(line)


def test_a_directive_is_paired_with_the_code_it_applies_to() -> None:
    source = "//go:build linux\n\npackage main\n\n//go:noinline\nfunc f() {}\n"

    assert anchored_directives(source) == [
        ("//go:build linux", "package main"),
        ("//go:noinline", "func f() {}"),
    ]


def test_comments_and_blank_lines_between_a_directive_and_its_code_are_skipped() -> None:
    source = "//go:noinline\n// a doc line\n\n// another\nfunc f() {}\n"

    assert anchored_directives(source) == [("//go:noinline", "func f() {}")]


def test_a_directive_at_the_end_of_a_file_applies_to_nothing() -> None:
    assert anchored_directives("package p\n//go:noinline\n") == [("//go:noinline", "")]


def test_the_line_ending_and_the_indentation_do_not_matter() -> None:
    crlf = "//go:build a\r\npackage p\r\n"
    indented = "\t//go:build a\n  package p\n"

    assert anchored_directives(crlf) == anchored_directives(indented)


def test_an_ordinary_comment_added_next_to_a_directive_changes_nothing() -> None:
    before = "//go:embed a.txt\nvar data string\n"
    after = "// data holds the file.\n//go:embed a.txt\n// It is read once.\nvar data string\n"

    assert anchored_directives(before) == anchored_directives(after)


def test_a_trailing_directive_is_listed_with_its_line() -> None:
    assert anchored_directives("var X = 1 //go:noinline\nvar Y = 2\n") == [
        ("var X = 1 //go:noinline", "var Y = 2")
    ]


def test_a_plus_build_line_is_paired_with_the_raw_line_after_it() -> None:
    with_blank_line = "// +build ignore\n\npackage p\n"
    without = "// +build ignore\npackage p\n"

    assert anchored_directives(with_blank_line) == [("// +build ignore", "")]
    assert anchored_directives(with_blank_line) != anchored_directives(without)


def test_a_go_build_line_is_paired_with_the_next_code_line_as_go_ignores_the_blank_line() -> None:
    with_blank_line = "//go:build ignore\n\npackage p\n"
    without = "//go:build ignore\npackage p\n"

    assert anchored_directives(with_blank_line) == anchored_directives(without)


def test_directives_that_swap_places_between_identical_code_lines_are_a_different_list() -> None:
    before = "// Output: a\n}\n// Output: b\n}\n"
    after = "// Output: b\n}\n// Output: a\n}\n"

    assert anchored_directives(before) != anchored_directives(after)
    assert sorted(anchored_directives(before)) == sorted(anchored_directives(after))


def test_a_changed_directive_is_a_different_list() -> None:
    assert anchored_directives("//go:embed a.txt\nvar d string\n") != anchored_directives(
        "//go:embed b.txt\nvar d string\n"
    )


def test_a_directive_moved_to_other_code_is_a_different_list() -> None:
    before = "//go:embed a.txt\nvar first string\nvar second string\n"
    after = "var first string\n//go:embed a.txt\nvar second string\n"

    assert anchored_directives(before) != anchored_directives(after)


def test_two_directives_that_swap_places_are_a_different_list() -> None:
    before = "//go:embed a.txt\nvar a string\n//go:embed b.txt\nvar b string\n"
    after = "//go:embed b.txt\nvar a string\n//go:embed a.txt\nvar b string\n"

    assert anchored_directives(before) != anchored_directives(after)


@pytest.mark.parametrize(
    "source",
    [
        'import "C"',
        'import "C" // the preamble is above',
        'import (\n\t"fmt"\n\t"C"\n)',
        'import C "C"',
        "import `C`",
        '\timport "C"',
        'import "C"; import "fmt"',
        'import "C" /* a note */',
        'import /* a note */ "C"',
        'import ("fmt"; "C")',
        'name := "C"',
        '// import "C" is described here',
    ],
)
def test_text_that_may_import_c_is_taken_for_a_cgo_file(source: str) -> None:
    assert may_import_c(f"package p\n\n{source}\n")


@pytest.mark.parametrize("source", ['import "fmt"', 'import "Cx"', 'import "pkg/C"', ""])
def test_a_file_with_no_c_import_text_is_not_a_cgo_file(source: str) -> None:
    assert not may_import_c(f"package p\n\n{source}\n")


@pytest.mark.parametrize(
    "source",
    [
        "func ExampleHi() {",
        "func Example() {",
        "func ExampleServer_start() {",
        "func  ExampleHi() {",
        "func Examples() {",
    ],
)
def test_a_file_with_an_example_function_holds_an_example(source: str) -> None:
    assert holds_example(f"package p\n\n{source}\n}}\n")


@pytest.mark.parametrize(
    "source",
    [
        "func Run() {",
        "func (s *S) ExampleHi() {",
        "// func ExampleHi() {",
        "\tfunc ExampleHi() {",
        'var s = "func ExampleHi"',
    ],
)
def test_a_file_with_no_example_function_holds_no_example(source: str) -> None:
    assert not holds_example(f"package p\n\n{source}\n")


def written_as_files(tmp_path: Path, edit: DoubtfulEdit) -> tuple[str, str]:
    old, new = tmp_path / "old.go", tmp_path / "new.go"
    old.write_text(edit.old, encoding="utf-8")
    new.write_text(edit.new, encoding="utf-8")
    return read_source(old), read_source(new)


@pytest.mark.parametrize("doubtful", EVERY_DOUBTFUL_EDIT, ids=lambda edit: edit.name)
def test_doubt_about_an_edit_is_answered_as_a_change(
    tmp_path: Path, doubtful: DoubtfulEdit
) -> None:
    assert comment_edit_may_change_behaviour(*written_as_files(tmp_path, doubtful))


@pytest.mark.parametrize("ordinary", ORDINARY_EDITS, ids=lambda edit: edit.name)
def test_an_ordinary_comment_edit_raises_no_doubt(tmp_path: Path, ordinary: DoubtfulEdit) -> None:
    assert not comment_edit_may_change_behaviour(*written_as_files(tmp_path, ordinary))


def test_a_new_block_comment_raises_doubt_because_it_may_swallow_code() -> None:
    assert comment_edit_may_change_behaviour("// a\nfunc f() {}\n", "/* a */\nfunc f() {}\n")


def test_a_block_comment_that_is_only_reworded_raises_no_doubt() -> None:
    assert not comment_edit_may_change_behaviour("/* a */\nvar x = 1\n", "/* b */\nvar x = 1\n")


def test_a_byte_order_mark_does_not_hide_the_first_line(tmp_path: Path) -> None:
    path = tmp_path / "x.go"
    path.write_text(BOM + "//go:build ignore\n\npackage p\n", encoding="utf-8")

    assert anchored_directives(read_source(path)) == [("//go:build ignore", "package p")]


def test_a_file_that_is_not_utf8_is_still_read(tmp_path: Path) -> None:
    path = tmp_path / "x.go"
    path.write_bytes(b"package p\n\n// caf\xe9\n")

    assert "package p" in read_source(path)

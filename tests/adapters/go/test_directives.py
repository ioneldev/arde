"""Comments the compiler or a tool reads. An edit to one is a change, however it looks."""

import pytest

from arde.adapters.language.go.directives import anchored_directives, imports_cgo, is_directive

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
    ],
)
def test_a_file_that_imports_c_is_a_cgo_file(source: str) -> None:
    assert imports_cgo(f"package p\n\n{source}\n")


@pytest.mark.parametrize(
    "source",
    ['import "fmt"', 'import "Cx"', '// import "C" is described here', 'name := "C"', ""],
)
def test_a_file_that_does_not_import_c_is_not_a_cgo_file(source: str) -> None:
    assert not imports_cgo(f"package p\n\n{source}\n")

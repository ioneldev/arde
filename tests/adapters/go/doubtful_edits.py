"""Edits that look like comment edits and are not. Each was measured on the real toolchain.

Every pair below passed as comment-only before, and changed what the build or the program does:
the effect is named beside it. Doubt answers False, because a wrongly false answer costs a
fraction of a cent and a wrongly true one ignores a real change.
"""

from typing import NamedTuple

BOM = "﻿"


class DoubtfulEdit(NamedTuple):
    name: str
    old: str
    new: str


_IMPORTS_FMT = 'package p\n\nimport "fmt"\n\n'


def _example(body: str) -> str:
    return _IMPORTS_FMT + 'func ExampleHi() {\n\tfmt.Println("hi")\n' + body + "}\n"


_TWO_EXAMPLES = (
    _IMPORTS_FMT
    + 'func ExampleA() {{\n\tfmt.Println("a")\n{a}}}\n\n'
    + 'func ExampleB() {{\n\tfmt.Println("b")\n{b}}}\n'
)

# go test compares the output of an example with the last comment group of its body.
EXAMPLE_EDITS = (
    DoubtfulEdit(
        "a continuation line of the expected output is edited (passes, then fails)",
        _example("\t// Output:\n\t// hi\n\t// x\n"),
        _example("\t// Output:\n\t// hi\n\t// y\n"),
    ),
    DoubtfulEdit(
        "an output in a block comment is edited (passes, then fails)",
        _example("\t/* Output: hi */\n"),
        _example("\t/* Output: bye */\n"),
    ),
    DoubtfulEdit(
        "an output moves to the next example (the first stops running, the second fails)",
        _TWO_EXAMPLES.format(a="\t// Output: a\n", b=""),
        _TWO_EXAMPLES.format(a="", b="\t// Output: a\n"),
    ),
    DoubtfulEdit(
        "a note above a wrong output (a failing example stops running)",
        _example("\t// Output: wrong\n"),
        _example("\t// a note\n\t// Output: wrong\n"),
    ),
    DoubtfulEdit(
        "a note after an output, past a blank line (the build fails)",
        _example("\t// Output: wrong\n"),
        _example("\t// Output: wrong\n\n\t// trailing note\n"),
    ),
    DoubtfulEdit(
        "a note below an output (passes, then fails)",
        _example("\t// Output: hi\n"),
        _example("\t// Output: hi\n\t// a note\n"),
    ),
    DoubtfulEdit(
        "two examples swap their outputs",
        _TWO_EXAMPLES.format(a="\t// Output: a\n", b="\t// Output: b\n"),
        _TWO_EXAMPLES.format(a="\t// Output: b\n", b="\t// Output: a\n"),
    ),
)

DIRECTIVE_EDITS = (
    DoubtfulEdit(
        "a byte order mark hides the build constraint (the file enters the build)",
        BOM + "//go:build ignore\n\npackage p\n",
        BOM + "//go:build darwin\n\npackage p\n",
    ),
    DoubtfulEdit(
        "a byte order mark hides a build constraint that is then removed",
        BOM + "//go:build ignore\n\npackage p\n",
        BOM + "package p\n",
    ),
    DoubtfulEdit(
        "the blank line after a // +build line goes (the file enters the build)",
        "// +build ignore\n\npackage p\n",
        "// +build ignore\npackage p\n",
    ),
    DoubtfulEdit(
        "a trailing //go:noinline is added (the build fails: misplaced directive)",
        "package p\n\nvar X = 1\n",
        "package p\n\nvar X = 1 //go:noinline\n",
    ),
    DoubtfulEdit(
        "a /*line*/ directive in the middle of a line is retargeted (runtime.Caller changes)",
        'package main\n\nimport "runtime"\n\nfunc main() {\n'
        "\t_, _, _, _ = /*line a.go:100:1*/ runtime.Caller(0)\n}\n",
        'package main\n\nimport "runtime"\n\nfunc main() {\n'
        "\t_, _, _, _ = /*line b.go:200:1*/ runtime.Caller(0)\n}\n",
    ),
    DoubtfulEdit(
        "a block comment closed by // */ swallows a live directive (can inline: 0 to 1)",
        "package p\n\n//\n//go:noinline\n// */\nfunc f() {}\n",
        "package p\n\n/*\n//go:noinline\n// */\nfunc f() {}\n",
    ),
)

_CGO_MAIN = (
    'package main\n\nimport "fmt"\n\n// #define LIMIT {limit}\n{import_c}\n\n'
    "func main() {{ fmt.Println(C.LIMIT) }}\n"
)

CGO_EDITS = (
    DoubtfulEdit(
        'import "C" followed by another import on the line (prints 10, then 20)',
        'package main\n\n// #define LIMIT 10\nimport "C"; import "fmt"\n\n'
        "func main() { fmt.Println(C.LIMIT) }\n",
        'package main\n\n// #define LIMIT 20\nimport "C"; import "fmt"\n\n'
        "func main() { fmt.Println(C.LIMIT) }\n",
    ),
    DoubtfulEdit(
        'import "C" followed by a block comment (prints 10, then 20)',
        _CGO_MAIN.format(limit=10, import_c='import "C" /* a note */'),
        _CGO_MAIN.format(limit=20, import_c='import "C" /* a note */'),
    ),
)

EVERY_DOUBTFUL_EDIT = (*EXAMPLE_EDITS, *DIRECTIVE_EDITS, *CGO_EDITS)

# Edits that change nothing a compiler reads. These stay comment-only.
ORDINARY_EDITS = (
    DoubtfulEdit(
        "a doc comment is reworded",
        "package p\n\n// F does a thing.\nfunc F() {}\n",
        "package p\n\n// F does another thing, at length.\nfunc F() {}\n",
    ),
    DoubtfulEdit(
        "a comment is added inside a body",
        "package p\n\nfunc F() {\n\tprintln(1)\n}\n",
        "package p\n\nfunc F() {\n\t// say one\n\tprintln(1)\n}\n",
    ),
    DoubtfulEdit(
        "a trailing comment is reworded",
        "package p\n\nvar X = 1 // old\n",
        "package p\n\nvar X = 1 // new\n",
    ),
    DoubtfulEdit(
        "a block comment is reworded",
        "package p\n\n/* old note */\nvar X = 1\n",
        "package p\n\n/* new note */\nvar X = 1\n",
    ),
    DoubtfulEdit(
        "an ordinary comment is added next to a directive",
        "package p\n\n//go:noinline\nfunc F() {}\n",
        "package p\n\n// F stays out of line.\n//go:noinline\nfunc F() {}\n",
    ),
    DoubtfulEdit(
        "a byte order mark is added",
        "package p\n\n// a\nfunc F() {}\n",
        BOM + "package p\n\n// b\nfunc F() {}\n",
    ),
)

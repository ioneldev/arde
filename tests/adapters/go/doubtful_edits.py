"""Edits that look like comment edits and are not, and edits that are. Each was measured.

Every pair in the first groups passed as comment-only at some point and changed what the build or
the program does: the effect is named beside it. Doubt answers False, because a wrongly false
answer costs a fraction of a cent and a wrongly true one ignores a real change.
"""

from typing import NamedTuple

BOM = "﻿"


class Edit(NamedTuple):
    name: str
    old: str
    new: str
    # go test runs examples from _test.go files only, and the rule treats those files apart.
    file_name: str = "x.go"


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
    Edit(
        "a continuation line of the expected output is edited (passes, then fails)",
        _example("\t// Output:\n\t// hi\n\t// x\n"),
        _example("\t// Output:\n\t// hi\n\t// y\n"),
        "x_test.go",
    ),
    Edit(
        "an output in a block comment is edited (passes, then fails)",
        _example("\t/* Output: hi */\n"),
        _example("\t/* Output: bye */\n"),
        "x_test.go",
    ),
    Edit(
        "an output moves to the next example (the first stops running, the second fails)",
        _TWO_EXAMPLES.format(a="\t// Output: a\n", b=""),
        _TWO_EXAMPLES.format(a="", b="\t// Output: a\n"),
        "x_test.go",
    ),
    Edit(
        "a note above a wrong output (a failing example stops running)",
        _example("\t// Output: wrong\n"),
        _example("\t// a note\n\t// Output: wrong\n"),
        "x_test.go",
    ),
    Edit(
        "a note after an output, past a blank line (the build fails)",
        _example("\t// Output: wrong\n"),
        _example("\t// Output: wrong\n\n\t// trailing note\n"),
        "x_test.go",
    ),
    Edit(
        "a note below an output (passes, then fails)",
        _example("\t// Output: hi\n"),
        _example("\t// Output: hi\n\t// a note\n"),
        "x_test.go",
    ),
    Edit(
        "two examples swap their outputs",
        _TWO_EXAMPLES.format(a="\t// Output: a\n", b="\t// Output: b\n"),
        _TWO_EXAMPLES.format(a="\t// Output: b\n", b="\t// Output: a\n"),
        "x_test.go",
    ),
    Edit(
        "an indented func Example, with a note above the output",
        _IMPORTS_FMT + "  func ExampleA() {\n\tfmt.Println(1)\n\t// Output: 2\n}\n",
        _IMPORTS_FMT + "  func ExampleA() {\n\tfmt.Println(1)\n\t// a note\n\t// Output: 2\n}\n",
        "x_test.go",
    ),
    Edit(
        "func /* c */ ExampleA, with a note above the output",
        _IMPORTS_FMT + "func /* c */ ExampleA() {\n\tfmt.Println(1)\n\t// Output: 2\n}\n",
        _IMPORTS_FMT
        + "func /* c */ ExampleA() {\n\tfmt.Println(1)\n\t// a note\n\t// Output: 2\n}\n",
        "x_test.go",
    ),
)

DIRECTIVE_EDITS = (
    Edit(
        "a byte order mark hides the build constraint (the file enters the build)",
        BOM + "//go:build ignore\n\npackage p\n",
        BOM + "//go:build darwin\n\npackage p\n",
    ),
    Edit(
        "a byte order mark hides a build constraint that is then removed",
        BOM + "//go:build ignore\n\npackage p\n",
        BOM + "package p\n",
    ),
    Edit(
        "the blank line after a // +build line goes (the file enters the build)",
        "// +build ignore\n\npackage p\n",
        "// +build ignore\npackage p\n",
    ),
    Edit(
        "a trailing //go:noinline is added (the build fails: misplaced directive)",
        "package p\n\nvar X = 1\n",
        "package p\n\nvar X = 1 //go:noinline\n",
    ),
    Edit(
        "a /*line*/ directive in the middle of a line is retargeted (runtime.Caller changes)",
        'package main\n\nimport "runtime"\n\nfunc main() {\n'
        "\t_, _, _, _ = /*line a.go:100:1*/ runtime.Caller(0)\n}\n",
        'package main\n\nimport "runtime"\n\nfunc main() {\n'
        "\t_, _, _, _ = /*line b.go:200:1*/ runtime.Caller(0)\n}\n",
    ),
    Edit(
        "a block comment closed by // */ swallows a live directive (can inline: 0 to 1)",
        "package p\n\n//\n//go:noinline\n// */\nfunc f() {}\n",
        "package p\n\n/*\n//go:noinline\n// */\nfunc f() {}\n",
    ),
)

# The re-review: a // +build line is read by position, with or without a //go:build line.
PLUS_BUILD_EDITS = (
    Edit(
        "// +build ignore moves below the package clause (the file enters the build)",
        "// +build ignore\n\npackage p\n",
        "package p\n\n// +build ignore\n",
    ),
    Edit(
        "the same, with a doc comment kept",
        "// +build ignore\n\n// Package p does things.\npackage p\n",
        "// Package p does things.\npackage p\n\n// +build ignore\n",
    ),
    Edit(
        "// +build ignore moves above the package clause (the file leaves the build)",
        "package p\n\n// +build ignore\n",
        "// +build ignore\n\npackage p\n",
    ),
    Edit(
        "a block comment moves above // +build ignore",
        "// +build ignore\n\n/* note */\npackage p\n",
        "/* note */\n// +build ignore\n\npackage p\n",
    ),
    Edit(
        "a licence comment sits between // +build ignore and the blank line, which goes",
        "// +build ignore\n// licence\n\npackage p\n",
        "// +build ignore\n// licence\npackage p\n",
    ),
    Edit(
        "the same, with the blank line added",
        "// +build ignore\n// licence\npackage p\n",
        "// +build ignore\n// licence\n\npackage p\n",
    ),
)

_PRINTS_TWICE = 'package main\n\nimport "fmt"\n\nfunc main() {{\n{body}}}\n'

HARDER_EDITS = (
    Edit(
        "// /* becomes /*, closed by a // */ line that swallows a live directive",
        "package p\n\n// /*\n//go:noinline\n// */\nfunc f() {}\n",
        "package p\n\n/*\n//go:noinline\n// */\nfunc f() {}\n",
    ),
    Edit(
        "a /* a */ comment loses its */, which a // */ line below then closes",
        "package p\n\n/* a */\n//go:noinline\n// */\nfunc F() {}\n",
        "package p\n\n/* a\n//go:noinline\n// */\nfunc F() {}\n",
    ),
    Edit(
        "a //line directive moves between two identical lines (prints 100 101, then 14 100)",
        _PRINTS_TWICE.format(body="\tfmt.Println(1)\n\t//line a.go:100\n\tfmt.Println(1)\n"),
        _PRINTS_TWICE.format(body="\t//line a.go:100\n\tfmt.Println(1)\n\tfmt.Println(1)\n"),
    ),
    Edit(
        "a //line directive is indented off column 1 (it stops being one)",
        "package p\n\n//line a.go:100\nvar X = 1\n",
        "package p\n\n\t//line a.go:100\nvar X = 1\n",
    ),
)

CGO_EDITS = (
    Edit(
        'import "C" followed by another import on the line (prints 10, then 20)',
        'package main\n\n// #define LIMIT 10\nimport "C"; import "fmt"\n\n'
        "func main() { fmt.Println(C.LIMIT) }\n",
        'package main\n\n// #define LIMIT 20\nimport "C"; import "fmt"\n\n'
        "func main() { fmt.Println(C.LIMIT) }\n",
    ),
    Edit(
        'import "C" followed by a block comment (prints 10, then 20)',
        'package main\n\nimport "fmt"\n\n// #define LIMIT 10\nimport "C" /* a note */\n\n'
        "func main() { fmt.Println(C.LIMIT) }\n",
        'package main\n\nimport "fmt"\n\n// #define LIMIT 20\nimport "C" /* a note */\n\n'
        "func main() { fmt.Println(C.LIMIT) }\n",
    ),
    Edit(
        "a plain comment in a file that imports C",
        'package main\n\n// #define LIMIT 10\nimport "C"\n\n// starts.\nfunc run() {}\n',
        'package main\n\n// #define LIMIT 10\nimport "C"\n\n// begins.\nfunc run() {}\n',
    ),
)

EVERY_DOUBTFUL_EDIT = (
    *EXAMPLE_EDITS,
    *DIRECTIVE_EDITS,
    *PLUS_BUILD_EDITS,
    *HARDER_EDITS,
    *CGO_EDITS,
)

# Edits that change nothing a compiler reads: ordinary comment lines. These stay comment-only.
ORDINARY_EDITS = (
    Edit(
        "a doc comment is reworded",
        "package p\n\n// F does a thing.\nfunc F() {}\n",
        "package p\n\n// F does another thing, at length.\nfunc F() {}\n",
    ),
    Edit(
        "a comment line is added inside a body",
        "package p\n\nfunc F() {\n\tprintln(1)\n}\n",
        "package p\n\nfunc F() {\n\t// say one\n\tprintln(1)\n}\n",
    ),
    Edit(
        "a comment line is removed",
        "package p\n\nfunc F() {\n\t// say one\n\tprintln(1)\n}\n",
        "package p\n\nfunc F() {\n\tprintln(1)\n}\n",
    ),
    Edit(
        "an ordinary comment is added next to a directive",
        "package p\n\n//go:noinline\nfunc F() {}\n",
        "package p\n\n// F stays out of line.\n//go:noinline\nfunc F() {}\n",
    ),
    Edit(
        "swag lines are added above a handler",
        "package p\n\n// Show writes one item.\nfunc Show() {}\n",
        "package p\n\n// Show writes one item.\n// @Summary Show one item\n"
        '// @Param qty query int false "Units to price" minimum(1) maximum(10) default(1)\n'
        "// @Router /items/{sku} [get]\nfunc Show() {}\n",
    ),
    Edit(
        "a field comment is edited",
        "package p\n\ntype T struct {\n\t// Title is the shown name.\n\tTitle string\n}\n",
        "package p\n\ntype T struct {\n\t// Title is what a customer sees.\n\tTitle string\n}\n",
    ),
    Edit(
        "a comment is indented differently",
        "package p\n\nfunc F() {\n\t// note\n\tprintln(1)\n}\n",
        "package p\n\nfunc F() {\n  // note\n\tprintln(1)\n}\n",
    ),
    Edit(
        "a byte order mark is added with a comment edit",
        "package p\n\n// a\nfunc F() {}\n",
        BOM + "package p\n\n// b\nfunc F() {}\n",
    ),
    Edit(
        "the line endings change with a comment edit",
        "package p\n\n// a\nfunc F() {}\n",
        "package p\r\n\r\n// b\r\nfunc F() {}\r\n",
    ),
    Edit(
        "the line endings change beside a directive and a block comment, with a comment edit",
        "package p\n\n//go:noinline\n/* note */\nfunc F() {}\n// a\n",
        "package p\r\n\r\n//go:noinline\r\n/* note */\r\nfunc F() {}\r\n// b\r\n",
    ),
    Edit(
        "a spaced go:build look-alike is edited (the compiler reads no such line)",
        "package p\n\n// go:build linux\nfunc F() {}\n",
        "package p\n\n// go:build darwin\nfunc F() {}\n",
    ),
    Edit(
        "an example's note is edited in a file that is not a test",
        "package p\n\n// ExampleHi is documented here.\nfunc ExampleHi() {}\n",
        "package p\n\n// ExampleHi is described here.\nfunc ExampleHi() {}\n",
    ),
)

_FIELDS = "package p\n\ntype T struct {{\n{lines}}}\n"

# The tags ARDE adds, on a field with a tag and on one without, with the neighbours realigned
# the way gofmt realigns them.
TAG_EDITS = (
    Edit(
        "binding optional is added to a field with a tag",
        _FIELDS.format(lines='\tA  int    `json:"a"`\n\tBb string `json:"bb"`\n'),
        _FIELDS.format(
            lines='\tA  int    `json:"a" binding:"optional"`\n\tBb string `json:"bb"`\n'
        ),
    ),
    Edit(
        "oneOf is added to a field with no tag, and the columns move",
        _FIELDS.format(lines="\tA int\n\tBb string\n"),
        _FIELDS.format(lines='\tA  int    `oneOf:"1,2"`\n\tBb string\n'),
    ),
    Edit(
        "both tags are added to neighbours and every column is realigned",
        _FIELDS.format(lines='\tA  int    `json:"a"`\n\tBb string `json:"bb"`\n\tC  bool\n'),
        _FIELDS.format(
            lines='\tA  int    `json:"a" oneOf:"1,2"`\n\tBb string `json:"bb"`\n'
            '\tC  bool   `binding:"optional"`\n'
        ),
    ),
    Edit(
        "a tag is added before the json tag",
        _FIELDS.format(lines='\tA int `json:"a"`\n'),
        _FIELDS.format(lines='\tA int `binding:"optional" json:"a"`\n'),
    ),
    Edit(
        "both tags are removed again",
        _FIELDS.format(
            lines='\tA  int    `json:"a" oneOf:"1,2"`\n\tC  bool   `binding:"optional"`\n'
        ),
        _FIELDS.format(lines='\tA int `json:"a"`\n\tC bool\n'),
    ),
    Edit(
        "a tag is added beside trailing comments, which move with the columns",
        _FIELDS.format(lines='\tA int `json:"a"` // first\n\tBb string `json:"bb"` // second\n'),
        _FIELDS.format(
            lines='\tA  int    `json:"a" binding:"optional"` // first\n'
            '\tBb string `json:"bb"`                    // second\n'
        ),
    ),
)

# Deliberately real. The rule leaves these to the model, because each could hide a change.
DELIBERATELY_REAL_EDITS = (
    Edit(
        "a block comment is edited",
        "package p\n\n/* old note */\nvar X = 1\n",
        "package p\n\n/* new note */\nvar X = 1\n",
    ),
    Edit(
        "a trailing comment is edited",
        "package p\n\nvar X = 1 // old\n",
        "package p\n\nvar X = 1 // new\n",
    ),
    Edit(
        "a blank line is added",
        "package p\n\nvar X = 1\nvar Y = 2\n",
        "package p\n\nvar X = 1\n\nvar Y = 2\n",
    ),
    Edit(
        "binding required is added, which changes how a request is checked",
        _FIELDS.format(lines='\tA int `json:"a"`\n'),
        _FIELDS.format(lines='\tA int `json:"a" binding:"required"`\n'),
    ),
    Edit(
        "a line of code is edited",
        "package p\n\nfunc F() int {\n\treturn 1\n}\n",
        "package p\n\nfunc F() int {\n\treturn 2\n}\n",
    ),
)

# Looks like an ordinary comment line to the text rule. Only the syntax tree knows it is a string.
RAW_STRING_EDIT = Edit(
    "a line starting // inside a raw string is edited",
    "package p\n\nconst Query = `\n// select 1\n`\n",
    "package p\n\nconst Query = `\n// select 2\n`\n",
)

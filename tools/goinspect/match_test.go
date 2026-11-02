package main

import (
	"encoding/json"
	"reflect"
	"strings"
	"testing"
)

const stdlibPatterns = `[
  {"kind":"route","call":"^mux\\.HandleFunc$","args":{"path":0,"handler":1}},
  {"kind":"param","call":"^r\\.PathValue$|^query\\.Get$","args":{"name":0}},
  {"kind":"error","call":"^writeError$","args":{"status":1,"code":2}}
]`

const ginPatterns = `[
  {"kind":"route","call":"^(r|v1)\\.(GET|POST|PUT|DELETE)$","args":{"path":0,"handler":1}},
  {"kind":"param","call":"^c\\.(Query|DefaultQuery)$","args":{"name":0}},
  {"kind":"error","call":"^respondError$","args":{"status":1,"code":2}}
]`

const stdlibStyleService = `package main

func register(mux *http.ServeMux, h *handlers) {
	mux.HandleFunc("GET /widgets/{id}", h.detail)
}

func (h *handlers) detail(w http.ResponseWriter, r *http.Request) {
	id := r.PathValue("id")
	if id == "" {
		writeError(w, http.StatusBadRequest, "missing_id")
	}
}
`

const ginStyleService = `package main

func register(r *gin.Engine, h *handlers) {
	r.GET("/widgets/:id", h.detail)
}

func (h *handlers) detail(c *gin.Context) {
	if c.Query("colour") == "" {
		respondError(c, 400, "missing_colour")
	}
}
`

type matchedCall struct {
	Kind string            `json:"kind"`
	Call string            `json:"call"`
	File string            `json:"file"`
	Line int               `json:"line"`
	Args map[string]string `json:"args"`
}

func matchesIn(t *testing.T, files map[string]string, patterns string) []matchedCall {
	t.Helper()
	root := writeTree(t, files)
	patternsFile := writeTree(t, map[string]string{"patterns.json": patterns}) + "/patterns.json"

	exitCode, stdout, stderr := runCommand(t, "match", "--patterns", patternsFile, root)

	if exitCode != 0 || stderr != "" {
		t.Fatalf("exit %d, stderr %q", exitCode, stderr)
	}
	var report struct {
		Matches []matchedCall `json:"matches"`
	}
	if err := json.Unmarshal([]byte(stdout), &report); err != nil {
		t.Fatalf("stdout is not the match report: %v\n%s", err, stdout)
	}
	return report.Matches
}

func assertMatches(t *testing.T, got, want []matchedCall) {
	t.Helper()
	if !reflect.DeepEqual(got, want) {
		gotJSON, _ := json.MarshalIndent(got, "", "  ")
		wantJSON, _ := json.MarshalIndent(want, "", "  ")
		t.Fatalf("got\n%s\nwant\n%s", gotJSON, wantJSON)
	}
}

func TestMatchStdlibStyle(t *testing.T) {
	got := matchesIn(t, map[string]string{"main.go": stdlibStyleService}, stdlibPatterns)

	assertMatches(t, got, []matchedCall{
		{Kind: "route", Call: "mux.HandleFunc", File: "main.go", Line: 4,
			Args: map[string]string{"path": `"GET /widgets/{id}"`, "handler": "h.detail"}},
		{Kind: "param", Call: "r.PathValue", File: "main.go", Line: 8,
			Args: map[string]string{"name": `"id"`}},
		{Kind: "error", Call: "writeError", File: "main.go", Line: 10,
			Args: map[string]string{"status": "http.StatusBadRequest", "code": `"missing_id"`}},
	})
}

func TestMatchGinStyle(t *testing.T) {
	got := matchesIn(t, map[string]string{"main.go": ginStyleService}, ginPatterns)

	assertMatches(t, got, []matchedCall{
		{Kind: "route", Call: "r.GET", File: "main.go", Line: 4,
			Args: map[string]string{"path": `"/widgets/:id"`, "handler": "h.detail"}},
		{Kind: "param", Call: "c.Query", File: "main.go", Line: 8,
			Args: map[string]string{"name": `"colour"`}},
		{Kind: "error", Call: "respondError", File: "main.go", Line: 9,
			Args: map[string]string{"status": "400", "code": `"missing_colour"`}},
	})
}

func TestMatchSkipsTestFiles(t *testing.T) {
	got := matchesIn(t, map[string]string{
		"main.go":      stdlibStyleService,
		"main_test.go": strings.Replace(stdlibStyleService, "/widgets/{id}", "/only-in-tests", 1),
	}, stdlibPatterns)

	for _, found := range got {
		if found.File != "main.go" {
			t.Errorf("matched inside %s, a test file", found.File)
		}
	}
	if len(got) != 3 {
		t.Fatalf("want the 3 matches of main.go, got %d", len(got))
	}
}

func TestMatchSkipsVendorAndGitDirs(t *testing.T) {
	got := matchesIn(t, map[string]string{
		"main.go":                  stdlibStyleService,
		"vendor/lib/lib.go":        stdlibStyleService,
		".git/hooks/planted.go":    stdlibStyleService,
		"internal/testdata/fix.go": stdlibStyleService,
		"_scratch/x.go":            stdlibStyleService,
	}, stdlibPatterns)

	for _, found := range got {
		if found.File != "main.go" {
			t.Errorf("matched inside %s, which the go tool ignores", found.File)
		}
	}
	if len(got) != 3 {
		t.Fatalf("want the 3 matches of main.go, got %d", len(got))
	}
}

func TestMatchSortsByFileThenLineThenKind(t *testing.T) {
	paramFirst := "package p\n\nfunc f() { r.PathValue(\"n\"); writeError(w, 400, \"c\") }\n"
	errorFirst := "package p\n\nfunc f() { writeError(w, 400, \"c\"); r.PathValue(\"n\") }\n"
	got := matchesIn(t, map[string]string{
		"z.go":     paramFirst,
		"a/b.go":   errorFirst,
		"a.go":     "package p\n\nfunc f() {\n\tr.PathValue(\"late\")\n\twriteError(w, 400, \"c\")\n}\n",
		"main.go":  "package p\n",
		"first.go": "package p\n\nvar _ = func() { r.PathValue(\"x\") }\n",
	}, stdlibPatterns)

	var order []string
	for _, found := range got {
		order = append(order, found.File+":"+found.Kind)
	}
	want := []string{
		"a.go:param", "a.go:error",
		"a/b.go:error", "a/b.go:param",
		"first.go:param",
		"z.go:error", "z.go:param",
	}
	if !reflect.DeepEqual(order, want) {
		t.Fatalf("got %v, want %v", order, want)
	}
}

func TestMatchLeavesOutAnArgumentTheCallDoesNotHave(t *testing.T) {
	got := matchesIn(t, map[string]string{"main.go": "package p\n\nfunc f() { writeError(w, 400) }\n"}, stdlibPatterns)

	assertMatches(t, got, []matchedCall{
		{Kind: "error", Call: "writeError", File: "main.go", Line: 3,
			Args: map[string]string{"status": "400"}},
	})
}

func TestMatchFindsCallsAtEveryDepth(t *testing.T) {
	source := "package p\n\nvar setup = func() {\n\tmux.HandleFunc(\"GET /a\", func(w http.ResponseWriter, r *http.Request) {\n\t\twriteError(w, 404, r.PathValue(\"id\"))\n\t})\n}\n"

	got := matchesIn(t, map[string]string{"main.go": source}, stdlibPatterns)

	var kinds []string
	for _, found := range got {
		kinds = append(kinds, found.Kind)
	}
	if !reflect.DeepEqual(kinds, []string{"route", "error", "param"}) {
		t.Fatalf("got %v", kinds)
	}
}

func TestMatchReadsWindowsLineEndings(t *testing.T) {
	crlf := strings.ReplaceAll(stdlibStyleService, "\n", "\r\n")

	got := matchesIn(t, map[string]string{"main.go": crlf}, stdlibPatterns)

	want := matchesIn(t, map[string]string{"main.go": stdlibStyleService}, stdlibPatterns)
	assertMatches(t, got, want)
}

func TestMatchPrintsAMultiLineCallAtItsFirstLine(t *testing.T) {
	source := "package p\n\nfunc f() {\n\tmux.HandleFunc(\n\t\t\"GET /a\",\n\t\th.a,\n\t)\n}\n"

	got := matchesIn(t, map[string]string{"main.go": source}, stdlibPatterns)

	assertMatches(t, got, []matchedCall{
		{Kind: "route", Call: "mux.HandleFunc", File: "main.go", Line: 4,
			Args: map[string]string{"path": `"GET /a"`, "handler": "h.a"}},
	})
}

func TestMatchWithNothingFoundPrintsAnEmptyList(t *testing.T) {
	root := writeTree(t, map[string]string{"main.go": "package p\n"})
	patternsFile := writeTree(t, map[string]string{"patterns.json": stdlibPatterns}) + "/patterns.json"

	exitCode, stdout, _ := runCommand(t, "match", "--patterns", patternsFile, root)

	if exitCode != 0 || !strings.HasPrefix(stdout, `{"matches":[],`) {
		t.Fatalf("exit %d, stdout %q", exitCode, stdout)
	}
}

func unmatchedPatternsOf(t *testing.T, root, patterns string) string {
	t.Helper()
	patternsFile := writeTree(t, map[string]string{"patterns.json": patterns}) + "/patterns.json"
	exitCode, stdout, stderr := runCommand(t, "match", "--patterns", patternsFile, root)
	if exitCode != 0 || stderr != "" {
		t.Fatalf("exit %d, stderr %q", exitCode, stderr)
	}
	return stdout[strings.Index(stdout, `"unmatched_patterns"`):]
}

func TestMatchListsEachPatternWithNoMatchUnderItsKind(t *testing.T) {
	root := writeTree(t, map[string]string{"main.go": "package p\n\nfunc f() { used(\"a\") }\n"})
	patterns := `[{"kind":"env","call":"^used$","args":{}},{"kind":"param","call":"^unused$","args":{}}]`

	got := unmatchedPatternsOf(t, root, patterns)

	if want := `"unmatched_patterns":[{"kind":"param","call":"^unused$"}]}` + "\n"; got != want {
		t.Fatalf("got %s, want %s", got, want)
	}
}

func TestMatchTellsApartTwoKindsOfOnePattern(t *testing.T) {
	root := writeTree(t, map[string]string{"main.go": "package p\n\nfunc f() { used(\"a\") }\n"})
	patterns := `[{"kind":"env","call":"^used$","args":{}},{"kind":"param","call":"^used$","args":{}}]`

	got := unmatchedPatternsOf(t, root, patterns)

	if want := `"unmatched_patterns":[]}` + "\n"; got != want {
		t.Fatalf("got %s, want %s", got, want)
	}
}

func TestMatchPrintsAnEmptyListWhenEveryPatternMatched(t *testing.T) {
	root := writeTree(t, map[string]string{"main.go": "package p\n"})

	if got, want := unmatchedPatternsOf(t, root, "[]"), `"unmatched_patterns":[]}`+"\n"; got != want {
		t.Fatalf("got %s, want %s", got, want)
	}
}

func TestMatchFailsOnAFileThatDoesNotParseAndNamesIt(t *testing.T) {
	root := writeTree(t, map[string]string{
		"good.go":       stdlibStyleService,
		"pkg/broken.go": "package p\nfunc (",
	})
	patternsFile := writeTree(t, map[string]string{"patterns.json": stdlibPatterns}) + "/patterns.json"

	exitCode, stdout, stderr := runCommand(t, "match", "--patterns", patternsFile, root)

	if exitCode != 1 || stdout != "" {
		t.Fatalf("exit %d, stdout %q", exitCode, stdout)
	}
	if !strings.Contains(stderr, "pkg/broken.go") {
		t.Fatalf("stderr must name the file, got %q", stderr)
	}
}

func TestMatchIgnoresAFileTheWalkSkipsEvenWhenItDoesNotParse(t *testing.T) {
	got := matchesIn(t, map[string]string{
		"main.go":              stdlibStyleService,
		"vendor/broken.go":     "package p\nfunc (",
		"x_test.go":            "package p\nfunc (",
		"testdata/garbage.go":  "not go at all",
		"notes.txt":            "func (",
		"internal/.hidden.go":  "package p\nfunc (",
		"internal/_hidden2.go": "package p\nfunc (",
	}, stdlibPatterns)

	if len(got) != 3 {
		t.Fatalf("want the 3 matches of main.go, got %d", len(got))
	}
}

func TestMatchRejectsAPatternsFileItCannotUse(t *testing.T) {
	longCall := strings.Repeat("a", 501)
	cases := map[string]struct {
		patterns    string
		mustMention string
	}{
		"a regex that will not compile": {
			patterns:    `[{"kind":"route","call":"^mux\\.HandleFunc$","args":{}},{"kind":"param","call":"^(unclosed","args":{}}]`,
			mustMention: "pattern 2",
		},
		"a call longer than 500 characters": {
			patterns:    `[{"kind":"route","call":"` + longCall + `","args":{}}]`,
			mustMention: "pattern 1",
		},
		"a negative argument index": {
			patterns:    `[{"kind":"route","call":"^a$","args":{"path":0}},{"kind":"route","call":"^b$","args":{"path":-1}}]`,
			mustMention: "pattern 2",
		},
		"an empty call, which would match every call": {
			patterns:    `[{"kind":"route","call":"","args":{}}]`,
			mustMention: "pattern 1",
		},
		"an empty kind": {
			patterns:    `[{"kind":"","call":"^a$","args":{}}]`,
			mustMention: "pattern 1",
		},
		"a misspelt field, which would drop the arguments": {
			patterns:    `[{"kind":"route","call":"^a$","arguments":{"path":0}}]`,
			mustMention: "arguments",
		},
		"an object instead of an array": {
			patterns:    `{"kind":"route","call":"^a$","args":{}}`,
			mustMention: "patterns",
		},
		"text that is not JSON": {
			patterns:    `not json`,
			mustMention: "patterns",
		},
		"text after the array": {
			patterns:    `[] not json`,
			mustMention: "after the array",
		},
		"a second array after the first": {
			patterns:    `[{"kind":"route","call":"^a$","args":{}}] [{"kind":"route","call":"^b$","args":{}}]`,
			mustMention: "after the array",
		},
		"a stray bracket after the array": {
			patterns:    `[] ]`,
			mustMention: "after the array",
		},
		"a file holding null": {
			patterns:    `null`,
			mustMention: "null",
		},
		"a pattern with no args": {
			patterns:    `[{"kind":"route","call":"^a$"}]`,
			mustMention: "pattern 1",
		},
		"a pattern with null args": {
			patterns:    `[{"kind":"route","call":"^a$","args":null}]`,
			mustMention: "args",
		},
	}
	// The tree holds no Go file: a bad pattern must fail before any file is read.
	root := writeTree(t, map[string]string{"README.md": "no go here"})

	for name, test := range cases {
		patternsFile := writeTree(t, map[string]string{"patterns.json": test.patterns}) + "/patterns.json"

		exitCode, stdout, stderr := runCommand(t, "match", "--patterns", patternsFile, root)

		if exitCode != 1 || stdout != "" {
			t.Errorf("%s: exit %d, stdout %q", name, exitCode, stdout)
		}
		if !strings.Contains(stderr, test.mustMention) {
			t.Errorf("%s: stderr must mention %q, got %q", name, test.mustMention, stderr)
		}
	}
}

func TestMatchAcceptsACallOfExactlyFiveHundredCharacters(t *testing.T) {
	patterns := `[{"kind":"route","call":"` + strings.Repeat("a", 500) + `","args":{}}]`

	got := matchesIn(t, map[string]string{"main.go": "package p\n"}, patterns)

	if len(got) != 0 {
		t.Fatalf("got %v", got)
	}
}

// patternsFor builds a patterns file for one route pattern, so a test can write the regex as Go
// source and not as JSON.
func patternsFor(t *testing.T, call string) string {
	t.Helper()
	encoded, err := json.Marshal([]patternSpec{{Kind: "route", Call: call, Args: map[string]int{"path": 0, "handler": 1}}})
	assertNoError(t, err)
	return string(encoded)
}

// The regex runs against the callee written on one line, with its instantiation and its
// parentheses taken off.
func TestMatchNamesTheCalleeTheWayItIsMeantNotTheWayItIsLaidOut(t *testing.T) {
	cases := map[string]struct {
		body     string
		pattern  string
		wantCall string
		wantArgs map[string]string
	}{
		"a call split after the dot": {
			body:     "mux.\n\t\tHandleFunc(\"GET /a\", h.a)",
			pattern:  `^mux\.HandleFunc$`,
			wantCall: "mux.HandleFunc",
			wantArgs: map[string]string{"path": `"GET /a"`, "handler": "h.a"},
		},
		"a selector chain split over lines": {
			body:     "r.\n\tURL.\n\tQuery().\n\tGet(\"colour\")",
			pattern:  `^r\.URL\.Query\(\)\.Get$`,
			wantCall: "r.URL.Query().Get",
			wantArgs: map[string]string{"path": `"colour"`},
		},
		"a call on the result of another call": {
			body:     "r.GET(\"/a\", h.a).\n\tPOST(\"/b\", h.b)",
			pattern:  `\.POST$`,
			wantCall: `r.GET("/a", h.a).POST`,
			wantArgs: map[string]string{"path": `"/b"`, "handler": "h.b"},
		},
		"an explicit instantiation": {
			body:     "writeError[int](\"/a\", h.a)",
			pattern:  `^writeError$`,
			wantCall: "writeError",
			wantArgs: map[string]string{"path": `"/a"`, "handler": "h.a"},
		},
		"an explicit instantiation with two types": {
			body:     "writeError[int, string](\"/a\", h.a)",
			pattern:  `^writeError$`,
			wantCall: "writeError",
			wantArgs: map[string]string{"path": `"/a"`, "handler": "h.a"},
		},
		"a parenthesised callee": {
			body:     "(writeError)(\"/a\", h.a)",
			pattern:  `^writeError$`,
			wantCall: "writeError",
			wantArgs: map[string]string{"path": `"/a"`, "handler": "h.a"},
		},
	}

	for name, test := range cases {
		source := "package p\n\nfunc f() {\n\t" + test.body + "\n}\n"

		got := matchesIn(t, map[string]string{"main.go": source}, patternsFor(t, test.pattern))

		if len(got) != 1 {
			t.Errorf("%s: want 1 match, got %v", name, got)
			continue
		}
		if got[0].Call != test.wantCall || !reflect.DeepEqual(got[0].Args, test.wantArgs) {
			t.Errorf("%s: got call %q args %v, want call %q args %v", name, got[0].Call, got[0].Args, test.wantCall, test.wantArgs)
		}
	}
}

func TestMatchDoesNotTreatAnInvokedFunctionLiteralAsACalleeNamedAfterItsBody(t *testing.T) {
	source := "package p\n\nfunc f() {\n\tfunc() { x.HandleFunc(1) }()\n}\n"

	got := matchesIn(t, map[string]string{"main.go": source}, `[{"kind":"route","call":"HandleFunc","args":{}}]`)

	if len(got) != 1 || got[0].Call != "x.HandleFunc" {
		t.Fatalf("want the one call of x.HandleFunc, got %v", got)
	}
}

func TestMatchReportsAChainedCallAtTheLineOfItsOwnParenthesis(t *testing.T) {
	source := "package p\n\nfunc f() {\n\tr.GET(\"/a\", h.a).\n\t\tPOST(\"/b\", h.b).\n\t\tDELETE(\"/c\", h.c)\n}\n"
	patterns := `[{"kind":"route","call":"\\.(GET|POST|DELETE)$","args":{"path":0}}]`

	got := matchesIn(t, map[string]string{"main.go": source}, patterns)

	var lines []int
	for _, found := range got {
		lines = append(lines, found.Line)
	}
	if !reflect.DeepEqual(lines, []int{4, 5, 6}) {
		t.Fatalf("lines %v, want [4 5 6]", lines)
	}
}

// literalsOf returns the literals object of each match, as the helper printed it.
func literalsOf(t *testing.T, source, patterns string) []map[string]string {
	t.Helper()
	root := writeTree(t, map[string]string{"main.go": source})
	patternsFile := writeTree(t, map[string]string{"patterns.json": patterns}) + "/patterns.json"

	exitCode, stdout, stderr := runCommand(t, "match", "--patterns", patternsFile, root)

	if exitCode != 0 || stderr != "" {
		t.Fatalf("exit %d, stderr %q", exitCode, stderr)
	}
	var report struct {
		Matches []struct {
			Literals map[string]string `json:"literals"`
		} `json:"matches"`
	}
	if err := json.Unmarshal([]byte(stdout), &report); err != nil {
		t.Fatalf("stdout is not the match report: %v\n%s", err, stdout)
	}
	found := []map[string]string{}
	for _, match := range report.Matches {
		found = append(found, match.Literals)
	}
	return found
}

const oneArgumentPattern = `[{"kind":"param","call":"^pick$","args":{"name":0}}]`

func TestMatchReportsTheValueOfEachArgumentThatIsAStringLiteral(t *testing.T) {
	got := literalsOf(t, stdlibStyleService, stdlibPatterns)

	want := []map[string]string{
		{"path": "GET /widgets/{id}"},
		{"name": "id"},
		{"code": "missing_id"},
	}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("got %v, want %v", got, want)
	}
}

func TestMatchDecodesEscapesTheWayTheCompilerDoes(t *testing.T) {
	source := "package main\n\nfunc f() {\n" +
		"\tpick(\"/caf\\xc3\\xa9\")\n" +
		"\tpick(\"tab\\there\")\n" +
		"\tpick(\"\\u00e9\")\n" +
		"\tpick(`raw\\n`)\n" +
		"\tpick(\"\")\n" +
		"}\n"

	got := literalsOf(t, source, oneArgumentPattern)

	want := []map[string]string{
		{"name": "/caf\u00e9"},
		{"name": "tab\there"},
		{"name": "\u00e9"},
		{"name": `raw\n`},
		{"name": ""},
	}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("got %q, want %q", got, want)
	}
}

func TestMatchLeavesOutAnArgumentThatIsNotOneStringLiteral(t *testing.T) {
	source := "package main\n\nfunc f(name string) {\n" +
		"\tpick(name)\n" +
		"\tpick(\"a\" + name)\n" +
		"\tpick('x')\n" +
		"\tpick(400)\n" +
		"\tpick(fmt.Sprintf(\"%s\", name))\n" +
		"\tpick((\"in parentheses\"))\n" +
		"\tpick(prefix + \"suffix\")\n" +
		"}\n"

	got := literalsOf(t, source, oneArgumentPattern)

	if len(got) != 7 {
		t.Fatalf("want the 7 matches, got %d", len(got))
	}
	for index, literals := range got {
		if len(literals) != 0 {
			t.Errorf("match %d: want no literal, got %v", index, literals)
		}
	}
}

func TestMatchLiteralsAreAnEmptyObjectAndNeverNull(t *testing.T) {
	root := writeTree(t, map[string]string{"main.go": "package main\n\nfunc f(n string) {\n\tpick(n)\n}\n"})
	patternsFile := writeTree(t, map[string]string{"patterns.json": oneArgumentPattern}) + "/patterns.json"

	_, stdout, _ := runCommand(t, "match", "--patterns", patternsFile, root)

	if !strings.Contains(stdout, `"literals":{}`) {
		t.Fatalf("want an empty literals object in %s", stdout)
	}
}

func TestMatchLiteralsLeaveOutAPositionPastTheLastArgument(t *testing.T) {
	patterns := `[{"kind":"error","call":"^fail$","args":{"status":1,"code":2}}]`

	got := literalsOf(t, "package main\n\nfunc f() {\n\tfail(w, 400)\n}\n", patterns)

	if want := []map[string]string{{}}; !reflect.DeepEqual(got, want) {
		t.Fatalf("got %v, want %v", got, want)
	}
}

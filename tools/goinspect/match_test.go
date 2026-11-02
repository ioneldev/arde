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

	if exitCode != 0 || stdout != `{"matches":[]}`+"\n" {
		t.Fatalf("exit %d, stdout %q", exitCode, stdout)
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

package main

import (
	"encoding/json"
	"os"
	"os/exec"
	"path/filepath"
	"reflect"
	"slices"
	"strings"
	"testing"
)

const noPatterns = "[]"

type siteOutput struct {
	Kind        string            `json:"kind"`
	Call        string            `json:"call"`
	File        string            `json:"file"`
	Line        int               `json:"line"`
	Args        map[string]string `json:"args"`
	Literals    map[string]string `json:"literals"`
	HandlerID   *string           `json:"handler_id"`
	HandlerFile *string           `json:"handler_file"`
	HandlerLine *int              `json:"handler_line"`
}

type placeOutput struct {
	File string `json:"file"`
	Line int    `json:"line"`
}

type wrapperCallOutput struct {
	Kind string `json:"kind"`
	File string `json:"file"`
	Line int    `json:"line"`
}

type routesOutput struct {
	PackageFolders   []string            `json:"package_folders"`
	BuildFiles       []string            `json:"build_files"`
	Sites            []siteOutput        `json:"sites"`
	StrippedPrefixes []placeOutput       `json:"stripped_prefixes"`
	WrapperPrefixes  []placeOutput       `json:"wrapper_prefixes"`
	WrapperCalls     []wrapperCallOutput `json:"wrapper_calls"`
}

func writePatterns(t *testing.T, patterns string) string {
	t.Helper()
	path := filepath.Join(t.TempDir(), "patterns.json")
	assertNoError(t, os.WriteFile(path, []byte(patterns), 0o644))
	return path
}

func routesOf(t *testing.T, root, mainPackage, patterns string) routesOutput {
	t.Helper()
	exitCode, stdout, stderr := runCommand(t, "routes", "--patterns", writePatterns(t, patterns), "--main", mainPackage, root)
	if exitCode != 0 || stderr != "" {
		t.Fatalf("exit %d, stderr %q", exitCode, stderr)
	}
	var output routesOutput
	if err := json.Unmarshal([]byte(stdout), &output); err != nil {
		t.Fatalf("stdout is not the routes report: %v\n%s", err, stdout)
	}
	return output
}

func sitesOfSource(t *testing.T, source string) []siteOutput {
	t.Helper()
	root := writeModule(t, "example.com/depot", map[string]string{"main.go": source})
	return routesOf(t, root, ".", noPatterns).Sites
}

func sitePaths(sites []siteOutput) []string {
	paths := []string{}
	for _, site := range sites {
		paths = append(paths, site.Literals["path"])
	}
	slices.Sort(paths)
	return paths
}

func onlySite(t *testing.T, source string) siteOutput {
	t.Helper()
	sites := sitesOfSource(t, source)
	if len(sites) != 1 {
		t.Fatalf("want one site, got %+v", sites)
	}
	return sites[0]
}

const handlerFuncs = `
func parcels(w http.ResponseWriter, r *http.Request) {}

func depots(w http.ResponseWriter, r *http.Request) {}

func other(w http.ResponseWriter, r *http.Request) {}
`

func TestRoutesFindsEveryRegistrationWhateverItsReceiverIsCalled(t *testing.T) {
	source := `package main

import "net/http"

type gateway struct{ router *http.ServeMux }

type embedded struct{ *http.ServeMux }

func attach(mux *http.ServeMux) { mux.HandleFunc("/from-a-parameter", parcels) }

func main() {
	first := http.NewServeMux()
	first.HandleFunc("/local", parcels)
	first.Handle("/handle", http.HandlerFunc(depots))
	g := gateway{router: first}
	g.router.HandleFunc("/field", other)
	e := embedded{first}
	e.HandleFunc("/embedded", other)
	http.HandleFunc("/default", parcels)
	http.Handle("/default-handle", http.HandlerFunc(depots))
	attach(first)
}
` + handlerFuncs

	got := sitePaths(sitesOfSource(t, source))

	want := []string{"/default", "/default-handle", "/embedded", "/field", "/from-a-parameter", "/handle", "/local"}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("got %v, want %v", got, want)
	}
}

func TestRoutesDoesNotTakeACallThatOnlyLooksLikeARegistration(t *testing.T) {
	source := `package main

import (
	"context"
	"log/slog"
)

type logSink struct{ inner slog.Handler }

func (l logSink) Handle(path string, value int) {}

func main() {
	sink := logSink{}
	sink.Handle("/not-a-route", 1)
	var h slog.Handler
	_ = h.Handle(context.Background(), slog.Record{})
}
`
	root := writeModule(t, "example.com/depot", map[string]string{"main.go": source})

	if got := routesOf(t, root, ".", noPatterns).Sites; len(got) != 0 {
		t.Fatalf("got %+v, want no site", got)
	}
}

func TestRoutesReadsAPathTheCompilerKnows(t *testing.T) {
	source := `package main

import "net/http"

const prefix = "/api"

const shelves = prefix + "/shelves"

func main() {
	const local = "/local-const"
	mux := http.NewServeMux()
	mux.HandleFunc("/plain", parcels)
	mux.HandleFunc(prefix+"/sum", parcels)
	mux.HandleFunc(shelves, parcels)
	mux.HandleFunc(local, parcels)
	mux.HandleFunc(("/parenthesised"), parcels)
	mux.HandleFunc("GET "+prefix+"/with-method", parcels)
}
` + handlerFuncs

	got := sitePaths(sitesOfSource(t, source))

	want := []string{"/api/shelves", "/api/sum", "/local-const", "/parenthesised", "/plain", "GET /api/with-method"}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("got %v, want %v", got, want)
	}
}

func TestRoutesReportsAPathTheCompilerDoesNotKnowWithItsText(t *testing.T) {
	source := `package main

import "net/http"

type entry struct{ pattern string }

func main() {
	mux := http.NewServeMux()
	for _, e := range []entry{{pattern: "/a"}} {
		mux.HandleFunc(e.pattern, parcels)
	}
}
` + handlerFuncs

	site := onlySite(t, source)

	if _, known := site.Literals["path"]; known {
		t.Errorf("a variable has no value: %+v", site)
	}
	if site.Args["path"] != "e.pattern" {
		t.Errorf("path text %q, want e.pattern", site.Args["path"])
	}
}

func TestRoutesDoesNotPrintAPathPastItsCap(t *testing.T) {
	long := "/" + strings.Repeat("x", 2*maxPrintedBytes)
	source := "package main\n\nimport \"net/http\"\n\nconst far = \"" + long + "\"\n\nfunc main() {\n\thttp.HandleFunc(far, parcels)\n}\n" + handlerFuncs

	if got := onlySite(t, source).Literals["path"]; got != tooLongToPrint {
		t.Fatalf("got %d bytes, want the stand-in text", len(got))
	}
}

func TestRoutesTakesNoValueFromAPathThatIsNotUTF8(t *testing.T) {
	source := "package main\n\nimport \"net/http\"\n\nfunc main() {\n\thttp.HandleFunc(\"/caf\\xe9\", parcels)\n}\n" + handlerFuncs

	if value, known := onlySite(t, source).Literals["path"]; known {
		t.Fatalf("JSON would change these bytes, got %q", value)
	}
}

func TestRoutesRootsAHandlerAtTheFunctionItNames(t *testing.T) {
	cases := map[string]struct {
		argument    string
		wantID      string
		wantDisplay string
	}{
		"a function":                   {"parcels", "example.com/depot.parcels", "parcels"},
		"a method value":               {"d.list", "(*example.com/depot.desk).list", "d.list"},
		"a conversion":                 {"http.HandlerFunc(parcels)", "example.com/depot.parcels", "parcels"},
		"a conversion of a method":     {"http.HandlerFunc(d.list)", "(*example.com/depot.desk).list", "d.list"},
		"a wrapper":                    {"logged(parcels)", "example.com/depot.parcels", "parcels"},
		"a wrapper around a wrapper":   {"logged(logged(d.list))", "(*example.com/depot.desk).list", "d.list"},
		"a wrapper after other values": {"timed(3, parcels)", "example.com/depot.parcels", "parcels"},
		"a parenthesised name":         {"(parcels)", "example.com/depot.parcels", "parcels"},
		"a factory":                    {"d.build()", "(*example.com/depot.desk).build", "d.build"},
		"a factory that takes values":  {"makeHandler(3)", "example.com/depot.makeHandler", "makeHandler"},
		"a factory inside a wrapper":   {"logged(d.build())", "(*example.com/depot.desk).build", "d.build"},
		"a shape that returns an error": {
			"adapted(d.failing)", "(*example.com/depot.desk).failing", "d.failing",
		},
	}

	for name, test := range cases {
		source := `package main

import "net/http"

type desk struct{}

func (d *desk) list(w http.ResponseWriter, r *http.Request) {}

func (d *desk) failing(w http.ResponseWriter, r *http.Request) error { return nil }

func (d *desk) build() http.HandlerFunc { return d.list }

func makeHandler(size int) http.HandlerFunc { return parcels }

func logged(next http.HandlerFunc) http.HandlerFunc { return next }

func timed(limit int, next http.HandlerFunc) http.HandlerFunc { return next }

func adapted(next func(http.ResponseWriter, *http.Request) error) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) { _ = next(w, r) }
}

func parcels(w http.ResponseWriter, r *http.Request) {}

func main() {
	d := &desk{}
	_ = d
	http.HandleFunc("/x", ` + test.argument + `)
}
`
		site := onlySite(t, source)

		if site.HandlerID == nil || *site.HandlerID != test.wantID {
			t.Errorf("%s: id %v, want %s", name, site.HandlerID, test.wantID)
		}
		if site.Args["handler"] != test.wantDisplay {
			t.Errorf("%s: display %q, want %s", name, site.Args["handler"], test.wantDisplay)
		}
	}
}

func TestRoutesRootsAValuePassedToHandle(t *testing.T) {
	cases := map[string]struct {
		argument string
		wantID   string
	}{
		"a conversion to a type that serves": {"served(plain)", "example.com/depot.plain"},
		"a factory given a plain function":   {"sortedBy(compare)", "example.com/depot.sortedBy"},
	}

	for name, test := range cases {
		source := `package main

import "net/http"

type served func(code int)

func (s served) ServeHTTP(w http.ResponseWriter, r *http.Request) { s(200) }

func plain(code int) {}

func compare(a, b string) int { return 0 }

func sortedBy(less func(a, b string) int) http.Handler { return nil }

func main() {
	http.Handle("/x", ` + test.argument + `)
}
`
		site := onlySite(t, source)

		if site.HandlerID == nil || *site.HandlerID != test.wantID {
			t.Errorf("%s: id %v, want %s", name, site.HandlerID, test.wantID)
		}
	}
}

func TestRoutesGivesNoRootWhenTheCompilerCannotNameTheFunction(t *testing.T) {
	cases := map[string]string{
		"a function literal":                `func(w http.ResponseWriter, r *http.Request) {}`,
		"a local variable":                  `local`,
		"a field":                           `d.field`,
		"a package variable":                `shared`,
		"a method of an interface":          `api.list`,
		"a function outside the module":     `http.NotFound`,
		"a factory outside the module":      `http.NotFoundHandler().ServeHTTP`,
		"a wrapper around a local variable": `logged(local)`,
		"a wrapper around a field":          `logged(d.field)`,
		"a wrapper around a literal":        `logged(func(w http.ResponseWriter, r *http.Request) {})`,
		"a wrapper that takes two handlers": `either(parcels, depots)`,
		"a value taken from a table":        `table["x"]`,
	}

	for name, argument := range cases {
		source := `package main

import "net/http"

type lister interface {
	list(w http.ResponseWriter, r *http.Request)
}

type desk struct{ field http.HandlerFunc }

var shared http.HandlerFunc

var table = map[string]http.HandlerFunc{}

func logged(next http.HandlerFunc) http.HandlerFunc { return next }

func either(a, b http.HandlerFunc) http.HandlerFunc { return a }

func main() {
	var api lister
	_ = api
	d := desk{}
	_ = d
	local := http.HandlerFunc(parcels)
	_ = local
	http.HandleFunc("/x", ` + argument + `)
}
` + handlerFuncs
		site := onlySite(t, source)

		if site.HandlerID != nil || site.HandlerFile != nil || site.HandlerLine != nil {
			t.Errorf("%s: want no root, got %s %v %v", name, *site.HandlerID, site.HandlerFile, site.HandlerLine)
		}
	}
}

// Both of these sit inside the repo, so only the module tells them apart, and a root here would
// send the renderer's comment into code the repo does not own.
func TestRoutesGivesNoRootToAFunctionOfAnotherModuleInsideTheRepo(t *testing.T) {
	const serve = "package dep\n\nimport \"net/http\"\n\nfunc Serve(w http.ResponseWriter, r *http.Request) {}\n"
	const main = "package main\n\nimport (\n\t\"net/http\"\n\n\t\"example.com/dep\"\n)\n\nfunc main() {\n\thttp.HandleFunc(\"/x\", dep.Serve)\n}\n"
	layouts := map[string]map[string]string{
		"a nested module": {
			"go.mod":     "module example.com/depot\n\ngo 1.22\n\nrequire example.com/dep v0.0.0\n\nreplace example.com/dep => ./dep\n",
			"main.go":    main,
			"dep/go.mod": "module example.com/dep\n\ngo 1.22\n",
			"dep/dep.go": serve,
		},
		"a vendored module": {
			"go.mod":                      "module example.com/depot\n\ngo 1.22\n\nrequire example.com/dep v1.0.0\n",
			"main.go":                     main,
			"vendor/modules.txt":          "# example.com/dep v1.0.0\n## explicit; go 1.22\nexample.com/dep\n",
			"vendor/example.com/dep/d.go": serve,
		},
	}

	for name, files := range layouts {
		site := routesOf(t, writeTree(t, files), ".", noPatterns).Sites[0]

		if site.HandlerID != nil || site.HandlerFile != nil {
			t.Errorf("%s: got root %s in %s", name, *site.HandlerID, *site.HandlerFile)
		}
	}
}

func TestRoutesKeepsTheTextOfAHandlerWithNoRoot(t *testing.T) {
	source := `package main

import "net/http"

type desk struct{ field http.HandlerFunc }

func main() {
	d := desk{}
	http.HandleFunc("/x", d.field)
}
`
	if got := onlySite(t, source).Args["handler"]; got != "d.field" {
		t.Fatalf("got %q", got)
	}
}

func TestRoutesRootsAVariableThatSharesAFunctionsNameAtNothing(t *testing.T) {
	root := writeModule(t, "example.com/depot", map[string]string{
		"main.go": `package main

import (
	"net/http"

	"example.com/depot/audit"
)

func main() {
	list := http.HandlerFunc(serve)
	http.HandleFunc("/x", list)
	audit.List()
}

func serve(w http.ResponseWriter, r *http.Request) {}
`,
		"audit/audit.go": "package audit\n\nfunc List() {}\n\nfunc list() {}\n",
	})

	if got := routesOf(t, root, ".", noPatterns).Sites[0].HandlerID; got != nil {
		t.Fatalf("a local variable has no root, got %s", *got)
	}
}

func TestRoutesRootsAMethodNotAnotherTypesMethodOfTheSameName(t *testing.T) {
	source := `package main

import "net/http"

type stock struct{}

func (stock) list(w http.ResponseWriter, r *http.Request) {}

type shelf struct{}

func (shelf) list(w http.ResponseWriter, r *http.Request) {}

func main() {
	http.HandleFunc("/x", shelf{}.list)
}
`
	if got := *onlySite(t, source).HandlerID; got != "(example.com/depot.shelf).list" {
		t.Fatalf("got %s", got)
	}
}

func TestRoutesRootsAGenericMethodAtItsOrigin(t *testing.T) {
	source := `package main

import "net/http"

type pair[K comparable, V any] struct{}

func (p *pair[K, V]) serve(w http.ResponseWriter, r *http.Request) {}

func main() {
	p := &pair[string, int]{}
	http.HandleFunc("/x", p.serve)
}
`
	if got := *onlySite(t, source).HandlerID; got != "(*example.com/depot.pair[K, V]).serve" {
		t.Fatalf("got %s", got)
	}
}

func TestRoutesSaysWhereARootIsDeclared(t *testing.T) {
	root := writeModule(t, "example.com/depot", map[string]string{
		"main.go": `package main

import (
	"net/http"

	"example.com/depot/api"
)

func main() {
	http.HandleFunc("/x", api.Show)
}
`,
		"api/show.go": "package api\n\nimport \"net/http\"\n\n// Show answers.\nfunc Show(w http.ResponseWriter, r *http.Request) {}\n",
	})

	site := routesOf(t, root, ".", noPatterns).Sites[0]

	if site.File != "main.go" || site.Line != 10 {
		t.Errorf("site at %s:%d", site.File, site.Line)
	}
	if site.HandlerFile == nil || *site.HandlerFile != "api/show.go" || site.HandlerLine == nil || *site.HandlerLine != 6 {
		t.Errorf("handler at %v:%v", site.HandlerFile, site.HandlerLine)
	}
}

func TestRoutesReadsAFileAsWrittenAndNotWhereALineDirectivePoints(t *testing.T) {
	for _, target := range []string{"../../outside/elsewhere.go", "inside/elsewhere.go"} {
		source := "package main\n\nimport \"net/http\"\n\nfunc main() {\n//line " + target + ":100\n\thttp.HandleFunc(\"/x\", parcels)\n}\n\n//line " + target + ":7\nfunc parcels(w http.ResponseWriter, r *http.Request) {}\n"

		site := onlySite(t, source)

		if site.File != "main.go" || site.Line != 7 {
			t.Errorf("%s: site at %s:%d", target, site.File, site.Line)
		}
		if *site.HandlerFile != "main.go" || *site.HandlerLine != 11 {
			t.Errorf("%s: handler at %s:%d", target, *site.HandlerFile, *site.HandlerLine)
		}
	}
}

func requireACCompiler(t *testing.T) {
	t.Helper()
	if _, err := exec.LookPath("cc"); err != nil {
		t.Skip("cgo needs a C compiler")
	}
}

// cgo rewrites a file and adds one of its own, so the compiler reads paths outside the repo. The
// rewritten file points back at the source, and the added one holds nothing a repo wrote.
func TestRoutesReadsAFileThatUsesCgoAtItsOwnPlace(t *testing.T) {
	requireACCompiler(t)
	root := writeModule(t, "example.com/depot", map[string]string{
		"main.go": "// A comment, so the line numbers cgo keeps can be told from its own.\npackage main\n\n// int seven(void) { return 7; }\nimport \"C\"\n\nimport \"net/http\"\n\nfunc main() {\n\t_ = C.seven()\n\thttp.HandleFunc(\"/from-cgo\", served)\n}\n",
		"c.go":    "package main\n\n// int eight(void) { return 8; }\nimport \"C\"\n\nimport \"net/http\"\n\nfunc served(w http.ResponseWriter, r *http.Request) { _ = C.eight() }\n",
	})

	output := routesOf(t, root, ".", noPatterns)

	if len(output.Sites) != 1 {
		t.Fatalf("got %+v", output.Sites)
	}
	site := output.Sites[0]
	if site.File != "main.go" || site.Line != 11 {
		t.Errorf("site at %s:%d", site.File, site.Line)
	}
	if site.HandlerID == nil || *site.HandlerFile != "c.go" || *site.HandlerLine != 8 {
		t.Errorf("handler %v at %v:%v", site.HandlerID, site.HandlerFile, site.HandlerLine)
	}
	if want := []string{"c.go", "main.go"}; !reflect.DeepEqual(output.BuildFiles, want) {
		t.Errorf("files %v, want %v", output.BuildFiles, want)
	}
}

func TestRoutesTreatsAServeMuxHandlerAsAMountAndNotARoute(t *testing.T) {
	cases := map[string]string{
		"a mux":                     `inner`,
		"the default mux":           `http.DefaultServeMux`,
		"a mux behind a wrapper":    `logged(inner)`,
		"a mux behind two wrappers": `logged(logged(inner))`,
	}

	for name, handler := range cases {
		source := `package main

import "net/http"

func logged(next http.Handler) http.Handler { return next }

func main() {
	inner := http.NewServeMux()
	inner.HandleFunc("/stock/items", parcels)
	outer := http.NewServeMux()
	outer.Handle("/stock/", ` + handler + `)
	_ = outer
}
` + handlerFuncs
		if got, want := sitePaths(sitesOfSource(t, source)), []string{"/stock/items"}; !reflect.DeepEqual(got, want) {
			t.Errorf("%s: got %v, want %v", name, got, want)
		}
	}
}

func TestRoutesListsEveryCallToStripPrefix(t *testing.T) {
	cases := map[string]string{
		"written at the site": `package main

import "net/http"

func main() {
	http.Handle("/files/", http.StripPrefix("/files", http.NotFoundHandler()))
}
`,
		"behind an import name": `package main

import web "net/http"

func main() {
	web.Handle("/files/", web.StripPrefix("/files", web.NotFoundHandler()))
}
`,
		"held in a variable": `package main

import "net/http"

func main() {
	stripped := http.StripPrefix("/files", http.NotFoundHandler())
	http.Handle("/files/", stripped)
}
`,
		"inside a helper": `package main

import "net/http"

func stripped() http.Handler {
	return http.StripPrefix("/files", http.NotFoundHandler())
}

func main() {
	http.Handle("/files/", stripped())
}
`,
	}

	for name, source := range cases {
		root := writeModule(t, "example.com/depot", map[string]string{"main.go": source})

		stripped := routesOf(t, root, ".", noPatterns).StrippedPrefixes

		if len(stripped) != 1 || stripped[0].File != "main.go" {
			t.Errorf("%s: got %+v", name, stripped)
		}
	}
}

func TestRoutesDoesNotTakeAnotherPackagesStripPrefixForTheOne(t *testing.T) {
	source := `package main

import "net/http"

func StripPrefix(path string, next http.Handler) http.Handler { return next }

func main() {
	http.Handle("/files/", StripPrefix("/files", http.NotFoundHandler()))
}
`
	root := writeModule(t, "example.com/depot", map[string]string{"main.go": source})

	if got := routesOf(t, root, ".", noPatterns).StrippedPrefixes; len(got) != 0 {
		t.Fatalf("got %+v", got)
	}
}

const wrapperModule = `package main

import "net/http"

type gate struct{ mux *http.ServeMux }

func (g gate) post(path string, h http.HandlerFunc) {
	g.mux.HandleFunc("POST "+path, h)
	g.mux.HandleFunc("/known-inside-the-wrapper", parcels)
}

func (g gate) fixed() {
	g.mux.HandleFunc("/always", parcels)
}

func main() {
	g := gate{mux: http.NewServeMux()}
	g.post("/orders", parcels)
	g.fixed()
}
` + handlerFuncs

func TestRoutesPassesOverASiteInsideAFunctionARoutePatternMatches(t *testing.T) {
	root := writeModule(t, "example.com/depot", map[string]string{"main.go": wrapperModule})
	patterns := `[{"kind":"route","call":"^g\\.post$","args":{"path":0,"handler":1}}]`

	got := sitePaths(routesOf(t, root, ".", patterns).Sites)

	if want := []string{"/always", "/known-inside-the-wrapper", "/orders"}; !reflect.DeepEqual(got, want) {
		t.Fatalf("got %v, want %v", got, want)
	}
}

func TestRoutesKeepsTheSiteInsideAWrapperWhenNoPatternMatchesIt(t *testing.T) {
	root := writeModule(t, "example.com/depot", map[string]string{"main.go": wrapperModule})

	sites := routesOf(t, root, ".", noPatterns).Sites

	unread := []string{}
	for _, site := range sites {
		if _, known := site.Literals["path"]; !known {
			unread = append(unread, site.Args["path"])
		}
	}
	if want := []string{`"POST " + path`}; !reflect.DeepEqual(unread, want) {
		t.Fatalf("narrowing the pattern must not lose the site, got %v from %+v", unread, sites)
	}
}

func TestRoutesListsAWrapperThatAddsToThePathItIsGiven(t *testing.T) {
	cases := map[string]string{
		"a slash in front":          `"/v1"+path`,
		"a method and a slash":      `"POST /v1"+path`,
		"a const holding the slash": `versionRoot+path`,
		"a slash behind":            `path+"/"`,
		"a call that joins":         `strings.Join([]string{"/v1", path}, "")`,
	}

	for name, argument := range cases {
		source := `package main

import (
	"net/http"
	"strings"
)

const versionRoot = "/v1"

var _ = strings.Join

type gate struct{ mux *http.ServeMux }

func (g gate) post(path string, h http.HandlerFunc) {
	g.mux.HandleFunc(` + argument + `, h)
}

func main() {
	gate{mux: http.NewServeMux()}.post("/items", parcels)
}
` + handlerFuncs
		root := writeModule(t, "example.com/depot", map[string]string{"main.go": source})
		patterns := `[{"kind":"route","call":"\\.post$","args":{"path":0,"handler":1}}]`

		got := routesOf(t, root, ".", patterns).WrapperPrefixes

		if want := []placeOutput{{File: "main.go", Line: 15}}; !reflect.DeepEqual(got, want) {
			t.Errorf("%s: got %+v, want %+v", name, got, want)
		}
	}
}

func TestRoutesDoesNotCallAMethodOrAPlainPathAPrefix(t *testing.T) {
	for _, argument := range []string{`path`, `"POST "+path`, `"GET "+path`} {
		source := `package main

import "net/http"

type gate struct{ mux *http.ServeMux }

func (g gate) post(path string, h http.HandlerFunc) {
	g.mux.HandleFunc(` + argument + `, h)
}

func main() {
	gate{mux: http.NewServeMux()}.post("/items", parcels)
}
` + handlerFuncs
		root := writeModule(t, "example.com/depot", map[string]string{"main.go": source})
		patterns := `[{"kind":"route","call":"\\.post$","args":{"path":0,"handler":1}}]`

		if got := routesOf(t, root, ".", patterns).WrapperPrefixes; len(got) != 0 {
			t.Errorf("%s: got %+v", argument, got)
		}
	}
}

func TestRoutesDoesNotCallAPathOutsideAWrapperAPrefix(t *testing.T) {
	source := `package main

import "net/http"

func main() {
	suffix := "x"
	http.HandleFunc("/v1/"+suffix, parcels)
}
` + handlerFuncs
	root := writeModule(t, "example.com/depot", map[string]string{"main.go": source})

	output := routesOf(t, root, ".", noPatterns)

	if len(output.WrapperPrefixes) != 0 || len(output.Sites) != 1 {
		t.Fatalf("this site stops as a path the compiler does not know, got %+v", output)
	}
}

func TestRoutesFindsAWrapperWhateverItsReceiverIsCalledAtItsCalls(t *testing.T) {
	source := `package main

import "net/http"

type gate struct{ mux *http.ServeMux }

func (g gate) post(path string, h http.HandlerFunc) {
	g.mux.HandleFunc("POST "+path, h)
}

func main() {
	a, b := gate{mux: http.NewServeMux()}, gate{mux: http.NewServeMux()}
	a.post("/one", parcels)
	b.post("/two", parcels)
}
` + handlerFuncs
	root := writeModule(t, "example.com/depot", map[string]string{"main.go": source})
	patterns := `[{"kind":"route","call":"^a\\.post$","args":{"path":0,"handler":1}}]`

	got := sitePaths(routesOf(t, root, ".", patterns).Sites)

	if want := []string{"/one", "/two"}; !reflect.DeepEqual(got, want) {
		t.Fatalf("a pattern on one receiver must not lose the call on the other, got %v", got)
	}
}

func TestRoutesUsesThePositionsAPatternGives(t *testing.T) {
	source := `package main

import "net/http"

func register(h http.HandlerFunc, path string) { http.DefaultServeMux.HandleFunc(path, h) }

func main() {
	register(parcels, "/backwards")
}
` + handlerFuncs
	root := writeModule(t, "example.com/depot", map[string]string{"main.go": source})
	patterns := `[{"kind":"route","call":"^register$","args":{"path":1,"handler":0}}]`

	sites := routesOf(t, root, ".", patterns).Sites

	if got, want := sitePaths(sites), []string{"/backwards"}; !reflect.DeepEqual(got, want) {
		t.Fatalf("got %v, want %v", got, want)
	}
	if sites[0].Kind != "route" || sites[0].Call != "register" || *sites[0].HandlerID != "example.com/depot.parcels" {
		t.Fatalf("got %+v", sites[0])
	}
}

func TestRoutesLeavesOutAHandlerPositionPastTheArguments(t *testing.T) {
	root := writeModule(t, "example.com/depot", map[string]string{"main.go": "package main\n\nimport \"net/http\"\n\nfunc main() {\n\thttp.HandleFunc(\"/x\", parcels)\n}\n" + handlerFuncs})
	patterns := `[{"kind":"route","call":"^http\\.HandleFunc$","args":{"path":0,"handler":9}}]`

	site := routesOf(t, root, ".", patterns).Sites[0]

	if _, present := site.Args["handler"]; present || site.HandlerID != nil {
		t.Fatalf("a position past the arguments must read as missing, got %+v", site)
	}
}

func TestRoutesListsTheCallsInsideAWrapperOfTheSameKind(t *testing.T) {
	source := `package main

import (
	"net/http"
	"os"
)

func setting(name string) string { return os.Getenv(name) }

func main() {
	_ = setting("DEPOT_PORT")
	_ = os.Getenv("DEPOT_MODE")
	http.HandleFunc("/x", parcels)
}
` + handlerFuncs
	root := writeModule(t, "example.com/depot", map[string]string{"main.go": source})
	patterns := `[{"kind":"env","call":"^os\\.Getenv$","args":{"name":0}},{"kind":"env","call":"^setting$","args":{"name":0}}]`

	got := routesOf(t, root, ".", patterns).WrapperCalls

	if want := []wrapperCallOutput{{Kind: "env", File: "main.go", Line: 8}}; !reflect.DeepEqual(got, want) {
		t.Fatalf("got %+v, want %+v", got, want)
	}
}

func twoServices() map[string]string {
	return map[string]string{
		"cmd/tracker/main.go": `package main

import (
	"net/http"

	"example.com/depot/internal/routes"
)

func main() {
	http.HandleFunc("/track", routes.Track)
}
`,
		"cmd/console/main.go": `package main

import (
	"net/http"

	"example.com/depot/internal/routes"
)

func main() {
	http.HandleFunc("/wipe", routes.Track)
}
`,
		"internal/routes/routes.go": "package routes\n\nimport \"net/http\"\n\nfunc Track(w http.ResponseWriter, r *http.Request) {}\n",
		"internal/unused/unused.go": "package unused\n\nimport \"net/http\"\n\nfunc init() {\n\thttp.HandleFunc(\"/never\", nil)\n}\n",
	}
}

func TestRoutesReadsOnlyThePackagesTheServiceImports(t *testing.T) {
	root := writeModule(t, "example.com/depot", twoServices())

	output := routesOf(t, root, "./cmd/tracker", noPatterns)

	if got, want := sitePaths(output.Sites), []string{"/track"}; !reflect.DeepEqual(got, want) {
		t.Errorf("sites %v, want %v", got, want)
	}
	if want := []string{"cmd/tracker", "internal/routes"}; !reflect.DeepEqual(output.PackageFolders, want) {
		t.Errorf("folders %v, want %v", output.PackageFolders, want)
	}
	if want := []string{"cmd/tracker/main.go", "internal/routes/routes.go"}; !reflect.DeepEqual(output.BuildFiles, want) {
		t.Errorf("files %v, want %v", output.BuildFiles, want)
	}
}

func TestRoutesFollowsAnImportIntoAFolderTheServiceDoesNotName(t *testing.T) {
	root := writeModule(t, "example.com/depot", map[string]string{
		"cmd/api/main.go":  "package main\n\nimport (\n\t\"net/http\"\n\n\t_ \"example.com/depot/plugin\"\n)\n\nfunc main() {\n\thttp.HandleFunc(\"/api\", http.NotFound)\n}\n",
		"plugin/plugin.go": "package plugin\n\nimport \"net/http\"\n\nfunc init() {\n\thttp.HandleFunc(\"/added-by-an-import\", http.NotFound)\n}\n",
	})

	got := sitePaths(routesOf(t, root, "./cmd/api", noPatterns).Sites)

	if want := []string{"/added-by-an-import", "/api"}; !reflect.DeepEqual(got, want) {
		t.Fatalf("got %v, want %v", got, want)
	}
}

func TestRoutesReadsAFolderTheGoToolPassesOverWhenTheServiceImportsIt(t *testing.T) {
	root := writeModule(t, "example.com/depot", map[string]string{
		"main.go":        "package main\n\nimport _ \"example.com/depot/_extra\"\n\nfunc main() {}\n",
		"_extra/more.go": "package extra\n\nimport \"net/http\"\n\nfunc init() {\n\thttp.HandleFunc(\"/behind-an-underscore\", http.NotFound)\n}\n",
	})

	got := sitePaths(routesOf(t, root, ".", noPatterns).Sites)

	if want := []string{"/behind-an-underscore"}; !reflect.DeepEqual(got, want) {
		t.Fatalf("got %v, want %v", got, want)
	}
}

func TestRoutesLeavesOutAnotherModuleWhoseImportPathStartsTheSame(t *testing.T) {
	root := writeTree(t, map[string]string{
		"go.mod":        "module example.com/depot\n\ngo 1.22\n\nrequire example.com/depot/sub v0.0.0\n\nreplace example.com/depot/sub => ./sub\n",
		"main.go":       "package main\n\nimport _ \"example.com/depot/sub\"\n\nfunc main() {}\n",
		"sub/go.mod":    "module example.com/depot/sub\n\ngo 1.22\n",
		"sub/routes.go": "package sub\n\nimport \"net/http\"\n\nfunc init() {\n\thttp.HandleFunc(\"/another-module\", http.NotFound)\n}\n",
	})

	output := routesOf(t, root, ".", noPatterns)

	if len(output.Sites) != 0 {
		t.Errorf("got %+v", output.Sites)
	}
	if want := []string{"."}; !reflect.DeepEqual(output.PackageFolders, want) {
		t.Errorf("folders %v, want %v", output.PackageFolders, want)
	}
}

func TestRoutesLeavesAFileForAnotherBuildOutOfTheBuildFiles(t *testing.T) {
	root := writeModule(t, "example.com/depot", map[string]string{
		"main.go":        "package main\n\nfunc main() {}\n",
		"extra_plan9.go": "package main\n\nimport \"net/http\"\n\nfunc init() {\n\thttp.HandleFunc(\"/only-there\", http.NotFound)\n}\n",
		"helper_test.go": "package main\n\nimport \"net/http\"\n\nfunc init() {\n\thttp.HandleFunc(\"/only-in-tests\", http.NotFound)\n}\n",
	})

	output := routesOf(t, root, ".", noPatterns)

	if len(output.Sites) != 0 {
		t.Errorf("got %+v", output.Sites)
	}
	if want := []string{"main.go"}; !reflect.DeepEqual(output.BuildFiles, want) {
		t.Errorf("files %v, want %v", output.BuildFiles, want)
	}
}

func TestRoutesSortsSitesAndPrintsEmptyListsAsLists(t *testing.T) {
	root := writeModule(t, "example.com/depot", map[string]string{"main.go": "package main\n\nfunc main() {}\n"})

	exitCode, stdout, stderr := runCommand(t, "routes", "--patterns", writePatterns(t, noPatterns), "--main", ".", root)

	if exitCode != 0 || stderr != "" {
		t.Fatalf("exit %d, stderr %q", exitCode, stderr)
	}
	want := `{"package_folders":["."],"build_files":["main.go"],"sites":[],"stripped_prefixes":[],"wrapper_prefixes":[],"wrapper_calls":[]}` + "\n"
	if stdout != want {
		t.Fatalf("got %s, want %s", stdout, want)
	}
}

func TestRoutesFailsLoudly(t *testing.T) {
	services := twoServices()
	cases := map[string]struct {
		files       map[string]string
		mainPackage string
		mustMention string
	}{
		"a package that does not exist": {services, "./cmd/nothing", "main_package"},
		"several packages":              {services, "./...", "main_package"},
		"a file and not a folder":       {services, "./cmd/tracker/main.go", "main_package"},
		"a type error in the service": {
			map[string]string{"main.go": "package main\n\nfunc main() { missingName() }\n"}, ".", "missingName",
		},
		"a syntax error in the service": {
			map[string]string{"main.go": "package main\n\nfunc main( {\n"}, ".", "main.go",
		},
		"an import nothing provides": {
			map[string]string{"main.go": "package main\n\nimport _ \"example.invalid/nothing\"\n\nfunc main() {}\n"}, ".", "example.invalid/nothing",
		},
	}

	for name, test := range cases {
		root := writeModule(t, "example.com/depot", test.files)

		exitCode, stdout, stderr := runCommand(t, "routes", "--patterns", writePatterns(t, noPatterns), "--main", test.mainPackage, root)

		if exitCode != 1 || stdout != "" {
			t.Errorf("%s: exit %d, stdout %q", name, exitCode, stdout)
		}
		if !strings.Contains(stderr, test.mustMention) {
			t.Errorf("%s: stderr must mention %q, got %q", name, test.mustMention, stderr)
		}
	}
}

func TestRoutesIsNotStoppedByABrokenPackageOutsideTheService(t *testing.T) {
	files := twoServices()
	files["cmd/console/oops.go"] = "package main\n\nvar broken int = \"text\"\n"
	root := writeModule(t, "example.com/depot", files)

	got := sitePaths(routesOf(t, root, "./cmd/tracker", noPatterns).Sites)

	if want := []string{"/track"}; !reflect.DeepEqual(got, want) {
		t.Fatalf("got %v, want %v", got, want)
	}
}

func TestRoutesReadsNoGoFileBeforeTheBadPatternStopsIt(t *testing.T) {
	root := writeTree(t, map[string]string{"not-a-module.txt": "x"})

	exitCode, _, stderr := runCommand(t, "routes", "--patterns", writePatterns(t, `[{"kind":"route","call":"(","args":{}}]`), "--main", ".", root)

	if exitCode != 1 || !strings.Contains(stderr, "does not compile") {
		t.Fatalf("exit %d, stderr %q", exitCode, stderr)
	}
}

func TestRoutesRejectsBadArguments(t *testing.T) {
	root := writeModule(t, "example.com/depot", map[string]string{"main.go": "package main\n\nfunc main() {}\n"})
	patterns := writePatterns(t, noPatterns)
	cases := map[string][]string{
		"no patterns":      {"routes", "--main", ".", root},
		"no main package":  {"routes", "--patterns", patterns, root},
		"no directory":     {"routes", "--patterns", patterns, "--main", "."},
		"two directories":  {"routes", "--patterns", patterns, "--main", ".", root, root},
		"a missing go.mod": {"routes", "--patterns", patterns, "--main", ".", t.TempDir()},
	}

	for name, args := range cases {
		exitCode, stdout, stderr := runCommand(t, args...)

		if exitCode != 1 || stdout != "" || strings.TrimSpace(stderr) == "" {
			t.Errorf("%s: exit %d, stdout %q, stderr %q", name, exitCode, stdout, stderr)
		}
	}
}

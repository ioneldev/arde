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
	Callee      string            `json:"callee"`
	File        string            `json:"file"`
	Line        int               `json:"line"`
	Args        map[string]string `json:"args"`
	Literals    map[string]string `json:"literals"`
	HandlerID   *string           `json:"handler_id"`
	HandlerFile *string           `json:"handler_file"`
	HandlerLine *int              `json:"handler_line"`
	Roots       []string          `json:"roots"`
	// HandlerIdentity is what the handler holds, whatever its spelling.
	HandlerIdentity         string `json:"handler_identity"`
	ConstantsHash           string `json:"constants_hash"`
	HandlerIsTheAnchorAlone bool   `json:"handler_is_the_anchor_alone"`
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

type unreadSiteOutput struct {
	File      string       `json:"file"`
	Line      int          `json:"line"`
	Problem   string       `json:"problem"`
	WrittenAt *placeOutput `json:"written_at"`
}

type routesOutput struct {
	PackageFolders   []string            `json:"package_folders"`
	BuildFiles       []string            `json:"build_files"`
	Sites            []siteOutput        `json:"sites"`
	StrippedPrefixes []placeOutput       `json:"stripped_prefixes"`
	UnreadSites      []unreadSiteOutput  `json:"unread_sites"`
	WrapperCalls     []wrapperCallOutput `json:"wrapper_calls"`
}

func writePatterns(t *testing.T, patterns string) string {
	t.Helper()
	path := filepath.Join(t.TempDir(), "patterns.json")
	assertNoError(t, os.WriteFile(path, []byte(patterns), 0o644))
	return path
}

type routeQuery struct {
	root        string
	mainPackage string
	patterns    string
}

func routesOf(t *testing.T, query routeQuery) routesOutput {
	t.Helper()
	exitCode, stdout, stderr := runCommand(t, "routes", "--patterns", writePatterns(t, query.patterns), "--main", query.mainPackage, query.root)
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
	return routesOf(t, routeQuery{root: root, mainPackage: ".", patterns: noPatterns}).Sites
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

	if got := routesOf(t, routeQuery{root: root, mainPackage: ".", patterns: noPatterns}).Sites; len(got) != 0 {
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
	}

	for name, test := range cases {
		source := `package main

import (
	"net/http"
	"time"
)

type desk struct{}

func (d *desk) list(w http.ResponseWriter, r *http.Request) {}

func (d *desk) failing(w http.ResponseWriter, r *http.Request) error { return nil }

func (d *desk) build() http.HandlerFunc { return d.list }

func makeHandler(size int) http.HandlerFunc { return parcels }

func logged(next http.HandlerFunc) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) { next.ServeHTTP(w, r) }
}

func timed(limit int, next http.HandlerFunc) http.HandlerFunc {
	return http.TimeoutHandler(next, time.Duration(limit), "late").ServeHTTP
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
		"a value taken from a table":        `table["x"]`,
		"a call the compiler cannot name":   `wrap(parcels)`,
		"a factory given a literal":         `makeHandler(func() int { return 3 })`,
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

func logged(next http.HandlerFunc) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) { next.ServeHTTP(w, r) }
}

func makeHandler(size func() int) http.HandlerFunc { return parcels }

func main() {
	var api lister
	_ = api
	d := desk{}
	_ = d
	local := http.HandlerFunc(parcels)
	_ = local
	wrap := logged
	// Written again, so a call through wrap could reach anything.
	wrap = logged
	_ = wrap
	http.HandleFunc("/x", ` + argument + `)
}
` + handlerFuncs
		site := onlySite(t, source)

		if site.HandlerID != nil || site.HandlerFile != nil || site.HandlerLine != nil || len(site.Roots) != 0 {
			t.Errorf("%s: want no root, got %v %v %v %v", name, site.HandlerID, site.HandlerFile, site.HandlerLine, site.Roots)
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
		site := routesOf(t, routeQuery{root: writeTree(t, files), mainPackage: ".", patterns: noPatterns}).Sites[0]

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

	if got := routesOf(t, routeQuery{root: root, mainPackage: ".", patterns: noPatterns}).Sites[0].HandlerID; got != nil {
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

	site := routesOf(t, routeQuery{root: root, mainPackage: ".", patterns: noPatterns}).Sites[0]

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

	output := routesOf(t, routeQuery{root: root, mainPackage: ".", patterns: noPatterns})

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
		"a mux behind middleware of another module":                 `http.TimeoutHandler(inner, 1, "late")`,
		"a mux behind middleware given a function written in place": `guarded(inner, func(r *http.Request) bool { return true })`,
		"a mux a factory returns":                                   `mounted()`,
		"a mux's ServeHTTP":                                         `http.HandlerFunc(inner.ServeHTTP)`,
		"a mux's ServeHTTP behind middleware":                       `http.HandlerFunc(called(inner.ServeHTTP))`,
		"a mux behind middleware that keeps it in a struct":         `secured(inner)`,
		"a mux behind middleware that takes its ServeHTTP":          `passed(inner)`,
	}

	for name, handler := range cases {
		source := `package main

import "net/http"

func logged(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { next.ServeHTTP(w, r) })
}

func mounted() *http.ServeMux { return http.NewServeMux() }

func called(next http.HandlerFunc) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) { next(w, r) }
}

type headerMiddleware struct{ next http.Handler }

func (m *headerMiddleware) ServeHTTP(w http.ResponseWriter, r *http.Request) { m.next.ServeHTTP(w, r) }

func secured(next http.Handler) http.Handler { return &headerMiddleware{next: next} }

func passed(next http.Handler) http.Handler { return http.HandlerFunc(next.ServeHTTP) }

func guarded(next http.Handler, allow func(*http.Request) bool) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if allow(r) {
			next.ServeHTTP(w, r)
		}
	})
}

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

func TestRoutesReadsAMuxGivenToAFunctionThatDoesNotServeItAsARouteOfThatFunction(t *testing.T) {
	source := `package main

import "net/http"

func listing(mux *http.ServeMux) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {}
}

func main() {
	inner := http.NewServeMux()
	inner.HandleFunc("/stock/items", parcels)
	inner.Handle("/stock/", listing(inner))
}
` + handlerFuncs
	sites := sitesOfSource(t, source)

	if got, want := sitePaths(sites), []string{"/stock/", "/stock/items"}; !reflect.DeepEqual(got, want) {
		t.Fatalf("got %v, want %v", got, want)
	}
	for _, site := range sites {
		if id := site.HandlerID; site.Literals["path"] == "/stock/" && (id == nil || *id != "example.com/depot.listing") {
			t.Errorf("got %v", id)
		}
	}
}

// depotID writes a function of the test module by its id. A method's id is written whole.
func depotID(name string) string {
	if strings.HasPrefix(name, "(") {
		return name
	}
	return "example.com/depot." + name
}

// The anchor is the one function the route's docs belong on, and the roots are every function of
// the module the handler names, which its fingerprint covers.
func TestRoutesAnchorsAtTheFactoryAndRootsAtEveryFunctionTheHandlerNames(t *testing.T) {
	cases := map[string]struct {
		argument   string
		wantAnchor string
		wantShown  string
		wantRoots  []string
	}{
		"a factory given a dependency": {
			"handleList(d, renderError)", "handleList", "handleList", []string{"handleList", "renderError"},
		},
		"a function that falls back on its handler": {
			"spa(parcels)", "parcels", "parcels", []string{"parcels", "spa"},
		},
		"an adapter that calls a handler that returns an error": {
			"adapted(d.failing)", "(*example.com/depot.desk).failing", "d.failing", []string{"(*example.com/depot.desk).failing", "adapted"},
		},
		"a function given two handlers": {
			"either(parcels, depots)", "either", "either", []string{"depots", "either", "parcels"},
		},
		"a factory inside middleware": {
			"logged(handleList(d, renderError))", "handleList", "handleList", []string{"handleList", "logged", "renderError"},
		},
		"a factory inside middleware of another module": {
			`http.TimeoutHandler(d.build(), 1, "late")`, "(*example.com/depot.desk).build", "d.build", []string{"(*example.com/depot.desk).build"},
		},
		"a middleware chain": {
			"chain(http.HandlerFunc(parcels), audited)", "parcels", "parcels", []string{"audited", "chain", "parcels"},
		},
		"middleware alone": {
			"logged(http.HandlerFunc(parcels))", "parcels", "parcels", []string{"logged", "parcels"},
		},
		"a conversion of a handler that returns an error": {
			"failable(d.failing)", "(*example.com/depot.desk).failing", "d.failing", []string{"(*example.com/depot.desk).failing", "(example.com/depot.failable).ServeHTTP"},
		},
		"a handler alone": {
			"http.HandlerFunc(parcels)", "parcels", "parcels", []string{"parcels"},
		},
	}

	for name, test := range cases {
		source := `package main

import "net/http"

type desk struct{}

func (d *desk) failing(w http.ResponseWriter, r *http.Request) error { return nil }

func (d *desk) build() http.HandlerFunc { return nil }

type failable func(http.ResponseWriter, *http.Request) error

func (f failable) ServeHTTP(w http.ResponseWriter, r *http.Request) { _ = f(w, r) }

func renderError(w http.ResponseWriter, r *http.Request, err error) {}

func handleList(d *desk, fail func(http.ResponseWriter, *http.Request, error)) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) { fail(w, r, nil) }
}

func spa(fallback http.HandlerFunc) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) { fallback(w, r) }
}

func adapted(next func(http.ResponseWriter, *http.Request) error) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) { _ = next(w, r) }
}

func either(a, b http.HandlerFunc) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) { a.ServeHTTP(w, r); b.ServeHTTP(w, r) }
}

func logged(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { next.ServeHTTP(w, r) })
}

func audited(next http.Handler) http.Handler { return logged(next) }

func chain(h http.Handler, steps ...func(http.Handler) http.Handler) http.Handler {
	for _, step := range steps {
		h = step(h)
	}
	return h
}

func main() {
	d := &desk{}
	_ = d
	http.Handle("/x", ` + test.argument + `)
}
` + handlerFuncs
		site := onlySite(t, source)

		wantRoots := []string{}
		for _, root := range test.wantRoots {
			wantRoots = append(wantRoots, depotID(root))
		}
		if site.HandlerID == nil || *site.HandlerID != depotID(test.wantAnchor) || site.Args["handler"] != test.wantShown {
			t.Errorf("%s: anchor %v shown as %q, want %s", name, site.HandlerID, site.Args["handler"], test.wantAnchor)
		}
		if !reflect.DeepEqual(site.Roots, wantRoots) {
			t.Errorf("%s: roots %v, want %v", name, site.Roots, wantRoots)
		}
	}
}

func TestRoutesFindsStripPrefixHoweverItIsReached(t *testing.T) {
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

		stripped := routesOf(t, routeQuery{root: root, mainPackage: ".", patterns: noPatterns}).StrippedPrefixes

		if len(stripped) != 1 || stripped[0].File != "main.go" {
			t.Errorf("%s: got %+v", name, stripped)
		}
	}
}

func TestRoutesReadsAFileServerUnderStripPrefixAsARoute(t *testing.T) {
	cases := map[string]string{
		"FileServer at the site": `package main

import "net/http"

func main() {
	http.Handle("/static/", http.StripPrefix("/static/", http.FileServer(http.Dir("public"))))
}
`,
		"FileServerFS at the site": `package main

import (
	"net/http"
	"os"
)

func main() {
	http.Handle("/static/", http.StripPrefix("/static/", http.FileServerFS(os.DirFS("public"))))
}
`,
		"behind an import name": `package main

import web "net/http"

func main() {
	web.Handle("/static/", web.StripPrefix("/static/", web.FileServer(web.Dir("public"))))
}
`,
		"in parentheses": `package main

import "net/http"

func main() {
	http.Handle("/static/", http.StripPrefix("/static/", (http.FileServer(http.Dir("public")))))
}
`,
		"on a mux": `package main

import "net/http"

func main() {
	mux := http.NewServeMux()
	mux.Handle("/static/", http.StripPrefix("/static/", http.FileServer(http.Dir("public"))))
	http.ListenAndServe(":0", mux)
}
`,
		"the stripped handler in a variable": `package main

import "net/http"

func main() {
	files := http.StripPrefix("/static/", http.FileServer(http.Dir("public")))
	http.Handle("/static/", files)
}
`,
	}

	for name, source := range cases {
		root := writeModule(t, "example.com/depot", map[string]string{"main.go": source})

		report := routesOf(t, routeQuery{root: root, mainPackage: ".", patterns: noPatterns})

		if len(report.StrippedPrefixes) != 0 {
			t.Errorf("%s: got %+v", name, report.StrippedPrefixes)
		}
		if got := sitePaths(report.Sites); !slices.Equal(got, []string{"/static/"}) {
			t.Errorf("%s: sites %v", name, got)
		}
		if report.Sites[0].HandlerID != nil {
			t.Errorf("%s: a file server has no function of the service, got %s", name, *report.Sites[0].HandlerID)
		}
	}
}

func TestRoutesStopsAtEveryOtherStripPrefix(t *testing.T) {
	cases := map[string]string{
		"a variable that holds a file server": `package main

import "net/http"

func main() {
	files := http.FileServer(http.Dir("public"))
	http.Handle("/static/", http.StripPrefix("/static/", files))
}
`,
		"a function of the service with the same name": `package main

import "net/http"

func FileServer(root http.FileSystem) http.Handler { return http.NotFoundHandler() }

func main() {
	http.Handle("/static/", http.StripPrefix("/static/", FileServer(http.Dir("public"))))
}
`,
		"middleware around a file server": `package main

import "net/http"

func logged(next http.Handler) http.Handler { return next }

func main() {
	http.Handle("/static/", http.StripPrefix("/static/", logged(http.FileServer(http.Dir("public")))))
}
`,
		"a conversion around a file server": `package main

import "net/http"

func main() {
	http.Handle("/static/", http.StripPrefix("/static/", http.Handler(http.FileServer(http.Dir("public")))))
}
`,
		"a file server reached through a value": `package main

import "net/http"

func main() {
	serve := http.FileServer
	http.Handle("/static/", http.StripPrefix("/static/", serve(http.Dir("public"))))
}
`,
		"both arguments from one call": `package main

import "net/http"

func both() (string, http.Handler) { return "/static/", http.FileServer(http.Dir("public")) }

func main() {
	http.Handle("/static/", http.StripPrefix(both()))
}
`,
		"a file server as the path of something else": `package main

import "net/http"

func main() {
	http.Handle("/x/", http.StripPrefix("/x/", http.TimeoutHandler(http.FileServer(http.Dir("public")), 1, "slow")))
}
`,
	}

	for name, source := range cases {
		root := writeModule(t, "example.com/depot", map[string]string{"main.go": source})

		if got := routesOf(t, routeQuery{root: root, mainPackage: ".", patterns: noPatterns}).StrippedPrefixes; len(got) != 1 {
			t.Errorf("%s: got %+v", name, got)
		}
	}
}

func TestRoutesReadsAFileServerUnderStripPrefixBesideACgoFile(t *testing.T) {
	requireACCompiler(t)
	root := writeModule(t, "example.com/depot", map[string]string{
		"c.go": "package main\n\n// int two(void) { return 2; }\nimport \"C\"\n\nvar two = int(C.two())\n",
		"main.go": `package main

import "net/http"

func main() {
	http.Handle("/static/", http.StripPrefix("/static/", http.FileServer(http.Dir("public"))))
}
`,
	})

	report := routesOf(t, routeQuery{root: root, mainPackage: ".", patterns: noPatterns})

	if len(report.StrippedPrefixes) != 0 || !slices.Equal(sitePaths(report.Sites), []string{"/static/"}) {
		t.Fatalf("got %+v", report)
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

	if got := routesOf(t, routeQuery{root: root, mainPackage: ".", patterns: noPatterns}).StrippedPrefixes; len(got) != 0 {
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

	got := sitePaths(routesOf(t, routeQuery{root: root, mainPackage: ".", patterns: patterns}).Sites)

	if want := []string{"/always", "/known-inside-the-wrapper", "POST /orders"}; !reflect.DeepEqual(got, want) {
		t.Fatalf("got %v, want %v", got, want)
	}
}

func TestRoutesKeepsTheSiteInsideAWrapperWhenNoPatternMatchesIt(t *testing.T) {
	root := writeModule(t, "example.com/depot", map[string]string{"main.go": wrapperModule})

	sites := routesOf(t, routeQuery{root: root, mainPackage: ".", patterns: noPatterns}).Sites

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

// aWrapperRegistering puts the site in a wrapper that takes the method, the path and the handler.
func aWrapperRegistering(site string) string {
	return `package main

import (
	"net/http"
	paths "path"
	"strings"
)

const versionRoot = "/v1"

var elsewhere = "/x"

var _, _ = strings.Join, paths.Join

type gate struct {
	mux    *http.ServeMux
	prefix string
}

func (g gate) post(method, path string, h http.HandlerFunc) {
	` + site + `
}

func main() {
	gate{mux: http.NewServeMux()}.post("PUT", "/items", parcels)
}
` + handlerFuncs
}

func lineHolding(t *testing.T, source, text string) int {
	t.Helper()
	for number, line := range strings.Split(source, "\n") {
		if strings.Contains(line, text) {
			return number + 1
		}
	}
	t.Fatalf("no line holds %q", text)
	return 0
}

func unreadSitesOf(t *testing.T, source, patterns string) []unreadSiteOutput {
	t.Helper()
	root := writeModule(t, "example.com/depot", map[string]string{"main.go": source})
	return routesOf(t, routeQuery{root: root, mainPackage: ".", patterns: patterns}).UnreadSites
}

const postWithoutAMethod = `[{"kind":"route","call":"\\.post$","args":{"path":1,"handler":2}}]`

const postWithAMethod = `[{"kind":"route","call":"\\.post$","args":{"method":0,"path":1,"handler":2}}]`

func TestRoutesStopsAtAWrapperThatBuildsItsPathFromMoreThanItsParameters(t *testing.T) {
	cases := map[string]struct {
		site     string
		patterns string
	}{
		"a slash in front":                   {`g.mux.HandleFunc("/v1"+path, h)`, postWithoutAMethod},
		"a method and a slash":               {`g.mux.HandleFunc("POST /v1"+path, h)`, postWithoutAMethod},
		"a const holding the slash":          {`g.mux.HandleFunc(versionRoot+path, h)`, postWithoutAMethod},
		"a prefix held in a field":           {`g.mux.HandleFunc("GET "+g.prefix+path, h)`, postWithoutAMethod},
		"a slash behind":                     {`g.mux.HandleFunc(path+"/", h)`, postWithoutAMethod},
		"a call that joins":                  {`g.mux.HandleFunc(strings.Join([]string{"/v1", path}, ""), h)`, postWithoutAMethod},
		"a suffix joined on":                 {`g.mux.HandleFunc("GET "+paths.Join(path, "{id}"), h)`, postWithoutAMethod},
		"a local variable":                   {`item := path + "/{id}"; g.mux.HandleFunc("GET "+item, h)`, postWithoutAMethod},
		"a variable that is no parameter":    {`g.mux.HandleFunc("GET "+elsewhere, h)`, postWithoutAMethod},
		"a method the pattern does not name": {`g.mux.HandleFunc(method+" "+path, h)`, postWithoutAMethod},
		"a method and two spaces":            {`g.mux.HandleFunc(method+"  "+path, h)`, postWithAMethod},
		"a method in lower case":             {`g.mux.HandleFunc("get "+path, h)`, postWithoutAMethod},
		"the path before the method":         {`g.mux.HandleFunc(path+" "+method, h)`, postWithAMethod},
	}

	for name, test := range cases {
		source := aWrapperRegistering(test.site)

		got := unreadSitesOf(t, source, test.patterns)

		want := []unreadSiteOutput{{File: "main.go", Line: lineHolding(t, source, "HandleFunc("), Problem: "builds its path"}}
		if !reflect.DeepEqual(got, want) {
			t.Errorf("%s: got %+v, want %+v", name, got, want)
		}
	}
}

// A parameter that is still the same variable can hold another value by the time the site reads it,
// so a write anywhere in the wrapper, a closure's included, stops the run and names the write.
func TestRoutesStopsAtAWrapperThatWritesAParameterItRegisters(t *testing.T) {
	cases := map[string]struct {
		site     string
		write    string
		patterns string
	}{
		"a prefix from a field": {"path = g.prefix + path\n\tg.mux.HandleFunc(\"GET \"+path, h)", "path = g.prefix", postWithoutAMethod},
		"a prefix written in":   {"path = \"/v1\" + path\n\tg.mux.HandleFunc(path, h)", "path = \"/v1\"", postWithoutAMethod},
		"a slash added in a branch": {
			"if !strings.HasSuffix(path, \"/\") {\n\t\tpath += \"/\"\n\t}\n\tg.mux.HandleFunc(\"GET \"+path, h)", "path += ", postWithoutAMethod,
		},
		"the method replaced":         {"method = \"PUT\"\n\tg.mux.HandleFunc(method+\" \"+path, h)", "method = ", postWithAMethod},
		"the path declared again":     {"path, found := \"/v1\"+path, true\n\t_ = found\n\tg.mux.HandleFunc(\"GET \"+path, h)", "path, found", postWithoutAMethod},
		"the path written by a range": {"for path = range map[string]int{\"/x\": 1} {\n\t}\n\tg.mux.HandleFunc(\"GET \"+path, h)", "for path = range", postWithoutAMethod},
		"the address taken":           {"where := &path\n\t_ = where\n\tg.mux.HandleFunc(\"GET \"+path, h)", "where := &path", postWithoutAMethod},
		"a write inside a closure":    {"widen := func() { path = \"/v1\" + path }\n\twiden()\n\tg.mux.HandleFunc(\"GET \"+path, h)", "widen := func", postWithoutAMethod},
	}

	for name, test := range cases {
		source := aWrapperRegistering(test.site)

		got := unreadSitesOf(t, source, test.patterns)

		written := &placeOutput{File: "main.go", Line: lineHolding(t, source, test.write)}
		want := []unreadSiteOutput{{File: "main.go", Line: lineHolding(t, source, "HandleFunc("), Problem: "builds its path", WrittenAt: written}}
		if !reflect.DeepEqual(got, want) {
			t.Errorf("%s: got %+v, want %+v", name, got, want)
		}
	}
}

func TestRoutesReadsAWrapperThatOnlyReadsItsParameters(t *testing.T) {
	source := aWrapperRegistering("if path == \"\" || method == \"\" {\n\t\treturn\n\t}\n\tg.mux.HandleFunc(method+\" \"+path, h)")
	root := writeModule(t, "example.com/depot", map[string]string{"main.go": source})

	output := routesOf(t, routeQuery{root: root, mainPackage: ".", patterns: postWithAMethod})

	if got := sitePaths(output.Sites); !reflect.DeepEqual(got, []string{"PUT /items"}) || len(output.UnreadSites) != 0 {
		t.Fatalf("got %v and %+v", got, output.UnreadSites)
	}
}

func TestRoutesReadsThePathAWrapperRegistersAtEachCall(t *testing.T) {
	cases := map[string]string{
		`path`:                    "/items",
		`"POST "+path`:            "POST /items",
		`"GET "+path`:             "GET /items",
		`method+" "+path`:         "PUT /items",
		`(method + " ") + (path)`: "PUT /items",
	}

	for argument, want := range cases {
		source := aWrapperRegistering(`g.mux.HandleFunc(` + argument + `, h)`)
		root := writeModule(t, "example.com/depot", map[string]string{"main.go": source})

		output := routesOf(t, routeQuery{root: root, mainPackage: ".", patterns: postWithAMethod})

		if got := sitePaths(output.Sites); !reflect.DeepEqual(got, []string{want}) || len(output.UnreadSites) != 0 {
			t.Errorf("%s: got %v and %+v, want %s", argument, got, output.UnreadSites, want)
		}
	}
}

func TestRoutesReadsOneRouteForEachSiteInsideAWrapper(t *testing.T) {
	source := aWrapperRegistering(`g.mux.HandleFunc("GET "+path, h); g.mux.HandleFunc("HEAD "+path, h)`)
	root := writeModule(t, "example.com/depot", map[string]string{"main.go": source})

	got := sitePaths(routesOf(t, routeQuery{root: root, mainPackage: ".", patterns: postWithoutAMethod}).Sites)

	if want := []string{"GET /items", "HEAD /items"}; !reflect.DeepEqual(got, want) {
		t.Fatalf("got %v, want %v", got, want)
	}
}

// The reviewer's x1: a wrapper registers a CORS preflight beside each route. curl OPTIONS /items is
// served by preflight, and items never runs.
const aWrapperWithAFixedCompanion = `package main

import "net/http"

type app struct{ mux *http.ServeMux }

func (a *app) get(path string, h http.HandlerFunc) {
	a.mux.HandleFunc("OPTIONS "+path, preflight)
	a.mux.HandleFunc("GET "+path, logged(h))
}

func logged(next http.HandlerFunc) http.HandlerFunc { return next }

func preflight(w http.ResponseWriter, r *http.Request) {}

func items(w http.ResponseWriter, r *http.Request) {}

func orders(w http.ResponseWriter, r *http.Request) {}

func main() {
	a := &app{mux: http.NewServeMux()}
	a.get("/items", items)
	a.get("/orders", orders)
}
`

func TestRoutesReadsASiteInsideAWrapperThatHoldsNoHandlerItIsGivenByItsOwnHandler(t *testing.T) {
	root := writeModule(t, "example.com/depot", map[string]string{"main.go": aWrapperWithAFixedCompanion})

	sites := routesOf(t, routeQuery{root: root, mainPackage: ".", patterns: getOnA}).Sites

	type reading struct {
		anchor string
		roots  []string
	}
	got, identities := map[string]reading{}, map[string]string{}
	for _, site := range sites {
		got[site.Literals["path"]] = reading{anchor: *site.HandlerID, roots: site.Roots}
		identities[site.Literals["path"]] = site.HandlerIdentity
	}
	want := map[string]reading{
		"OPTIONS /items":  {"example.com/depot.preflight", []string{"(*example.com/depot.app).get", "example.com/depot.preflight"}},
		"OPTIONS /orders": {"example.com/depot.preflight", []string{"(*example.com/depot.app).get", "example.com/depot.preflight"}},
		"GET /items":      {"example.com/depot.items", []string{"(*example.com/depot.app).get", "example.com/depot.items", "example.com/depot.logged"}},
		"GET /orders":     {"example.com/depot.orders", []string{"(*example.com/depot.app).get", "example.com/depot.logged", "example.com/depot.orders"}},
	}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("got %v, want %v", got, want)
	}
	if identities["OPTIONS /items"] != identities["OPTIONS /orders"] || identities["GET /items"] == identities["GET /orders"] {
		t.Fatalf("identities %v", identities)
	}
}

func TestRoutesStopsAtAWrapperThatHandsItsPathToAnotherWrapper(t *testing.T) {
	source := `package main

import "net/http"

type gate struct{ mux *http.ServeMux }

func (g gate) get(path string, h http.HandlerFunc) {
	g.mux.HandleFunc("GET "+path, h)
}

func (g gate) item(path string, h http.HandlerFunc) {
	g.get(path, h)
}

func main() {
	gate{mux: http.NewServeMux()}.item("/items", parcels)
}
` + handlerFuncs
	patterns := `[{"kind":"route","call":"\\.(get|item)$","args":{"path":0,"handler":1}}]`

	got := unreadSitesOf(t, source, patterns)

	if want := []unreadSiteOutput{{File: "main.go", Line: 12, Problem: "hands on its path"}}; !reflect.DeepEqual(got, want) {
		t.Fatalf("got %+v, want %+v", got, want)
	}
}

func TestRoutesStopsAtACallToAWrapperThatRegistersNothing(t *testing.T) {
	cases := map[string]struct {
		source   string
		patterns string
	}{
		"a mux as the handler": {`package main

import "net/http"

func mount(mux *http.ServeMux, prefix string) {
	mux.HandleFunc(prefix, parcels)
}

func main() {
	mount(http.NewServeMux(), "/api/")
}
` + handlerFuncs, `[{"kind":"route","call":"^mount$","args":{"path":1,"handler":0}}]`},
		"no site inside": {`package main

import "net/http"

func announce(path string, h http.HandlerFunc) {}

func main() {
	announce("/api/", parcels)
}
` + handlerFuncs, `[{"kind":"route","call":"^announce$","args":{"path":0,"handler":1}}]`},
		"a known path alone inside": {`package main

import "net/http"

func announce(path string, h http.HandlerFunc) {
	http.HandleFunc("/fixed", h)
}

func main() {
	announce("/api/", parcels)
}
` + handlerFuncs, `[{"kind":"route","call":"^announce$","args":{"path":0,"handler":1}}]`},
	}

	for name, test := range cases {
		got := unreadSitesOf(t, test.source, test.patterns)

		want := []unreadSiteOutput{{File: "main.go", Line: lineHolding(t, test.source, `"/api/"`), Problem: "registers nothing"}}
		if !reflect.DeepEqual(got, want) {
			t.Errorf("%s: got %+v, want %+v", name, got, want)
		}
	}
}

// aRegistrarUsedAs puts a use of the wrapper get, or of a registration of net/http, beside one
// direct call to get that the pattern matches.
func aRegistrarUsedAs(use string) string {
	return `package main

import "net/http"

type app struct{ mux *http.ServeMux }

func (a *app) get(path string, h http.HandlerFunc) {
	a.mux.HandleFunc("POST "+path, h)
}

type registrar interface {
	get(path string, h http.HandlerFunc)
}

// bigger is one *app alone does not satisfy, and outer does, through the get it embeds.
type bigger interface {
	get(path string, h http.HandlerFunc)
	close()
}

type outer struct{ *app }

func (outer) close() {}

type muxLike interface {
	HandleFunc(pattern string, handler func(http.ResponseWriter, *http.Request))
}

type keeper struct {
	add  func(string, http.HandlerFunc)
	adds []func(string, http.HandlerFunc)
}

func registerShop(add func(string, http.HandlerFunc)) { add("/items", parcels) }

func main() {
	a := &app{mux: http.NewServeMux()}
	var k keeper
	var r registrar = a
	var m muxLike = a.mux
	_, _, _ = k, r, m
	a.get("/healthz", parcels)
	` + use + `
}
` + handlerFuncs
}

const getOnA = `[{"kind":"route","call":"^a\\.get$","args":{"path":0,"handler":1}}]`

const getAnywhere = `[{"kind":"route","call":"\\.get$","args":{"path":0,"handler":1}}]`

// A call ARDE cannot see is a route it cannot list, so every use of a function that registers
// routes must be a direct call that a pattern reads.
func TestRoutesStopsAtAUseOfAFunctionThatRegistersRoutesOtherThanADirectCall(t *testing.T) {
	cases := map[string]struct {
		use     string
		problem string
	}{
		"an argument":                    {`registerShop(a.get)`, "used as a value"},
		"an assignment":                  {`g := a.get; g("/direct", parcels)`, "used as a value"},
		"a store into a field":           {`k.add = a.get`, "used as a value"},
		"a store into a slice":           {`k.adds = append(k.adds, a.get)`, "used as a value"},
		"a method expression":            {`(*app).get(a, "/items", parcels)`, "used as a value"},
		"a call through an interface":    {`r.get("/items", parcels)`, "called through an interface"},
		"an interface's method value":    {`f := r.get; f("/items", parcels)`, "called through an interface"},
		"a type that embeds the wrapper": {`var o bigger = outer{a}; o.get("/items", parcels)`, "called through an interface"},
		"a mux's method value":           {`add := a.mux.HandleFunc; add("/x", parcels)`, "used as a value"},
		"net/http's function as a value": {`register := http.HandleFunc; register("/x", parcels)`, "used as a value"},
		"a mux behind an interface":      {`m.HandleFunc("/x", parcels)`, "called through an interface"},
	}

	for name, test := range cases {
		source := aRegistrarUsedAs(test.use)

		got := unreadSitesOf(t, source, getOnA)

		want := []unreadSiteOutput{{File: "main.go", Line: lineHolding(t, source, test.use), Problem: test.problem}}
		if !reflect.DeepEqual(got, want) {
			t.Errorf("%s: got %+v, want %+v", name, got, want)
		}
	}
}

// A pattern that matches the call through the interface reads it as a call that registers its own
// route, with the method from its name, while the wrapper registers POST.
func TestRoutesStopsAtACallThroughAnInterfaceThatAPatternMatchesToo(t *testing.T) {
	source := aRegistrarUsedAs(`r.get("/items", parcels)`)

	got := unreadSitesOf(t, source, getAnywhere)

	want := []unreadSiteOutput{{File: "main.go", Line: lineHolding(t, source, `r.get("/items"`), Problem: "called through an interface"}}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("got %+v, want %+v", got, want)
	}
}

func TestRoutesReadsACallThroughAnInterfaceThatNoRegistrarSatisfies(t *testing.T) {
	source := aRegistrarUsedAs(`var s interface{ get(string) } = silent{}; s.get("/x")`) +
		"\ntype silent struct{}\n\nfunc (silent) get(path string) {}\n"
	root := writeModule(t, "example.com/depot", map[string]string{"main.go": source})

	output := routesOf(t, routeQuery{root: root, mainPackage: ".", patterns: getOnA})

	if len(output.UnreadSites) != 0 || !reflect.DeepEqual(sitePaths(output.Sites), []string{"POST /healthz"}) {
		t.Fatalf("got %+v and %v", output.UnreadSites, sitePaths(output.Sites))
	}
}

// A route registered through a wrapper is shaped by the wrapper's own code on every request, so the
// wrapper, the functions its site names and the functions the call hands it are roots too.
func TestRoutesRootsARouteRegisteredThroughAWrapperAtTheWrapperToo(t *testing.T) {
	source := `package main

import "net/http"

type app struct{ mux *http.ServeMux }

type middleware func(http.HandlerFunc) http.HandlerFunc

func writeJSON(w http.ResponseWriter, v any) {}

func (a *app) getJSON(path string, load func(*http.Request) any) {
	a.mux.HandleFunc("GET "+path, func(w http.ResponseWriter, r *http.Request) { writeJSON(w, load(r)) })
}

func (a *app) get(path string, h http.HandlerFunc, wrap ...middleware) {
	for _, m := range wrap {
		h = m(h)
	}
	a.mux.HandleFunc("GET "+path, audited(h))
}

func audited(next http.HandlerFunc) http.HandlerFunc { return next }

func requireAdmin(next http.HandlerFunc) http.HandlerFunc { return next }

func (a *app) items(r *http.Request) any { return nil }

func main() {
	a := &app{mux: http.NewServeMux()}
	a.getJSON("/items", a.items)
	a.get("/admin", parcels, requireAdmin)
	a.get("/inline", parcels, func(next http.HandlerFunc) http.HandlerFunc { return next })
}
` + handlerFuncs
	root := writeModule(t, "example.com/depot", map[string]string{"main.go": source})
	patterns := `[{"kind":"route","call":"^a\\.(get|getJSON)$","args":{"path":0,"handler":1}}]`

	sites := routesOf(t, routeQuery{root: root, mainPackage: ".", patterns: patterns}).Sites

	got := map[string][]string{}
	for _, site := range sites {
		got[site.Literals["path"]] = site.Roots
	}
	want := map[string][]string{
		"GET /items":  {"(*example.com/depot.app).getJSON", "(*example.com/depot.app).items", "example.com/depot.writeJSON"},
		"GET /admin":  {"(*example.com/depot.app).get", "example.com/depot.audited", "example.com/depot.parcels", "example.com/depot.requireAdmin"},
		"GET /inline": {},
	}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("got %v, want %v", got, want)
	}
}

func TestRoutesStopsAtAMethodThatIsNotAConstant(t *testing.T) {
	source := `package main

import (
	"net/http"
	"os"
)

type gate struct{ mux *http.ServeMux }

func (g gate) post(method, path string, h http.HandlerFunc) {
	g.mux.HandleFunc(method+" "+path, h)
}

func main() {
	gate{mux: http.NewServeMux()}.post(os.Getenv("METHOD"), "/items", parcels)
}
` + handlerFuncs

	got := unreadSitesOf(t, source, postWithAMethod)

	if want := []unreadSiteOutput{{File: "main.go", Line: 15, Problem: "method is not a constant"}}; !reflect.DeepEqual(got, want) {
		t.Fatalf("got %+v, want %+v", got, want)
	}
}

// A router of another module registers its route itself, so the method its pattern names comes
// before the path, as the router's own method puts it.
func TestRoutesPutsTheMethodAPatternNamesBeforeThePathOfACallToAnotherModule(t *testing.T) {
	cases := map[string][]string{`"GET"`: {"GET /x"}, `os.Getenv("M")`: {}}

	for method, want := range cases {
		source := "package main\n\nimport (\n\t\"net/http\"\n\t\"os\"\n\n\t\"example.com/router\"\n)\n\nvar _ = os.Getenv\n\nfunc main() {\n\tr := &router.Router{}\n\tr.Method(" + method + ", \"/x\", parcels)\n}\n" + handlerFuncs
		root := writeTree(t, map[string]string{
			"go.mod":           "module example.com/depot\n\ngo 1.22\n\nrequire example.com/router v0.0.0\n\nreplace example.com/router => ./router\n",
			"router/go.mod":    "module example.com/router\n\ngo 1.22\n",
			"router/router.go": "package router\n\nimport \"net/http\"\n\ntype Router struct{}\n\nfunc (*Router) Method(method, path string, h http.HandlerFunc) {}\n",
			"main.go":          source,
		})
		patterns := `[{"kind":"route","call":"^r\\.Method$","args":{"method":0,"path":1,"handler":2}}]`

		output := routesOf(t, routeQuery{root: root, mainPackage: ".", patterns: patterns})

		if got := sitePaths(output.Sites); !reflect.DeepEqual(got, want) {
			t.Errorf("%s: got %v, want %v", method, got, want)
		}
		if len(want) == 0 && !reflect.DeepEqual(output.UnreadSites, []unreadSiteOutput{{File: "main.go", Line: 14, Problem: "method is not a constant"}}) {
			t.Errorf("%s: got %+v", method, output.UnreadSites)
		}
	}
}

func TestRoutesLeavesAnUnknownPathOutsideAWrapperToTheSite(t *testing.T) {
	source := `package main

import "net/http"

func main() {
	suffix := "x"
	http.HandleFunc("/v1/"+suffix, parcels)
}
` + handlerFuncs
	root := writeModule(t, "example.com/depot", map[string]string{"main.go": source})

	output := routesOf(t, routeQuery{root: root, mainPackage: ".", patterns: noPatterns})

	if len(output.UnreadSites) != 0 || len(output.Sites) != 1 {
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

	got := sitePaths(routesOf(t, routeQuery{root: root, mainPackage: ".", patterns: patterns}).Sites)

	if want := []string{"POST /one", "POST /two"}; !reflect.DeepEqual(got, want) {
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

	sites := routesOf(t, routeQuery{root: root, mainPackage: ".", patterns: patterns}).Sites

	if got, want := sitePaths(sites), []string{"/backwards"}; !reflect.DeepEqual(got, want) {
		t.Fatalf("got %v, want %v", got, want)
	}
	if sites[0].Kind != "route" || sites[0].Callee != "register" || *sites[0].HandlerID != "example.com/depot.parcels" {
		t.Fatalf("got %+v", sites[0])
	}
}

func TestRoutesLeavesOutAHandlerPositionPastTheArguments(t *testing.T) {
	root := writeModule(t, "example.com/depot", map[string]string{"main.go": "package main\n\nimport \"net/http\"\n\nfunc main() {\n\thttp.HandleFunc(\"/x\", parcels)\n}\n" + handlerFuncs})
	patterns := `[{"kind":"route","call":"^http\\.HandleFunc$","args":{"path":0,"handler":9}}]`

	site := routesOf(t, routeQuery{root: root, mainPackage: ".", patterns: patterns}).Sites[0]

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

	got := routesOf(t, routeQuery{root: root, mainPackage: ".", patterns: patterns}).WrapperCalls

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

	output := routesOf(t, routeQuery{root: root, mainPackage: "./cmd/tracker", patterns: noPatterns})

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

	got := sitePaths(routesOf(t, routeQuery{root: root, mainPackage: "./cmd/api", patterns: noPatterns}).Sites)

	if want := []string{"/added-by-an-import", "/api"}; !reflect.DeepEqual(got, want) {
		t.Fatalf("got %v, want %v", got, want)
	}
}

func TestRoutesReadsAFolderTheGoToolPassesOverWhenTheServiceImportsIt(t *testing.T) {
	root := writeModule(t, "example.com/depot", map[string]string{
		"main.go":        "package main\n\nimport _ \"example.com/depot/_extra\"\n\nfunc main() {}\n",
		"_extra/more.go": "package extra\n\nimport \"net/http\"\n\nfunc init() {\n\thttp.HandleFunc(\"/behind-an-underscore\", http.NotFound)\n}\n",
	})

	got := sitePaths(routesOf(t, routeQuery{root: root, mainPackage: ".", patterns: noPatterns}).Sites)

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

	output := routesOf(t, routeQuery{root: root, mainPackage: ".", patterns: noPatterns})

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

	output := routesOf(t, routeQuery{root: root, mainPackage: ".", patterns: noPatterns})

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
	want := `{"package_folders":["."],"build_files":["main.go"],"sites":[],"stripped_prefixes":[],"unread_sites":[],"wrapper_calls":[]}` + "\n"
	if stdout != want {
		t.Fatalf("got %s, want %s", stdout, want)
	}
}

func TestRoutesFailsLoudly(t *testing.T) {
	cases := map[string]struct {
		files       map[string]string
		mainPackage string
		mustMention string
	}{
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

func TestABadMainPackageIsAProblemOfTheProfile(t *testing.T) {
	files := twoServices()
	files["empty/notes.txt"] = "no Go here"
	files["tagged/tagged.go"] = "//go:build e2e\n\npackage main\n\nfunc main() {}\n"
	files["nested/go.mod"] = "module example.com/nested\n\ngo 1.27\n"
	files["nested/main.go"] = "package main\n\nfunc main() {}\n"
	root := writeModule(t, "example.com/depot", files)
	cases := map[string]string{
		"a package that does not exist":   "./cmd/nothing",
		"several packages":                "./...",
		"a file and not a folder":         "./cmd/tracker/main.go",
		"a folder with no Go file":        "./empty",
		"a folder whose files are tagged": "./tagged",
		"a folder of another module":      "./nested",
	}

	for name, mainPackage := range cases {
		exitCode, stdout, stderr := runCommand(t, "routes", "--patterns", writePatterns(t, noPatterns), "--main", mainPackage, root)

		if exitCode != exitProfileProblem || stdout != "" {
			t.Errorf("%s: exit %d, stdout %q, stderr %q", name, exitCode, stdout, stderr)
		}
		if !strings.HasPrefix(stderr, "goinspect: bad main_package: ") {
			t.Errorf("%s: stderr must start with the mark the client reads, got %q", name, stderr)
		}
	}
}

func TestAPackageThatNamesAFileAtFaultIsAProblemOfTheRepo(t *testing.T) {
	cases := map[string]map[string]string{
		"a package clause that does not parse": {"main.go": "packag main\n"},
		"an import nothing provides":           {"main.go": "package main\n\nimport _ \"example.invalid/nothing\"\n\nfunc main() {}\n"},
	}

	for name, files := range cases {
		root := writeModule(t, "example.com/depot", files)

		exitCode, _, stderr := runCommand(t, "routes", "--patterns", writePatterns(t, noPatterns), "--main", ".", root)

		if exitCode != 1 || strings.HasPrefix(stderr, "goinspect: bad") {
			t.Errorf("%s: exit %d, stderr %q", name, exitCode, stderr)
		}
	}
}

func TestABrokenGoModIsAProblemOfTheRepo(t *testing.T) {
	root := writeTree(t, map[string]string{"go.mod": "module\n\nthis is not a module file\n", "main.go": "package main\n"})

	exitCode, _, stderr := runCommand(t, "routes", "--patterns", writePatterns(t, noPatterns), "--main", ".", root)

	if exitCode != 1 || strings.HasPrefix(stderr, "goinspect: bad") {
		t.Fatalf("exit %d, stderr %q", exitCode, stderr)
	}
}

func TestFingerprintsNamesABadMainPackageTheSameWay(t *testing.T) {
	root := writeModule(t, "example.com/depot", twoServices())
	rootsFile := writeTree(t, map[string]string{"roots.json": `["example.com/depot.x"]`}) + "/roots.json"

	exitCode, _, stderr := runCommand(t, "fingerprints", "--roots", rootsFile, "--main", "./cmd/nothing", root)

	if exitCode != exitProfileProblem || !strings.HasPrefix(stderr, "goinspect: bad main_package: ") {
		t.Fatalf("exit %d, stderr %q", exitCode, stderr)
	}
}

func TestAMissingPatternsFileIsNoProblemOfTheProfile(t *testing.T) {
	root := writeModule(t, "example.com/depot", twoServices())

	exitCode, _, stderr := runCommand(t, "routes", "--patterns", filepath.Join(t.TempDir(), "gone.json"), "--main", ".", root)

	if exitCode != 1 || strings.HasPrefix(stderr, "goinspect: bad") {
		t.Fatalf("exit %d, stderr %q", exitCode, stderr)
	}
}

func TestRoutesIsNotStoppedByABrokenPackageOutsideTheService(t *testing.T) {
	files := twoServices()
	files["cmd/console/oops.go"] = "package main\n\nvar broken int = \"text\"\n"
	root := writeModule(t, "example.com/depot", files)

	got := sitePaths(routesOf(t, routeQuery{root: root, mainPackage: "./cmd/tracker", patterns: noPatterns}).Sites)

	if want := []string{"/track"}; !reflect.DeepEqual(got, want) {
		t.Fatalf("got %v, want %v", got, want)
	}
}

func TestRoutesReadsNoGoFileBeforeTheBadPatternStopsIt(t *testing.T) {
	root := writeTree(t, map[string]string{"not-a-module.txt": "x"})

	exitCode, _, stderr := runCommand(t, "routes", "--patterns", writePatterns(t, `[{"kind":"route","call":"(","args":{}}]`), "--main", ".", root)

	if exitCode != exitProfileProblem || !strings.Contains(stderr, "does not compile") {
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

// handlerTypes are the module's own types that serve requests, for the tests on a value as the
// handler.
const handlerTypes = `
type items struct{ names []string }

func (h *items) ServeHTTP(w http.ResponseWriter, r *http.Request) { h.render(w) }

func (h *items) render(w http.ResponseWriter) {}

type plain struct{}

func (plain) ServeHTTP(w http.ResponseWriter, r *http.Request) {}

type apiFunc func(w http.ResponseWriter, r *http.Request) error

func (f apiFunc) ServeHTTP(w http.ResponseWriter, r *http.Request) { _ = f(w, r) }

type guard struct{ next http.Handler }

func (g *guard) ServeHTTP(w http.ResponseWriter, r *http.Request) { g.next.ServeHTTP(w, r) }

type desk struct{ items *items }

func failing(w http.ResponseWriter, r *http.Request) error { return nil }

func newItems() *items { return &items{} }

func logged(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { next.ServeHTTP(w, r) })
}

var shared = &items{}

var table = map[string]*items{}
`

func aHandlerRegistered(argument string) string {
	return `package main

import "net/http"
` + handlerTypes + `
func main() {
	h := &items{}
	d := desk{items: h}
	_ = d
	http.Handle("/x", ` + argument + `)
}
`
}

// A value of a module type that serves requests itself is anchored at its ServeHTTP, which is the
// whole handler, and the methods of a module type the handler hands to an interface are roots,
// because the static graph cannot follow a call through the interface.
func TestRoutesAnchorsAValueThatServesItselfAtItsServeHTTP(t *testing.T) {
	itemsMethods := []string{"(*example.com/depot.items).ServeHTTP", "(*example.com/depot.items).render"}
	cases := map[string]struct {
		argument   string
		wantAnchor string
		wantShown  string
		wantRoots  []string
	}{
		"a composite literal's address": {`&items{names: []string{"b"}}`, "(*example.com/depot.items).ServeHTTP", "items", itemsMethods},
		"a composite literal":           {`plain{}`, "(example.com/depot.plain).ServeHTTP", "plain", []string{"(example.com/depot.plain).ServeHTTP"}},
		"a variable":                    {`h`, "(*example.com/depot.items).ServeHTTP", "h", itemsMethods},
		"a package variable":            {`shared`, "(*example.com/depot.items).ServeHTTP", "shared", itemsMethods},
		"a field":                       {`d.items`, "(*example.com/depot.items).ServeHTTP", "d.items", itemsMethods},
		"a table entry":                 {`table["x"]`, "(*example.com/depot.items).ServeHTTP", `table["x"]`, itemsMethods},
		"behind middleware":             {`logged(&items{})`, "(*example.com/depot.items).ServeHTTP", "items", append(slices.Clone(itemsMethods), "example.com/depot.logged")},
		"a conversion of a function":    {`apiFunc(failing)`, "example.com/depot.failing", "failing", []string{"(example.com/depot.apiFunc).ServeHTTP", "example.com/depot.failing"}},
		"a factory of its own type":     {`newItems()`, "example.com/depot.newItems", "newItems", append(slices.Clone(itemsMethods), "example.com/depot.newItems")},
		"middleware given its own type": {`logged(newItems())`, "example.com/depot.newItems", "newItems", []string{"(*example.com/depot.items).ServeHTTP", "(*example.com/depot.items).render", "example.com/depot.logged", "example.com/depot.newItems"}},
		"a handler holding another":     {`&guard{next: newItems()}`, "(*example.com/depot.guard).ServeHTTP", "guard", []string{"(*example.com/depot.guard).ServeHTTP", "(*example.com/depot.items).ServeHTTP", "(*example.com/depot.items).render", "example.com/depot.newItems"}},
	}

	for name, test := range cases {
		site := onlySite(t, aHandlerRegistered(test.argument))

		if site.HandlerID == nil || *site.HandlerID != test.wantAnchor || site.Args["handler"] != test.wantShown {
			t.Errorf("%s: anchor %v shown as %q, want %s", name, site.HandlerID, site.Args["handler"], test.wantAnchor)
		}
		if !reflect.DeepEqual(site.Roots, test.wantRoots) {
			t.Errorf("%s: roots %v, want %v", name, site.Roots, test.wantRoots)
		}
	}
}

// Middleware serves through the handler it is given however it is written, so each spelling anchors
// at the handler inside, and a function that only looks at its handler is a factory.
func TestRoutesAnchorsAtTheHandlerInsideMiddlewareHoweverItServesThroughIt(t *testing.T) {
	cases := map[string]struct {
		argument   string
		wantAnchor string
	}{
		"calling it":                    {"called(parcels)", "parcels"},
		"calling its ServeHTTP":         {"served(http.HandlerFunc(parcels))", "parcels"},
		"taking its ServeHTTP":          {"passed(http.HandlerFunc(parcels))", "parcels"},
		"handing its ServeHTTP on":      {"handedOn(http.HandlerFunc(parcels))", "parcels"},
		"keeping it in a handler":       {"secured(http.HandlerFunc(parcels))", "parcels"},
		"keeping it in a handler value": {"kept(http.HandlerFunc(parcels))", "parcels"},
		"only looking at it":            {"described(http.HandlerFunc(parcels))", "described"},
	}

	for name, test := range cases {
		source := `package main

import (
	"fmt"
	"net/http"
)

func called(next http.HandlerFunc) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) { next(w, r) }
}

func served(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { next.ServeHTTP(w, r) })
}

func passed(next http.Handler) http.Handler { return http.HandlerFunc(next.ServeHTTP) }

func timed(next http.HandlerFunc) http.HandlerFunc { return next }

func handedOn(next http.Handler) http.Handler { return timed(next.ServeHTTP) }

type headerMiddleware struct{ next http.Handler }

func (m *headerMiddleware) ServeHTTP(w http.ResponseWriter, r *http.Request) { m.next.ServeHTTP(w, r) }

func secured(next http.Handler) http.Handler { return &headerMiddleware{next: next} }

type plainMiddleware struct{ next http.Handler }

func (m plainMiddleware) ServeHTTP(w http.ResponseWriter, r *http.Request) { m.next.ServeHTTP(w, r) }

func kept(next http.Handler) http.Handler { return plainMiddleware{next} }

func described(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { fmt.Fprint(w, next) })
}

func main() {
	http.Handle("/x", ` + test.argument + `)
}
` + handlerFuncs
		site := onlySite(t, source)

		if site.HandlerID == nil || *site.HandlerID != depotID(test.wantAnchor) {
			t.Errorf("%s: anchor %v, want %s", name, site.HandlerID, test.wantAnchor)
		}
	}
}

// Two routes that share an anchor share their docs, so the handlers must be told apart by what
// they hold: the functions they name, the variables they read and the constants they pass.
// The reviewer's c1 and c2: middleware that takes its settings first, as requireRole("admin")(h),
// cors(opts)(h) and rateLimit(n)(h) do.
const curriedMiddleware = `package main

import "net/http"

func requireRole(role string) func(http.Handler) http.Handler {
	return func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			if r.Header.Get("X-Role") != role {
				return
			}
			next.ServeHTTP(w, r)
		})
	}
}

func page(title string) func(http.Handler) http.Handler {
	return func(http.Handler) http.Handler { return http.NotFoundHandler() }
}

func listItems(w http.ResponseWriter, r *http.Request) {}

func listOrders(w http.ResponseWriter, r *http.Request) {}

func main() {
	mux := http.NewServeMux()
	admin := requireRole("admin")
	reader := requireRole("reader")
	reader = requireRole("writer")
	mux.Handle("GET /items", requireRole("admin")(http.HandlerFunc(listItems)))
	mux.Handle("GET /page", page("home")(http.HandlerFunc(listItems)))
	mux.Handle("GET /orders", admin(http.HandlerFunc(listOrders)))
	mux.Handle("GET /drafts", reader(http.HandlerFunc(listOrders)))
}
`

func TestRoutesPeelsMiddlewareThatAFunctionOfTheModuleMakes(t *testing.T) {
	sites := sitesOfSource(t, curriedMiddleware)

	type reading struct {
		anchor string
		roots  []string
	}
	got := map[string]reading{}
	for _, site := range sites {
		anchor := ""
		if site.HandlerID != nil {
			anchor = *site.HandlerID
		}
		got[site.Literals["path"]] = reading{anchor: anchor, roots: site.Roots}
	}
	want := map[string]reading{
		"GET /items":  {"example.com/depot.listItems", []string{"example.com/depot.listItems", "example.com/depot.requireRole"}},
		"GET /page":   {"example.com/depot.page", []string{"example.com/depot.listItems", "example.com/depot.page"}},
		"GET /orders": {"", []string{"example.com/depot.listOrders", "example.com/depot.requireRole"}},
		"GET /drafts": {"", []string{}},
	}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("got %v, want %v", got, want)
	}
}

func TestRoutesGivesTwoSpellingsOfOneHandlerOneIdentity(t *testing.T) {
	source := `package main

import "net/http"

func logged(next http.HandlerFunc) http.HandlerFunc { return next }

func paged(size int, next http.Handler) http.Handler { return next }

func main() {
	first, second := http.HandlerFunc(parcels), http.HandlerFunc(depots)
	http.HandleFunc("/bare", parcels)
	http.Handle("/converted", http.HandlerFunc(parcels))
	http.HandleFunc("/logged-parcels", logged(parcels))
	http.HandleFunc("/logged-depots", logged(depots))
	http.Handle("/five", paged(5, first))
	http.Handle("/nine", paged(9, first))
	http.Handle("/five-again", paged(2+3, first))
	http.Handle("/second", paged(5, second))
}
` + handlerFuncs
	identity := map[string]string{}
	for _, site := range sitesOfSource(t, source) {
		identity[site.Literals["path"]] = site.HandlerIdentity
	}

	same := [][2]string{{"/bare", "/converted"}, {"/five", "/five-again"}}
	different := [][2]string{{"/bare", "/logged-parcels"}, {"/logged-parcels", "/logged-depots"}, {"/five", "/nine"}, {"/five", "/second"}}
	for _, pair := range same {
		if identity[pair[0]] != identity[pair[1]] || identity[pair[0]] == "" {
			t.Errorf("%s and %s: want one identity, got %v", pair[0], pair[1], identity)
		}
	}
	for _, pair := range different {
		if identity[pair[0]] == identity[pair[1]] {
			t.Errorf("%s and %s: want two identities", pair[0], pair[1])
		}
	}
}

// The reviewer's g3: one method value registered from two methods, whose receivers are two
// variables of one type, is one handler.
func TestRoutesCountsAMethodValuesReceiverByItsType(t *testing.T) {
	source := `package main

import "net/http"

type server struct{ mux *http.ServeMux }

type mirror struct{ mux *http.ServeMux }

func (s *server) items(w http.ResponseWriter, r *http.Request)  {}
func (s *server) health(w http.ResponseWriter, r *http.Request) {}

func (m *mirror) items(w http.ResponseWriter, r *http.Request) {}

func (s *server) publicRoutes() {
	s.mux.HandleFunc("GET /items", s.items)
	s.mux.HandleFunc("GET /healthz", s.health)
}

func (s *server) v2Routes() {
	s.mux.HandleFunc("GET /v2/items", s.items)
}

func registerHealth(mux *http.ServeMux, s *server) {
	mux.HandleFunc("GET /readyz", s.health)
}

func main() {
	s := &server{mux: http.NewServeMux()}
	m := &mirror{mux: s.mux}
	s.publicRoutes()
	s.v2Routes()
	registerHealth(s.mux, s)
	s.mux.HandleFunc("GET /mirror", m.items)
}
`
	identity := map[string]string{}
	for _, site := range sitesOfSource(t, source) {
		identity[site.Literals["path"]] = site.HandlerIdentity
	}

	if identity["GET /items"] != identity["GET /v2/items"] || identity["GET /healthz"] != identity["GET /readyz"] {
		t.Errorf("want one identity for each method value, got %v", identity)
	}
	if identity["GET /items"] == identity["GET /healthz"] || identity["GET /items"] == identity["GET /mirror"] {
		t.Errorf("want two identities for two methods, got %v", identity)
	}
}

// Only a handler that is its anchor alone keeps the anchor beside a different handler that
// shares it, so the site says which it is.
func TestRoutesSaysWhetherAHandlerIsItsAnchorAlone(t *testing.T) {
	source := `package main

import "net/http"

type desk struct{}

func (d *desk) list(w http.ResponseWriter, r *http.Request) {}

func logged(next http.HandlerFunc) http.HandlerFunc { return next }

func paged(size int, next http.Handler) http.Handler { return next }

func timed(next http.Handler) http.Handler { return http.TimeoutHandler(next, 1, "slow") }

type apiFunc func(w http.ResponseWriter, r *http.Request) error

func (f apiFunc) ServeHTTP(w http.ResponseWriter, r *http.Request) { _ = f(w, r) }

func (d *desk) failing(w http.ResponseWriter, r *http.Request) error { return nil }

type items struct{}

func (items) ServeHTTP(w http.ResponseWriter, r *http.Request) {}

func main() {
	d := &desk{}
	http.HandleFunc("/bare", parcels)
	http.Handle("/converted", http.HandlerFunc(parcels))
	http.Handle("/twice-converted", (http.HandlerFunc)(http.HandlerFunc(parcels)))
	http.HandleFunc("/method-value", d.list)
	http.HandleFunc("/logged", logged(parcels))
	http.Handle("/paged", paged(5, http.HandlerFunc(parcels)))
	http.Handle("/timed-elsewhere", http.TimeoutHandler(http.HandlerFunc(parcels), 1, "slow"))
	http.Handle("/api", apiFunc(d.failing))
	http.Handle("/value", items{})
}
` + handlerFuncs
	alone := map[string]bool{}
	for _, site := range sitesOfSource(t, source) {
		alone[site.Literals["path"]] = site.HandlerIsTheAnchorAlone
	}

	want := map[string]bool{
		"/bare": true, "/converted": true, "/twice-converted": true, "/method-value": true,
		"/logged": false, "/paged": false, "/timed-elsewhere": false, "/api": false, "/value": false,
	}
	if !reflect.DeepEqual(alone, want) {
		t.Fatalf("got %v, want %v", alone, want)
	}
}

func TestRoutesSaysARouteThroughAWrapperIsNotItsAnchorAlone(t *testing.T) {
	root := writeModule(t, "example.com/depot", map[string]string{"main.go": aWrapperWithAFixedCompanion})

	for _, site := range routesOf(t, routeQuery{root: root, mainPackage: ".", patterns: getOnA}).Sites {
		if site.HandlerIsTheAnchorAlone {
			t.Errorf("%s: want a route shaped by the wrapper's code, got its anchor alone", site.Literals["path"])
		}
	}
}

// aMuxHandedTo hands a mux to a wrapper of the module whose body is given.
func aMuxHandedTo(wrapper string) string {
	return `package main

import "net/http"

type app struct{ mux *http.ServeMux }

func (a *app) register(path string, h http.Handler) { ` + wrapper + ` }

func passed(next http.Handler) http.Handler { return http.HandlerFunc(next.ServeHTTP) }

func listing(routes http.Handler) http.Handler { return http.NotFoundHandler() }

func main() {
	a := &app{mux: http.NewServeMux()}
	admin := http.NewServeMux()
	admin.HandleFunc("GET /admin/users", parcels)
	a.mux.HandleFunc("GET /items", parcels)
	a.register("/admin/", admin)
}
` + handlerFuncs
}

const registerOnA = `[{"kind":"route","call":"^a\\.register$","args":{"path":0,"handler":1}}]`

// A mux handed to a wrapper that registers it at its path is mounted there, as mux.Handle(path,
// mux) mounts it, and its own routes are listed where they are registered.
func TestRoutesReadsAMuxHandedToAWrapperThatRegistersItAtItsPathAsAMount(t *testing.T) {
	for _, wrapper := range []string{`a.mux.Handle(path, h)`, `a.mux.Handle(path, passed(h))`} {
		root := writeModule(t, "example.com/depot", map[string]string{"main.go": aMuxHandedTo(wrapper)})

		output := routesOf(t, routeQuery{root: root, mainPackage: ".", patterns: registerOnA})

		if got := sitePaths(output.Sites); !reflect.DeepEqual(got, []string{"GET /admin/users", "GET /items"}) || len(output.UnreadSites) != 0 {
			t.Errorf("%s: got %v and %+v", wrapper, got, output.UnreadSites)
		}
	}
}

func TestRoutesStopsAtAMuxHandedToAWrapperThatDoesMoreThanRegisterItAtItsPath(t *testing.T) {
	for _, wrapper := range []string{`a.mux.Handle("GET "+path, h)`, `a.mux.Handle(path, listing(h))`} {
		source := aMuxHandedTo(wrapper)

		got := unreadSitesOf(t, source, registerOnA)

		want := []unreadSiteOutput{{File: "main.go", Line: lineHolding(t, source, `a.register("/admin/"`), Problem: "mounts a mux"}}
		if !reflect.DeepEqual(got, want) {
			t.Errorf("%s: got %+v, want %+v", wrapper, got, want)
		}
	}
}

// aMethodOfItsOwnType registers through a wrapper whose method parameter has a named string type,
// with the method the call passes.
func aMethodOfItsOwnType(method string) string {
	return `package main

import "net/http"

type Method string

const Delete Method = "DELETE"

type gate struct{ mux *http.ServeMux }

func (g gate) post(m Method, path string, h http.HandlerFunc) {
	g.mux.HandleFunc(string(m)+" "+path, h)
}

func main() {
	gate{mux: http.NewServeMux()}.post(` + method + `, "/items", parcels)
}
` + handlerFuncs
}

func TestRoutesReadsAMethodOfANamedStringTypeConvertedAtTheSite(t *testing.T) {
	root := writeModule(t, "example.com/depot", map[string]string{"main.go": aMethodOfItsOwnType("Delete")})

	output := routesOf(t, routeQuery{root: root, mainPackage: ".", patterns: postWithAMethod})

	if got := sitePaths(output.Sites); !reflect.DeepEqual(got, []string{"DELETE /items"}) || len(output.UnreadSites) != 0 {
		t.Fatalf("got %v and %+v", got, output.UnreadSites)
	}
}

// An empty method makes the registered pattern start with a space, and a message about the path
// would not say why.
func TestRoutesStopsAtAnEmptyMethod(t *testing.T) {
	wrapped := aMethodOfItsOwnType(`""`)
	if got := unreadSitesOf(t, wrapped, postWithAMethod); !reflect.DeepEqual(got, []unreadSiteOutput{{File: "main.go", Line: lineHolding(t, wrapped, `post(""`), Problem: "method is empty"}}) {
		t.Errorf("a wrapper: got %+v", got)
	}

	source := "package main\n\nimport (\n\t\"net/http\"\n\n\t\"example.com/router\"\n)\n\nfunc main() {\n\tr := &router.Router{}\n\tr.Method(\"\", \"/x\", parcels)\n}\n" + handlerFuncs
	root := writeTree(t, map[string]string{
		"go.mod":           "module example.com/depot\n\ngo 1.22\n\nrequire example.com/router v0.0.0\n\nreplace example.com/router => ./router\n",
		"router/go.mod":    "module example.com/router\n\ngo 1.22\n",
		"router/router.go": "package router\n\nimport \"net/http\"\n\ntype Router struct{}\n\nfunc (*Router) Method(method, path string, h http.HandlerFunc) {}\n",
		"main.go":          source,
	})
	patterns := `[{"kind":"route","call":"^r\\.Method$","args":{"method":0,"path":1,"handler":2}}]`
	output := routesOf(t, routeQuery{root: root, mainPackage: ".", patterns: patterns})
	if want := []unreadSiteOutput{{File: "main.go", Line: 11, Problem: "method is empty"}}; !reflect.DeepEqual(output.UnreadSites, want) {
		t.Errorf("another module's router: got %+v", output.UnreadSites)
	}
}

// SSA writes a constant into the code of the function that holds the registration, which no route's
// hash covers, so the route's own hash takes the constants its handler holds.
func TestRoutesHashesTheConstantsAHandlerHolds(t *testing.T) {
	source := `package main

import "net/http"

func paged(size int, next http.Handler) http.Handler { return next }

func main() {
	first := http.HandlerFunc(parcels)
	http.HandleFunc("/bare", parcels)
	http.Handle("/converted", http.HandlerFunc(parcels))
	http.Handle("/five", paged(5, first))
	http.Handle("/nine", paged(9, first))
	http.Handle("/five-again", paged(2+3, first))
	http.HandleFunc("/inline", func(w http.ResponseWriter, r *http.Request) { w.WriteHeader(201) })
}
` + handlerFuncs
	constants := map[string]string{}
	for _, site := range sitesOfSource(t, source) {
		constants[site.Literals["path"]] = site.ConstantsHash
	}

	if constants["/bare"] != "" || constants["/converted"] != "" {
		t.Errorf("a handler with no constant must hash none, got %v", constants)
	}
	if constants["/inline"] != "" {
		t.Errorf("a route with no roots has no hash for its constants to join, got %v", constants)
	}
	if constants["/five"] == "" || constants["/five"] == constants["/nine"] || constants["/five"] != constants["/five-again"] {
		t.Errorf("got %v", constants)
	}
}

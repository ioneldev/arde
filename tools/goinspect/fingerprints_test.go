package main

import (
	"encoding/json"
	"os"
	"path/filepath"
	"reflect"
	"regexp"
	"slices"
	"sort"
	"strings"
	"testing"
)

const (
	routeListing = "(*storefront/internal/handlers.Handlers).listing"
	routeDetail  = "(*storefront/internal/handlers.Handlers).detail"
	routePing    = "(*storefront/internal/handlers.Handlers).ping"

	shopListing = "shop.listing"
	shopDetail  = "shop.detail"
	shopPing    = "shop.ping"

	pricingFile  = "internal/pricing/pricing.go"
	handlersFile = "internal/handlers/handlers.go"
)

type fingerprintOutput struct {
	Fingerprints map[string]string   `json:"fingerprints"`
	Reach        map[string][]string `json:"reach"`
	Unresolved   []string            `json:"unresolved"`
	WithoutBody  []string            `json:"without_body"`
}

func writeRoots(t *testing.T, roots ...string) string {
	t.Helper()
	content, err := json.Marshal(roots)
	assertNoError(t, err)
	path := filepath.Join(t.TempDir(), "roots.json")
	assertNoError(t, os.WriteFile(path, content, 0o644))
	return path
}

func fingerprintsOf(t *testing.T, root string, roots ...string) fingerprintOutput {
	t.Helper()
	return fingerprintsAt(t, fingerprintQuery{root: root, mainPackage: ".", roots: roots})
}

// storefrontFingerprints starts from the handlers package, because the module has no main.
func storefrontFingerprints(t *testing.T, root string, roots ...string) fingerprintOutput {
	t.Helper()
	return fingerprintsAt(t, fingerprintQuery{root: root, mainPackage: "./internal/handlers", roots: roots})
}

type fingerprintQuery struct {
	root        string
	mainPackage string
	roots       []string
}

func fingerprintsAt(t *testing.T, query fingerprintQuery) fingerprintOutput {
	t.Helper()
	exitCode, stdout, stderr := runCommand(t, "fingerprints", "--roots", writeRoots(t, query.roots...), "--main", query.mainPackage, query.root)
	if exitCode != 0 || stderr != "" {
		t.Fatalf("exit %d, stderr %q", exitCode, stderr)
	}
	var output fingerprintOutput
	if err := json.Unmarshal([]byte(stdout), &output); err != nil {
		t.Fatalf("stdout is not the fingerprint report: %v\n%s", err, stdout)
	}
	return output
}

// copyOfStorefront returns a fresh copy of testdata/storefront, a module of three routes that
// share one pricing package: listing and detail reach pricing.Convert, ping reaches nothing.
func copyOfStorefront(t *testing.T) string {
	t.Helper()
	root := t.TempDir()
	if err := os.CopyFS(root, os.DirFS("testdata/storefront")); err != nil {
		t.Fatal(err)
	}
	return root
}

type fileEdit struct {
	path string
	old  string
	new  string
}

// editFile fails unless the old text occurs exactly once, so an edit that matched nothing cannot
// make a "nothing changed" test pass for the wrong reason.
func editFile(t *testing.T, root string, edit fileEdit) {
	t.Helper()
	path := filepath.Join(root, filepath.FromSlash(edit.path))
	content, err := os.ReadFile(path)
	assertNoError(t, err)
	if strings.Count(string(content), edit.old) != 1 {
		t.Fatalf("%s must hold %q exactly once", edit.path, edit.old)
	}
	edited := strings.Replace(string(content), edit.old, edit.new, 1)
	assertNoError(t, os.WriteFile(path, []byte(edited), 0o644))
}

func writeModule(t *testing.T, modulePath string, files map[string]string) string {
	t.Helper()
	withGoMod := map[string]string{"go.mod": "module " + modulePath + "\n\ngo 1.22\n"}
	for path, content := range files {
		withGoMod[path] = content
	}
	return writeTree(t, withGoMod)
}

func changedRoots(before, after fingerprintOutput) []string {
	changed := []string{}
	for root, hash := range before.Fingerprints {
		if after.Fingerprints[root] != hash {
			changed = append(changed, root)
		}
	}
	sort.Strings(changed)
	return changed
}

func TestFingerprintChangesWhenCalleeChanges(t *testing.T) {
	root := copyOfStorefront(t)
	before := storefrontFingerprints(t, root, routeListing, routeDetail, routePing)

	editFile(t, root, fileEdit{path: pricingFile, old: "+0.5", new: "+0.4"})
	after := storefrontFingerprints(t, root, routeListing, routeDetail, routePing)

	if got := changedRoots(before, after); !reflect.DeepEqual(got, []string{routeDetail, routeListing}) {
		t.Fatalf("changed %v, want listing and detail and not ping", got)
	}
}

func TestFingerprintChangesWhenTheRouteItselfChanges(t *testing.T) {
	root := copyOfStorefront(t)
	before := storefrontFingerprints(t, root, routeListing, routeDetail, routePing)

	editFile(t, root, fileEdit{path: handlersFile, old: `return "ok"`, new: `return "fine"`})
	after := storefrontFingerprints(t, root, routeListing, routeDetail, routePing)

	if got := changedRoots(before, after); !reflect.DeepEqual(got, []string{routePing}) {
		t.Fatalf("changed %v, want only ping", got)
	}
}

func TestFingerprintStableUnderComments(t *testing.T) {
	root := copyOfStorefront(t)
	before := storefrontFingerprints(t, root, routeListing, routeDetail, routePing)

	editFile(t, root, fileEdit{
		path: handlersFile,
		old:  "func (h *Handlers) listing() float64 {",
		new:  "// listing adds two totals.\n\n\nfunc (h *Handlers) listing() float64 { // trailing\n\t/* inside */",
	})
	editFile(t, root, fileEdit{path: pricingFile, old: "return round(amount * rate)", new: "return round(amount * rate) // rounded"})
	after := storefrontFingerprints(t, root, routeListing, routeDetail, routePing)

	if got := changedRoots(before, after); len(got) != 0 {
		t.Fatalf("a comment moved %v", got)
	}
}

func TestFingerprintStableUnderWindowsLineEndings(t *testing.T) {
	root := copyOfStorefront(t)
	before := storefrontFingerprints(t, root, routeListing, routeDetail, routePing)

	for _, file := range []string{pricingFile, handlersFile} {
		path := filepath.Join(root, filepath.FromSlash(file))
		content, err := os.ReadFile(path)
		assertNoError(t, err)
		assertNoError(t, os.WriteFile(path, []byte(strings.ReplaceAll(string(content), "\n", "\r\n")), 0o644))
	}
	after := storefrontFingerprints(t, root, routeListing, routeDetail, routePing)

	if got := changedRoots(before, after); len(got) != 0 {
		t.Fatalf("line endings moved %v", got)
	}
}

func TestFingerprintStableWhenCodeMovesDownAFile(t *testing.T) {
	root := copyOfStorefront(t)
	before := storefrontFingerprints(t, root, routeListing, routeDetail, routePing)

	editFile(t, root, fileEdit{
		path: handlersFile,
		old:  "type Handlers struct {",
		new:  "func unrelated() int {\n\n\n\treturn 7\n}\n\ntype Handlers struct {",
	})
	after := storefrontFingerprints(t, root, routeListing, routeDetail, routePing)

	if got := changedRoots(before, after); len(got) != 0 {
		t.Fatalf("moving code down the file moved %v", got)
	}
}

func TestFingerprintReadsModuleFromGoMod(t *testing.T) {
	root := copyOfStorefront(t)
	editFile(t, root, fileEdit{path: "go.mod", old: "module storefront", new: "module acme.example/orders"})
	editFile(t, root, fileEdit{path: handlersFile, old: `"storefront/internal/pricing"`, new: `"acme.example/orders/internal/pricing"`})

	const movedDetail = "(*acme.example/orders/internal/handlers.Handlers).detail"

	output := storefrontFingerprints(t, root, movedDetail)

	want := []string{
		movedDetail,
		"acme.example/orders/internal/pricing.Convert",
		"acme.example/orders/internal/pricing.round",
	}
	if !reflect.DeepEqual(output.Reach[movedDetail], want) {
		t.Fatalf("reach %v, want %v", output.Reach[movedDetail], want)
	}
}

func TestFingerprintOnlyCountsPackagesInsideTheModule(t *testing.T) {
	root := writeTree(t, map[string]string{
		"app/go.mod":          "module shop\n\ngo 1.22\n\nrequire shopfront v0.0.0\n\nreplace shopfront => ../shopfront\n",
		"app/shop.go":         "package shop\n\nimport \"shopfront/x\"\n\nfunc detail() int { return x.Helper() }\n",
		"shopfront/go.mod":    "module shopfront\n\ngo 1.22\n",
		"shopfront/x/help.go": "package x\n\nfunc Helper() int { return 1 }\n",
	})

	output := fingerprintsOf(t, filepath.Join(root, "app"), shopDetail)

	// "shopfront/x" starts with "shop" but is another module.
	if want := []string{"shop.detail"}; !reflect.DeepEqual(output.Reach[shopDetail], want) {
		t.Fatalf("reach %v, want %v", output.Reach[shopDetail], want)
	}
}

func TestFingerprintFollowsAFunctionNamedInit(t *testing.T) {
	source := "package shop\n\nfunc detail() string { return initDB() }\n\nfunc initDB() string { return \"a\" }\n"
	before := fingerprintsOf(t, writeModule(t, "shop", map[string]string{"shop.go": source}), shopDetail)

	editedRoot := writeModule(t, "shop", map[string]string{"shop.go": strings.Replace(source, `"a"`, `"b"`, 1)})
	after := fingerprintsOf(t, editedRoot, shopDetail)

	if want := []string{"shop.detail", "shop.initDB"}; !reflect.DeepEqual(before.Reach[shopDetail], want) {
		t.Errorf("reach %v, want %v", before.Reach[shopDetail], want)
	}
	if before.Fingerprints[shopDetail] == after.Fingerprints[shopDetail] {
		t.Error("editing initDB left the fingerprint of the handler that calls it unchanged")
	}
}

func TestFingerprintChangesWhenAClosureBodyChanges(t *testing.T) {
	cases := map[string]struct {
		source string
		edit   [2]string
	}{
		"a closure handed to sort.Slice": {
			source: "package shop\n\nimport \"sort\"\n\nfunc listing(xs []int) []int {\n\tsort.Slice(xs, func(i, j int) bool { return xs[i] < xs[j] })\n\treturn xs\n}\n",
			edit:   [2]string{"xs[i] < xs[j]", "xs[i] > xs[j]"},
		},
		"a closure inside a closure that is called at once": {
			source: "package shop\n\nimport \"sort\"\n\nfunc listing(xs []int) []int {\n\tfunc() {\n\t\tsort.Slice(xs, func(i, j int) bool { return xs[i] < xs[j] })\n\t}()\n\treturn xs\n}\n",
			edit:   [2]string{"xs[i] < xs[j]", "xs[i] > xs[j]"},
		},
		"a closure returned as the handler": {
			source: "package shop\n\nimport \"sort\"\n\nfunc listing(xs []int) func() {\n\treturn func() {\n\t\tsort.Slice(xs, func(i, j int) bool { return xs[i] < xs[j] })\n\t}\n}\n",
			edit:   [2]string{"xs[i] < xs[j]", "xs[i] > xs[j]"},
		},
		"a function that a closure handed to sort.Slice calls": {
			source: "package shop\n\nimport \"sort\"\n\nfunc listing(xs []int) []int {\n\tsort.Slice(xs, func(i, j int) bool { return before(xs[i], xs[j]) })\n\treturn xs\n}\n\nfunc before(a, b int) bool { return a < b }\n",
			edit:   [2]string{"return a < b", "return a > b"},
		},
	}

	for name, test := range cases {
		before := fingerprintsOf(t, writeModule(t, "shop", map[string]string{"shop.go": test.source}), shopListing)
		edited := strings.Replace(test.source, test.edit[0], test.edit[1], 1)
		if edited == test.source {
			t.Fatalf("%s: the edit matched nothing", name)
		}
		after := fingerprintsOf(t, writeModule(t, "shop", map[string]string{"shop.go": edited}), shopListing)

		if before.Fingerprints[shopListing] == after.Fingerprints[shopListing] {
			t.Errorf("%s: the edit left the fingerprint unchanged", name)
		}
	}
}

func TestFingerprintFollowsCallsThatGoThroughAGeneratedWrapper(t *testing.T) {
	cases := map[string]struct {
		source   string
		edit     [2]string
		wantName string
	}{
		"a generic function": {
			source: "package shop\n\nfunc detail() int {\n\t_, n := Pick[string](\"a\")\n\treturn n\n}\n\nfunc Pick[T any](x T) (T, int) { return x, 1 }\n",
			edit:   [2]string{"return x, 1", "return x, 2"}, wantName: "shop.Pick",
		},
		"a method of a generic type": {
			source: "package shop\n\ntype box[T any] struct{ v T }\n\nfunc (b box[T]) size() int { return 1 }\n\nfunc detail() int { return box[string]{\"a\"}.size() }\n",
			edit:   [2]string{"size() int { return 1 }", "size() int { return 2 }"}, wantName: "(shop.box[T]).size",
		},
		"a method expression": {
			source: "package shop\n\ntype store struct{}\n\nfunc (store) size() int { return 1 }\n\nfunc detail() int { return store.size(store{}) }\n",
			edit:   [2]string{"size() int { return 1 }", "size() int { return 2 }"}, wantName: "(shop.store).size",
		},
		"a method value called later": {
			source: "package shop\n\ntype store struct{}\n\nfunc (store) size() int { return 1 }\n\nfunc detail() int {\n\tsize := store{}.size\n\treturn size()\n}\n",
			edit:   [2]string{"size() int { return 1 }", "size() int { return 2 }"}, wantName: "(shop.store).size",
		},
		"a method promoted from an embedded type": {
			source: "package shop\n\ntype base struct{}\n\nfunc (base) size() int { return 1 }\n\ntype store struct{ base }\n\nfunc detail() int {\n\tsize := store{}.size\n\treturn size()\n}\n",
			edit:   [2]string{"size() int { return 1 }", "size() int { return 2 }"}, wantName: "(shop.base).size",
		},
	}

	for name, test := range cases {
		before := fingerprintsOf(t, writeModule(t, "shop", map[string]string{"shop.go": test.source}), shopDetail)
		edited := strings.Replace(test.source, test.edit[0], test.edit[1], 1)
		if edited == test.source {
			t.Fatalf("%s: the edit matched nothing", name)
		}
		after := fingerprintsOf(t, writeModule(t, "shop", map[string]string{"shop.go": edited}), shopDetail)

		if before.Fingerprints[shopDetail] == after.Fingerprints[shopDetail] {
			t.Errorf("%s: editing the callee left the fingerprint unchanged", name)
		}
		if !slices.Contains(before.Reach[shopDetail], test.wantName) {
			t.Errorf("%s: reach %v lacks %s", name, before.Reach[shopDetail], test.wantName)
		}
		for _, reached := range before.Reach[shopDetail] {
			if strings.Contains(reached, "$bound") || strings.Contains(reached, "$thunk") || strings.Contains(reached, "[string]") {
				t.Errorf("%s: reach lists the generated wrapper %s", name, reached)
			}
		}
	}
}

func TestFingerprintReachIsSortedAndHoldsTheRouteItself(t *testing.T) {
	output := storefrontFingerprints(t, copyOfStorefront(t), routeListing, routePing)

	want := map[string][]string{
		routeListing: {
			"(*storefront/internal/handlers.Handlers).listing",
			"(*storefront/internal/handlers.Handlers).total",
			"storefront/internal/pricing.Convert",
			"storefront/internal/pricing.round",
		},
		routePing: {"(*storefront/internal/handlers.Handlers).ping"},
	}
	if !reflect.DeepEqual(output.Reach, want) {
		t.Fatalf("reach %v, want %v", output.Reach, want)
	}
}

func TestFingerprintPrintsAFullSHA256AndAnEmptyUnresolvedList(t *testing.T) {
	root := copyOfStorefront(t)
	rootsFile := writeRoots(t, routePing, routeDetail)

	exitCode, stdout, stderr := runCommand(t, "fingerprints", "--roots", rootsFile, "--main", "./internal/handlers", root)

	if exitCode != 0 || stderr != "" {
		t.Fatalf("exit %d, stderr %q", exitCode, stderr)
	}
	decoded := decodeObject(t, stdout)
	for _, key := range []string{"fingerprints", "reach", "unresolved", "without_body"} {
		if _, found := decoded[key]; !found {
			t.Errorf("output lacks %q: %s", key, stdout)
		}
	}
	if !strings.Contains(stdout, `"unresolved":[],"without_body":[]`) {
		t.Errorf("both lists must print as [], got %s", stdout)
	}
	sha256Hex := regexp.MustCompile(`^[0-9a-f]{64}$`)
	for root, hash := range decoded["fingerprints"].(map[string]any) {
		if !sha256Hex.MatchString(hash.(string)) {
			t.Errorf("%s: %q is not a full SHA-256 in hex", root, hash)
		}
	}
}

func TestFingerprintListsARootThatMatchesNothingAsUnresolved(t *testing.T) {
	source := "package shop\n\nimport \"strings\"\n\nfunc detail() string { return strings.ToUpper(\"a\") }\n"
	root := writeModule(t, "shop", map[string]string{"shop.go": source})

	// A bare name names nothing any more, ToUpper is outside the module, and init exists only as
	// the compiler's package initialiser.
	output := fingerprintsOf(t, root, shopDetail, "detail", "shop.missing", "strings.ToUpper", "shop.init")

	want := []string{"detail", "shop.init", "shop.missing", "strings.ToUpper"}
	if !reflect.DeepEqual(output.Unresolved, want) {
		t.Errorf("unresolved %v, want %v", output.Unresolved, want)
	}
	if len(output.Fingerprints) != 1 || output.Fingerprints[shopDetail] == "" {
		t.Errorf("only detail resolves, got %v", output.Fingerprints)
	}
	if _, listed := output.Reach["shop.missing"]; listed {
		t.Error("an unresolved root must not appear in reach")
	}
}

func TestFingerprintStartsFromTheOneFunctionItsIdNames(t *testing.T) {
	source := "package shop\n\ntype cart struct{}\n\ntype order struct{}\n\nfunc (cart) size() int { return 1 }\n\nfunc (order) size() int { return 1 }\n"
	const cartSize, orderSize = "(shop.cart).size", "(shop.order).size"
	before := fingerprintsOf(t, writeModule(t, "shop", map[string]string{"shop.go": source}), cartSize, orderSize)
	editedOrder := strings.Replace(source, "func (order) size() int { return 1 }", "func (order) size() int { return 2 }", 1)
	after := fingerprintsOf(t, writeModule(t, "shop", map[string]string{"shop.go": editedOrder}), cartSize, orderSize)

	if want := []string{cartSize}; !reflect.DeepEqual(before.Reach[cartSize], want) {
		t.Errorf("reach %v, want %v", before.Reach[cartSize], want)
	}
	if got, want := changedRoots(before, after), []string{orderSize}; !reflect.DeepEqual(got, want) {
		t.Errorf("changed %v, want %v", got, want)
	}
}

func TestFingerprintReadsAnIdThatHoldsACommaAndASpace(t *testing.T) {
	source := "package shop\n\ntype pair[K comparable, V any] struct{}\n\nfunc (p *pair[K, V]) detail() int { return 1 }\n\nvar _ = (&pair[string, int]{}).detail()\n"
	const id = "(*shop.pair[K, V]).detail"

	output := fingerprintsOf(t, writeModule(t, "shop", map[string]string{"shop.go": source}), id)

	if output.Fingerprints[id] == "" {
		t.Fatalf("fingerprints %v, unresolved %v", output.Fingerprints, output.Unresolved)
	}
}

func TestFingerprintsAgreeWithTheIdsRoutesPrints(t *testing.T) {
	source := `package main

import "net/http"

type pair[K comparable, V any] struct{}

func (p *pair[K, V]) serve(w http.ResponseWriter, r *http.Request) {}

type desk struct{}

func (d desk) list(w http.ResponseWriter, r *http.Request) {}

func parcels(w http.ResponseWriter, r *http.Request) {}

func fallingBackOn(fallback http.HandlerFunc) http.HandlerFunc { return fallback }

func main() {
	p := &pair[string, int]{}
	http.HandleFunc("/generic", p.serve)
	http.HandleFunc("/value", desk{}.list)
	http.HandleFunc("/plain", parcels)
	http.HandleFunc("/made", fallingBackOn(parcels))
}
`
	root := writeModule(t, "example.com/depot", map[string]string{"main.go": source})
	ids := []string{}
	for _, site := range routesOf(t, routeQuery{root: root, mainPackage: ".", patterns: noPatterns}).Sites {
		ids = append(ids, site.Roots...)
	}
	slices.Sort(ids)
	ids = slices.Compact(ids)

	output := fingerprintsOf(t, root, ids...)

	if len(ids) != 4 || len(output.Fingerprints) != 4 || len(output.Unresolved) != 0 {
		t.Fatalf("ids %v, fingerprints %v, unresolved %v", ids, output.Fingerprints, output.Unresolved)
	}
}

func twoMainsSharingAGeneric(secondMain string) map[string]string {
	return map[string]string{
		"cmd/api/main.go":   "package main\n\nimport \"shop/lib\"\n\nfunc main() { lib.Detail(1) }\n",
		"cmd/admin/main.go": secondMain,
		"lib/lib.go":        "package lib\n\nfunc Detail[T any](x T) T { return x }\n",
		"unused/unused.go":  "package unused\n\nfunc Unused() {}\n",
	}
}

func TestFingerprintIsNotMovedByAnInstanceOutsideTheService(t *testing.T) {
	const id = "shop/lib.Detail"
	first := twoMainsSharingAGeneric("package main\n\nfunc main() {}\n")
	second := twoMainsSharingAGeneric("package main\n\nimport \"shop/lib\"\n\ntype other struct{}\n\nfunc main() { lib.Detail(other{}) }\n")

	before := fingerprintsAt(t, fingerprintQuery{root: writeModule(t, "shop", first), mainPackage: "./cmd/api", roots: []string{id}})
	after := fingerprintsAt(t, fingerprintQuery{root: writeModule(t, "shop", second), mainPackage: "./cmd/api", roots: []string{id}})

	if before.Fingerprints[id] == "" || before.Fingerprints[id] != after.Fingerprints[id] {
		t.Fatalf("before %v, after %v", before.Fingerprints, after.Fingerprints)
	}
}

func TestFingerprintDoesNotFindAFunctionOutsideTheService(t *testing.T) {
	root := writeModule(t, "shop", twoMainsSharingAGeneric("package main\n\nfunc main() {}\n"))

	output := fingerprintsAt(t, fingerprintQuery{root: root, mainPackage: "./cmd/api", roots: []string{"shop/unused.Unused"}})

	if want := []string{"shop/unused.Unused"}; !reflect.DeepEqual(output.Unresolved, want) {
		t.Fatalf("unresolved %v, want %v", output.Unresolved, want)
	}
}

func TestFingerprintsAreNotStoppedByABrokenPackageOutsideTheService(t *testing.T) {
	files := map[string]string{
		"shop.go":       "package shop\n\nfunc detail() int { return 1 }\n",
		"other/oops.go": "package other\n\nvar broken int = \"text\"\n",
	}

	output := fingerprintsOf(t, writeModule(t, "shop", files), shopDetail)

	if output.Fingerprints[shopDetail] == "" {
		t.Fatalf("got %+v", output)
	}
}

func TestFingerprintsFailWhenAPackageDoesNotLoad(t *testing.T) {
	cases := map[string]struct {
		files       map[string]string
		mustMention string
	}{
		"a type error in a package": {
			files:       map[string]string{"shop.go": "package shop\n\nfunc detail() int { return missingName }\n"},
			mustMention: "missingName",
		},
		"a syntax error in a package": {
			files:       map[string]string{"shop.go": "package shop\n\nfunc detail( {\n"},
			mustMention: "shop.go",
		},
		"an import nothing provides": {
			files:       map[string]string{"shop.go": "package shop\n\nimport _ \"example.invalid/nothing\"\n\nfunc detail() int { return 1 }\n"},
			mustMention: "example.invalid/nothing",
		},
		"a type error in a package the service imports and the route does not use": {
			files: map[string]string{
				"shop.go":       "package shop\n\nimport _ \"shop/other\"\n\nfunc detail() int { return 1 }\n",
				"other/oops.go": "package other\n\nvar broken int = \"text\"\n",
			},
			mustMention: "oops.go",
		},
	}

	for name, test := range cases {
		root := writeModule(t, "shop", test.files)

		exitCode, stdout, stderr := runCommand(t, "fingerprints", "--roots", writeRoots(t, shopDetail), "--main", ".", root)

		if exitCode != 1 || stdout != "" {
			t.Errorf("%s: exit %d, stdout %q", name, exitCode, stdout)
		}
		if !strings.Contains(stderr, test.mustMention) {
			t.Errorf("%s: stderr must mention %q, got %q", name, test.mustMention, stderr)
		}
	}
}

func TestFingerprintsFailWithoutAGoMod(t *testing.T) {
	root := writeTree(t, map[string]string{"shop.go": "package shop\n\nfunc detail() int { return 1 }\n"})

	exitCode, stdout, stderr := runCommand(t, "fingerprints", "--roots", writeRoots(t, shopDetail), "--main", ".", root)

	if exitCode != 1 || stdout != "" {
		t.Fatalf("exit %d, stdout %q", exitCode, stdout)
	}
	if !strings.Contains(stderr, "go.mod") {
		t.Fatalf("stderr must name go.mod, got %q", stderr)
	}
}

func TestFingerprintsFailWhenTheModuleHoldsNoPackage(t *testing.T) {
	root := writeModule(t, "shop", map[string]string{"README.md": "no go here"})

	exitCode, stdout, stderr := runCommand(t, "fingerprints", "--roots", writeRoots(t, shopDetail), "--main", ".", root)

	if exitCode != exitProfileProblem || stdout != "" || !strings.Contains(stderr, "main_package") {
		t.Fatalf("exit %d, stdout %q, stderr %q", exitCode, stdout, stderr)
	}
}

func TestFingerprintsRejectBadArgumentsBeforeLoadingAnything(t *testing.T) {
	root := copyOfStorefront(t)
	const main = "./internal/handlers"
	roots := writeRoots(t, routeListing)
	badRoots := filepath.Join(t.TempDir(), "bad.json")
	assertNoError(t, os.WriteFile(badRoots, []byte("a,b"), 0o644))
	nullRoots := filepath.Join(t.TempDir(), "null.json")
	assertNoError(t, os.WriteFile(nullRoots, []byte("null"), 0o644))
	cases := map[string][]string{
		"no --roots":                {"fingerprints", "--main", main, root},
		"no --main":                 {"fingerprints", "--roots", roots, root},
		"a roots file that is gone": {"fingerprints", "--roots", root + "/nope", "--main", main, root},
		"a roots file of names":     {"fingerprints", "--roots", badRoots, "--main", main, root},
		"a roots file holding null": {"fingerprints", "--roots", nullRoots, "--main", main, root},
		"no directory":              {"fingerprints", "--roots", roots, "--main", main},
		"two directories":           {"fingerprints", "--roots", roots, "--main", main, root, root},
		"an empty directory":        {"fingerprints", "--roots", roots, "--main", main, ""},
		"a missing directory":       {"fingerprints", "--roots", roots, "--main", main, root + "/nope"},
		"the flag after the root":   {"fingerprints", root, "--roots", roots, "--main", main},
		"a directory that is file":  {"fingerprints", "--roots", roots, "--main", main, filepath.Join(root, "go.mod")},
	}

	for name, args := range cases {
		exitCode, stdout, stderr := runCommand(t, args...)

		if exitCode != 1 || stdout != "" || strings.TrimSpace(stderr) == "" {
			t.Errorf("%s: exit %d, stdout %q, stderr %q", name, exitCode, stdout, stderr)
		}
	}
}

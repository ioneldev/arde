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
	routeListing = "listing"
	routeDetail  = "detail"
	routePing    = "ping"

	pricingFile  = "internal/pricing/pricing.go"
	handlersFile = "internal/handlers/handlers.go"
)

type fingerprintOutput struct {
	Fingerprints map[string]string   `json:"fingerprints"`
	Reach        map[string][]string `json:"reach"`
	Unresolved   []string            `json:"unresolved"`
}

func fingerprintsOf(t *testing.T, root string, roots ...string) fingerprintOutput {
	t.Helper()
	exitCode, stdout, stderr := runCommand(t, "fingerprints", "--roots", strings.Join(roots, ","), root)
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

// editFile fails unless the old text occurs exactly once, so an edit that matched nothing cannot
// make a "nothing changed" test pass for the wrong reason.
func editFile(t *testing.T, root, relativePath, old, replacement string) {
	t.Helper()
	path := filepath.Join(root, filepath.FromSlash(relativePath))
	content, err := os.ReadFile(path)
	assertNoError(t, err)
	if strings.Count(string(content), old) != 1 {
		t.Fatalf("%s must hold %q exactly once", relativePath, old)
	}
	edited := strings.Replace(string(content), old, replacement, 1)
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
	before := fingerprintsOf(t, root, routeListing, routeDetail, routePing)

	editFile(t, root, pricingFile, "+0.5", "+0.4")
	after := fingerprintsOf(t, root, routeListing, routeDetail, routePing)

	if got := changedRoots(before, after); !reflect.DeepEqual(got, []string{routeDetail, routeListing}) {
		t.Fatalf("changed %v, want listing and detail and not ping", got)
	}
}

func TestFingerprintChangesWhenTheRouteItselfChanges(t *testing.T) {
	root := copyOfStorefront(t)
	before := fingerprintsOf(t, root, routeListing, routeDetail, routePing)

	editFile(t, root, handlersFile, `return "ok"`, `return "fine"`)
	after := fingerprintsOf(t, root, routeListing, routeDetail, routePing)

	if got := changedRoots(before, after); !reflect.DeepEqual(got, []string{routePing}) {
		t.Fatalf("changed %v, want only ping", got)
	}
}

func TestFingerprintStableUnderComments(t *testing.T) {
	root := copyOfStorefront(t)
	before := fingerprintsOf(t, root, routeListing, routeDetail, routePing)

	editFile(t, root, handlersFile, "func (h *Handlers) listing() float64 {",
		"// listing adds two totals.\n\n\nfunc (h *Handlers) listing() float64 { // trailing\n\t/* inside */")
	editFile(t, root, pricingFile, "return round(amount * rate)", "return round(amount * rate) // rounded")
	after := fingerprintsOf(t, root, routeListing, routeDetail, routePing)

	if got := changedRoots(before, after); len(got) != 0 {
		t.Fatalf("a comment moved %v", got)
	}
}

func TestFingerprintStableUnderWindowsLineEndings(t *testing.T) {
	root := copyOfStorefront(t)
	before := fingerprintsOf(t, root, routeListing, routeDetail, routePing)

	for _, file := range []string{pricingFile, handlersFile} {
		path := filepath.Join(root, filepath.FromSlash(file))
		content, err := os.ReadFile(path)
		assertNoError(t, err)
		assertNoError(t, os.WriteFile(path, []byte(strings.ReplaceAll(string(content), "\n", "\r\n")), 0o644))
	}
	after := fingerprintsOf(t, root, routeListing, routeDetail, routePing)

	if got := changedRoots(before, after); len(got) != 0 {
		t.Fatalf("line endings moved %v", got)
	}
}

func TestFingerprintStableWhenCodeMovesDownAFile(t *testing.T) {
	root := copyOfStorefront(t)
	before := fingerprintsOf(t, root, routeListing, routeDetail, routePing)

	editFile(t, root, handlersFile, "type Handlers struct {",
		"func unrelated() int {\n\n\n\treturn 7\n}\n\ntype Handlers struct {")
	after := fingerprintsOf(t, root, routeListing, routeDetail, routePing)

	if got := changedRoots(before, after); len(got) != 0 {
		t.Fatalf("moving code down the file moved %v", got)
	}
}

func TestFingerprintReadsModuleFromGoMod(t *testing.T) {
	root := copyOfStorefront(t)
	editFile(t, root, "go.mod", "module storefront", "module acme.example/orders")
	editFile(t, root, handlersFile, `"storefront/internal/pricing"`, `"acme.example/orders/internal/pricing"`)

	output := fingerprintsOf(t, root, routeDetail)

	want := []string{
		"(*acme.example/orders/internal/handlers.Handlers).detail",
		"acme.example/orders/internal/pricing.Convert",
		"acme.example/orders/internal/pricing.round",
	}
	if !reflect.DeepEqual(output.Reach[routeDetail], want) {
		t.Fatalf("reach %v, want %v", output.Reach[routeDetail], want)
	}
}

func TestFingerprintOnlyCountsPackagesInsideTheModule(t *testing.T) {
	root := writeTree(t, map[string]string{
		"app/go.mod":          "module shop\n\ngo 1.22\n\nrequire shopfront v0.0.0\n\nreplace shopfront => ../shopfront\n",
		"app/shop.go":         "package shop\n\nimport \"shopfront/x\"\n\nfunc detail() int { return x.Helper() }\n",
		"shopfront/go.mod":    "module shopfront\n\ngo 1.22\n",
		"shopfront/x/help.go": "package x\n\nfunc Helper() int { return 1 }\n",
	})

	output := fingerprintsOf(t, filepath.Join(root, "app"), routeDetail)

	// "shopfront/x" starts with "shop" but is another module.
	if want := []string{"shop.detail"}; !reflect.DeepEqual(output.Reach[routeDetail], want) {
		t.Fatalf("reach %v, want %v", output.Reach[routeDetail], want)
	}
}

func TestFingerprintFollowsAFunctionNamedInit(t *testing.T) {
	source := "package shop\n\nfunc detail() string { return initDB() }\n\nfunc initDB() string { return \"a\" }\n"
	before := fingerprintsOf(t, writeModule(t, "shop", map[string]string{"shop.go": source}), routeDetail)

	editedRoot := writeModule(t, "shop", map[string]string{"shop.go": strings.Replace(source, `"a"`, `"b"`, 1)})
	after := fingerprintsOf(t, editedRoot, routeDetail)

	if want := []string{"shop.detail", "shop.initDB"}; !reflect.DeepEqual(before.Reach[routeDetail], want) {
		t.Errorf("reach %v, want %v", before.Reach[routeDetail], want)
	}
	if before.Fingerprints[routeDetail] == after.Fingerprints[routeDetail] {
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
		before := fingerprintsOf(t, writeModule(t, "shop", map[string]string{"shop.go": test.source}), routeListing)
		edited := strings.Replace(test.source, test.edit[0], test.edit[1], 1)
		if edited == test.source {
			t.Fatalf("%s: the edit matched nothing", name)
		}
		after := fingerprintsOf(t, writeModule(t, "shop", map[string]string{"shop.go": edited}), routeListing)

		if before.Fingerprints[routeListing] == after.Fingerprints[routeListing] {
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
		before := fingerprintsOf(t, writeModule(t, "shop", map[string]string{"shop.go": test.source}), routeDetail)
		edited := strings.Replace(test.source, test.edit[0], test.edit[1], 1)
		if edited == test.source {
			t.Fatalf("%s: the edit matched nothing", name)
		}
		after := fingerprintsOf(t, writeModule(t, "shop", map[string]string{"shop.go": edited}), routeDetail)

		if before.Fingerprints[routeDetail] == after.Fingerprints[routeDetail] {
			t.Errorf("%s: editing the callee left the fingerprint unchanged", name)
		}
		if !slices.Contains(before.Reach[routeDetail], test.wantName) {
			t.Errorf("%s: reach %v lacks %s", name, before.Reach[routeDetail], test.wantName)
		}
		for _, reached := range before.Reach[routeDetail] {
			if strings.Contains(reached, "$bound") || strings.Contains(reached, "$thunk") || strings.Contains(reached, "[string]") {
				t.Errorf("%s: reach lists the generated wrapper %s", name, reached)
			}
		}
	}
}

func TestFingerprintReachIsSortedAndHoldsTheRouteItself(t *testing.T) {
	output := fingerprintsOf(t, copyOfStorefront(t), routeListing, routePing)

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

	exitCode, stdout, stderr := runCommand(t, "fingerprints", "--roots", "ping,detail", root)

	if exitCode != 0 || stderr != "" {
		t.Fatalf("exit %d, stderr %q", exitCode, stderr)
	}
	decoded := decodeObject(t, stdout)
	for _, key := range []string{"fingerprints", "reach", "unresolved"} {
		if _, found := decoded[key]; !found {
			t.Errorf("output lacks %q: %s", key, stdout)
		}
	}
	if !strings.Contains(stdout, `"unresolved":[]`) {
		t.Errorf("unresolved must print as [], got %s", stdout)
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

	// ToUpper is outside the module, and init exists only as the compiler's package initialiser.
	output := fingerprintsOf(t, root, routeDetail, "missing", "ToUpper", "init")

	if want := []string{"ToUpper", "init", "missing"}; !reflect.DeepEqual(output.Unresolved, want) {
		t.Errorf("unresolved %v, want %v", output.Unresolved, want)
	}
	if len(output.Fingerprints) != 1 || output.Fingerprints[routeDetail] == "" {
		t.Errorf("only detail resolves, got %v", output.Fingerprints)
	}
	if _, listed := output.Reach["missing"]; listed {
		t.Error("an unresolved root must not appear in reach")
	}
}

func TestFingerprintOfARootMatchingSeveralFunctionsUnionsTheirReach(t *testing.T) {
	source := "package shop\n\ntype cart struct{}\n\ntype order struct{}\n\ntype crate[T any] struct{}\n\nfunc (cart) size() int { return 1 }\n\nfunc (order) size() int { return 1 }\n\nfunc (crate[T]) size() int { return 1 }\n"
	before := fingerprintsOf(t, writeModule(t, "shop", map[string]string{"shop.go": source}), "size")
	editedOrder := strings.Replace(source, "func (order) size() int { return 1 }", "func (order) size() int { return 2 }", 1)
	after := fingerprintsOf(t, writeModule(t, "shop", map[string]string{"shop.go": editedOrder}), "size")

	if want := []string{"(shop.cart).size", "(shop.crate[T]).size", "(shop.order).size"}; !reflect.DeepEqual(before.Reach["size"], want) {
		t.Errorf("reach %v, want %v", before.Reach["size"], want)
	}
	if before.Fingerprints["size"] == after.Fingerprints["size"] {
		t.Error("editing one of the two methods left the shared root's fingerprint unchanged")
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
		"a type error in a package the route does not use": {
			files: map[string]string{
				"shop.go":       "package shop\n\nfunc detail() int { return 1 }\n",
				"other/oops.go": "package other\n\nvar broken int = \"text\"\n",
			},
			mustMention: "oops.go",
		},
	}

	for name, test := range cases {
		root := writeModule(t, "shop", test.files)

		exitCode, stdout, stderr := runCommand(t, "fingerprints", "--roots", routeDetail, root)

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

	exitCode, stdout, stderr := runCommand(t, "fingerprints", "--roots", routeDetail, root)

	if exitCode != 1 || stdout != "" {
		t.Fatalf("exit %d, stdout %q", exitCode, stdout)
	}
	if !strings.Contains(stderr, "go.mod") {
		t.Fatalf("stderr must name go.mod, got %q", stderr)
	}
}

func TestFingerprintsFailWhenTheModuleHoldsNoPackage(t *testing.T) {
	root := writeModule(t, "shop", map[string]string{"README.md": "no go here"})

	exitCode, stdout, stderr := runCommand(t, "fingerprints", "--roots", routeDetail, root)

	if exitCode != 1 || stdout != "" || !strings.Contains(stderr, "no Go package") {
		t.Fatalf("exit %d, stdout %q, stderr %q", exitCode, stdout, stderr)
	}
}

func TestFingerprintsRejectBadArgumentsBeforeLoadingAnything(t *testing.T) {
	root := copyOfStorefront(t)
	cases := map[string][]string{
		"no --roots":               {"fingerprints", root},
		"an empty --roots":         {"fingerprints", "--roots", "", root},
		"only commas":              {"fingerprints", "--roots", ",", root},
		"an empty root in a list":  {"fingerprints", "--roots", "listing,,ping", root},
		"a trailing comma":         {"fingerprints", "--roots", "listing,", root},
		"no directory":             {"fingerprints", "--roots", "listing"},
		"two directories":          {"fingerprints", "--roots", "listing", root, root},
		"an empty directory":       {"fingerprints", "--roots", "listing", ""},
		"a missing directory":      {"fingerprints", "--roots", "listing", root + "/nope"},
		"the flag after the root":  {"fingerprints", root, "--roots", "listing"},
		"a directory that is file": {"fingerprints", "--roots", "listing", filepath.Join(root, "go.mod")},
	}

	for name, args := range cases {
		exitCode, stdout, stderr := runCommand(t, args...)

		if exitCode != 1 || stdout != "" || strings.TrimSpace(stderr) == "" {
			t.Errorf("%s: exit %d, stdout %q, stderr %q", name, exitCode, stdout, stderr)
		}
	}
}

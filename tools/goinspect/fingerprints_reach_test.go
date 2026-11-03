package main

import (
	"cmp"
	"path/filepath"
	"reflect"
	"slices"
	"strings"
	"testing"
)

type reachCase struct {
	source    string
	old       string
	new       string
	mustReach []string
	// root is the function to start from, when that is not the plain function detail.
	root string
}

func assertEditMovesAndReaches(t *testing.T, name string, test reachCase) {
	t.Helper()
	id := cmp.Or(test.root, shopDetail)
	root := writeModule(t, "shop", map[string]string{"shop.go": test.source})
	before := fingerprintsOf(t, root, id)
	edited := editedSource(t, name, sourceEdit{source: test.source, old: test.old, new: test.new})
	after := fingerprintsOf(t, writeModule(t, "shop", map[string]string{"shop.go": edited}), id)

	if before.Fingerprints[id] == after.Fingerprints[id] {
		t.Errorf("%s: editing %q left the fingerprint unchanged", name, test.old)
	}
	for _, wanted := range test.mustReach {
		if !slices.Contains(before.Reach[id], wanted) {
			t.Errorf("%s: reach %v lacks %s", name, before.Reach[id], wanted)
		}
	}
	for _, reached := range before.Reach[shopDetail] {
		if strings.Contains(reached, "[shop.") {
			t.Errorf("%s: reach lists the instance %s, and it should list the generic function", name, reached)
		}
	}
}

// With one generic body, x.Size() is a call through a type parameter that no static call graph
// resolves. The concrete type is known at the call site, so the walk reads the instance.
func TestFingerprintFollowsAMethodCalledOnATypeParameter(t *testing.T) {
	measure := "package shop\n\ntype sizer interface{ Size() int }\n\ntype store struct{}\n\nfunc (store) Size() int { return 1 }\n\n" +
		"func measure[T sizer](x T) int { return x.Size() }\n\nfunc detail() int { return measure(store{}) }\n"
	repo := "package shop\n\ntype product struct{}\n\nfunc (product) Key() int { return 1 }\n\n" +
		"type repo[T interface{ Key() int }] struct{ item T }\n\nfunc (r repo[T]) find() int { return r.item.Key() }\n\n" +
		"func detail() int { return repo[product]{}.find() }\n"
	validator := "type product struct{}\n\nfunc (product) Validate() string { return \"ok\" }\n\ntype validator interface{ Validate() string }\n\n"
	genericHandler := "package shop\n\nimport \"net/http\"\n\n" + validator +
		"type crud[T validator] struct{ mux *http.ServeMux }\n\n" +
		"func (c *crud[T]) detail(w http.ResponseWriter, r *http.Request) {\n\tvar item T\n\tw.Write([]byte(item.Validate()))\n}\n\n" +
		"func (c *crud[T]) mount() { c.mux.HandleFunc(\"GET /x\", c.detail) }\n\n" +
		"func Run(m *http.ServeMux) { (&crud[product]{mux: m}).mount() }\n"
	genericFunctionRoot := "package shop\n\n" + validator +
		"func detail[T validator](x T) string { return x.Validate() }\n\nfunc Run() string { return detail(product{}) }\n"
	genericField := "package shop\n\n" + validator +
		"type repo[T validator] struct{ item T }\n\nfunc (r repo[T]) detail() string { return r.item.Validate() }\n\n" +
		"func Run() string { return repo[product]{}.detail() }\n"
	cases := map[string]reachCase{
		// The root is the generic function itself. Its instances are where the concrete type is
		// known, and nothing calls the generic body directly.
		"a generic handler that calls a method on its type parameter": {
			source: genericHandler, old: "return \"ok\"", new: "return \"no\"",
			mustReach: []string{"(shop.product).Validate", "(*shop.crud[T]).detail"},
			root:      "(*shop.crud[T]).detail",
		},
		"a generic function as the root": {
			source: genericFunctionRoot, old: "return \"ok\"", new: "return \"no\"",
			mustReach: []string{"(shop.product).Validate", "shop.detail"},
		},
		"a generic root that calls a method on a field of its type parameter": {
			source: genericField, old: "return \"ok\"", new: "return \"no\"",
			mustReach: []string{"(shop.product).Validate", "(shop.repo[T]).detail"},
			root:      "(shop.repo[T]).detail",
		},
		"a function with a constrained parameter: the method": {
			source: measure, old: "Size() int { return 1 }", new: "Size() int { return 2 }",
			mustReach: []string{"(shop.store).Size", "shop.measure"},
		},
		"a function with a constrained parameter: the generic body": {
			source: measure, old: "return x.Size()", new: "return x.Size() + 1",
			mustReach: []string{"(shop.store).Size", "shop.measure"},
		},
		"a method of a generic type: the method": {
			source: repo, old: "Key() int { return 1 }", new: "Key() int { return 2 }",
			mustReach: []string{"(shop.product).Key", "(shop.repo[T]).find"},
		},
		"a method of a generic type: the generic body": {
			source: repo, old: "return r.item.Key()", new: "return r.item.Key() + 1",
			mustReach: []string{"(shop.product).Key", "(shop.repo[T]).find"},
		},
	}

	for name, test := range cases {
		assertEditMovesAndReaches(t, name, test)
	}
}

// A function named but not called, such as a comparator, is an edge too. Following it can only
// make a fingerprint move more often.
func TestFingerprintFollowsAFunctionHandedOverByName(t *testing.T) {
	cases := map[string]reachCase{
		"a function handed to slices.SortFunc": {
			source: "package shop\n\nimport \"slices\"\n\ntype item struct{ price int }\n\nfunc byPrice(a, b item) int { return a.price - b.price }\n\n" +
				"func detail(xs []item) []item {\n\tslices.SortFunc(xs, byPrice)\n\treturn xs\n}\n",
			old: "a.price - b.price", new: "b.price - a.price",
			mustReach: []string{"shop.byPrice"},
		},
		"a method value handed to sort.Slice": {
			source: "package shop\n\nimport \"sort\"\n\ntype view struct{ xs []int }\n\nfunc (v view) less(i, j int) bool { return v.xs[i] < v.xs[j] }\n\n" +
				"func detail(v view) {\n\tsort.Slice(v.xs, v.less)\n}\n",
			old: "v.xs[i] < v.xs[j]", new: "v.xs[i] > v.xs[j]",
			mustReach: []string{"(shop.view).less"},
		},
		"a function chosen on a branch": {
			source: "package shop\n\nfunc cheap(n int) int { return n }\n\nfunc dear(n int) int { return n * 2 }\n\n" +
				"func detail(premium bool) int {\n\tprice := cheap\n\tif premium {\n\t\tprice = dear\n\t}\n\treturn price(3)\n}\n",
			old: "n * 2", new: "n * 3",
			mustReach: []string{"shop.cheap", "shop.dear"},
		},
	}

	for name, test := range cases {
		assertEditMovesAndReaches(t, name, test)
	}
}

// A body is loaded only for the module's own packages. Another module under the same path comes
// from export data, so its edits cannot show in a hash. A route that reaches one has no hash, and
// the caller counts it as changed on every run.
func TestFingerprintListsARootThatReachesAFunctionWithNoBody(t *testing.T) {
	const reaching = "package shop\n\nimport \"%s\"\n\nfunc detail() int { return lib.Helper() }\n\nfunc ping() int { return 1 }\n"
	library := "package lib\n\nfunc Helper() int { return 1 }\n"
	layouts := map[string]struct {
		files map[string]string
		dir   string
	}{
		"a nested module": {
			files: map[string]string{
				"app/go.mod":        "module shop\n\ngo 1.22\n\nrequire shop/lib v0.0.0\n\nreplace shop/lib => ./nested\n",
				"app/shop.go":       strings.Replace(reaching, "%s", "shop/lib", 1),
				"app/nested/go.mod": "module shop/lib\n\ngo 1.22\n",
				"app/nested/lib.go": library,
			},
			dir: "app",
		},
		"a sibling module that shares the module path as a prefix": {
			files: map[string]string{
				"app/go.mod":  "module shop\n\ngo 1.22\n\nrequire shop/lib v0.0.0\n\nreplace shop/lib => ../lib\n",
				"app/shop.go": strings.Replace(reaching, "%s", "shop/lib", 1),
				"lib/go.mod":  "module shop/lib\n\ngo 1.22\n",
				"lib/lib.go":  library,
			},
			dir: "app",
		},
	}

	for name, layout := range layouts {
		root := writeTree(t, layout.files)

		output := fingerprintsOf(t, filepath.Join(root, layout.dir), shopDetail, shopPing)

		if want := []string{shopDetail}; !reflect.DeepEqual(output.WithoutBody, want) {
			t.Errorf("%s: without a body %v, want %v", name, output.WithoutBody, want)
		}
		if len(output.Unresolved) != 0 {
			t.Errorf("%s: detail is a function, got unresolved %v", name, output.Unresolved)
		}
		if _, listed := output.Fingerprints[shopDetail]; listed {
			t.Errorf("%s: detail reaches code with no body and must have no fingerprint", name)
		}
		if _, listed := output.Reach[shopDetail]; listed {
			t.Errorf("%s: detail must not be listed in reach", name)
		}
		if output.Fingerprints[shopPing] == "" {
			t.Errorf("%s: ping reaches nothing odd and still needs its fingerprint, got %v", name, output.Fingerprints)
		}
	}
}

// ./... passes over these folders, and the service's own imports do not.
func TestFingerprintHashesAFolderTheGoToolPassesOverWhenTheServiceImportsIt(t *testing.T) {
	for _, folder := range []string{"testdata/lib", "_gen/lib"} {
		files := func(body string) map[string]string {
			return map[string]string{
				"shop.go":          "package shop\n\nimport \"shop/" + folder + "\"\n\nfunc detail() int { return lib.Helper() }\n",
				folder + "/lib.go": "package lib\n\nfunc Helper() int { return " + body + " }\n",
			}
		}

		before := fingerprintsOf(t, writeModule(t, "shop", files("1")), shopDetail)
		after := fingerprintsOf(t, writeModule(t, "shop", files("2")), shopDetail)

		if before.Fingerprints[shopDetail] == "" || before.Fingerprints[shopDetail] == after.Fingerprints[shopDetail] {
			t.Errorf("%s: before %v, after %v", folder, before.Fingerprints, after.Fingerprints)
		}
	}
}

// Assembly and linknamed functions are declared in Go source without a body. They have syntax,
// so they stay in a fingerprint. What they run is outside the hash, and the docs say so.
func TestFingerprintKeepsARootThatReachesAFunctionDeclaredWithoutABody(t *testing.T) {
	source := "package shop\n\nimport _ \"unsafe\"\n\n//go:linkname now runtime.nanotime\nfunc now() int64\n\nfunc detail() int64 { return now() }\n"
	root := writeModule(t, "shop", map[string]string{"shop.go": source})

	output := fingerprintsOf(t, root, shopDetail)

	if output.Fingerprints[shopDetail] == "" || len(output.Unresolved) != 0 {
		t.Fatalf("fingerprints %v, unresolved %v", output.Fingerprints, output.Unresolved)
	}
	if want := []string{"shop.detail", "shop.now"}; !reflect.DeepEqual(output.Reach[shopDetail], want) {
		t.Fatalf("reach %v, want %v", output.Reach[shopDetail], want)
	}
}

const genericTypes = "package shop\n\ntype validator interface{ Validate() string }\n\n" +
	"type product struct{}\n\nfunc (product) Validate() string { return \"ok\" }\n\n" +
	"type other struct{}\n\nfunc (other) Validate() string { return \"other\" }\n\n" +
	"type extra struct{}\n\nfunc (extra) Validate() string { return \"extra\" }\n\n"

// Starting a generic root from its instances must not pull in the instances of anything else. An
// instance enters a hash only through its callees, so these edits add an instance that calls a
// method no route reaches: a walk that took in more instances than the root's own would move.
func TestFingerprintStaysTheSameWhenAnUnrelatedInstanceAppears(t *testing.T) {
	source := genericTypes +
		"func check[T validator](x T) string { return x.Validate() }\n\n" +
		"func detail[T validator](x T) string { return check(x) }\n\n" +
		"func Run() string { return detail(product{}) }\n\n" +
		"func lonely[T validator](x T) string { return x.Validate() }\n\n" +
		"func unrelated() string { return lonely(other{}) + check(other{}) }\n"
	cases := map[string]sourceEdit{
		"a generic no route reaches gets an instance with a type of its own": {
			source: source, old: "lonely(other{})", new: "lonely(other{}) + lonely(extra{})",
		},
		"a generic the root reaches gets an instance elsewhere, with a type of its own": {
			source: source, old: "check(other{})", new: "check(other{}) + check(extra{})",
		},
	}

	for name, test := range cases {
		assertEditKeepsTheFingerprint(t, name, test)
	}
}

// A generic root is walked once per instance. The instances come out of a map, so a walk that
// took only the first would miss the second type's method on some runs and not on others. Every
// run must agree, and the edit must show.
func TestFingerprintOfAGenericRootSeesEveryInstanceWhateverTheOrder(t *testing.T) {
	const runs = 8
	source := genericTypes +
		"func detail[T validator](x T) string { return x.Validate() }\n\n" +
		"func Run() string { return detail(product{}) + detail(other{}) }\n"
	edited := editedSource(t, "the second type's method", sourceEdit{source: source, old: "return \"other\"", new: "return \"changed\""})

	unedited := distinctFingerprints(t, source, runs)
	afterEdit := distinctFingerprints(t, edited, runs)

	if len(unedited) != 1 || len(afterEdit) != 1 {
		t.Fatalf("%d runs of each source gave %d and %d distinct fingerprints, and each must give 1", runs, len(unedited), len(afterEdit))
	}
	if reflect.DeepEqual(unedited, afterEdit) {
		t.Fatal("editing the method of the second type left the fingerprint unchanged")
	}
}

func distinctFingerprints(t *testing.T, source string, runs int) map[string]bool {
	t.Helper()
	distinct := map[string]bool{}
	for range runs {
		distinct[fingerprintOfSource(t, source)] = true
	}
	return distinct
}

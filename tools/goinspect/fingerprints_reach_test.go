package main

import (
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
}

func assertEditMovesAndReaches(t *testing.T, name string, test reachCase) {
	t.Helper()
	root := writeModule(t, "shop", map[string]string{"shop.go": test.source})
	before := fingerprintsOf(t, root, routeDetail)
	edited := editedSource(t, name, test.source, test.old, test.new)
	after := fingerprintsOf(t, writeModule(t, "shop", map[string]string{"shop.go": edited}), routeDetail)

	if before.Fingerprints[routeDetail] == after.Fingerprints[routeDetail] {
		t.Errorf("%s: editing %q left the fingerprint unchanged", name, test.old)
	}
	for _, wanted := range test.mustReach {
		if !slices.Contains(before.Reach[routeDetail], wanted) {
			t.Errorf("%s: reach %v lacks %s", name, before.Reach[routeDetail], wanted)
		}
	}
	for _, reached := range before.Reach[routeDetail] {
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
		},
		"a generic function as the root": {
			source: genericFunctionRoot, old: "return \"ok\"", new: "return \"no\"",
			mustReach: []string{"(shop.product).Validate", "shop.detail"},
		},
		"a generic root that calls a method on a field of its type parameter": {
			source: genericField, old: "return \"ok\"", new: "return \"no\"",
			mustReach: []string{"(shop.product).Validate", "(shop.repo[T]).detail"},
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

// ./... loads a body only for the module's own packages. Anything else in the module's path
// comes from export data and has no body, so its edits cannot show in a hash. A route that
// reaches one is unresolved, which the caller counts as changed on every run.
func TestFingerprintListsARootThatReachesAFunctionWithNoBodyAsUnresolved(t *testing.T) {
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
		"an imported testdata directory": {
			files: map[string]string{
				"app/go.mod":              "module shop\n\ngo 1.22\n",
				"app/shop.go":             strings.Replace(reaching, "%s", "shop/testdata/lib", 1),
				"app/testdata/lib/lib.go": library,
			},
			dir: "app",
		},
		"an imported underscore directory": {
			files: map[string]string{
				"app/go.mod":          "module shop\n\ngo 1.22\n",
				"app/shop.go":         strings.Replace(reaching, "%s", "shop/_gen/lib", 1),
				"app/_gen/lib/lib.go": library,
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

		output := fingerprintsOf(t, filepath.Join(root, layout.dir), routeDetail, routePing)

		if want := []string{routeDetail}; !reflect.DeepEqual(output.Unresolved, want) {
			t.Errorf("%s: unresolved %v, want %v", name, output.Unresolved, want)
		}
		if _, listed := output.Fingerprints[routeDetail]; listed {
			t.Errorf("%s: detail reaches code with no body and must have no fingerprint", name)
		}
		if _, listed := output.Reach[routeDetail]; listed {
			t.Errorf("%s: detail must not be listed in reach", name)
		}
		if output.Fingerprints[routePing] == "" {
			t.Errorf("%s: ping reaches nothing odd and still needs its fingerprint, got %v", name, output.Fingerprints)
		}
	}
}

// Assembly and linknamed functions are declared in Go source without a body. They have syntax,
// so they stay in a fingerprint. What they run is outside the hash, and the docs say so.
func TestFingerprintKeepsARootThatReachesAFunctionDeclaredWithoutABody(t *testing.T) {
	source := "package shop\n\nimport _ \"unsafe\"\n\n//go:linkname now runtime.nanotime\nfunc now() int64\n\nfunc detail() int64 { return now() }\n"
	root := writeModule(t, "shop", map[string]string{"shop.go": source})

	output := fingerprintsOf(t, root, routeDetail)

	if output.Fingerprints[routeDetail] == "" || len(output.Unresolved) != 0 {
		t.Fatalf("fingerprints %v, unresolved %v", output.Fingerprints, output.Unresolved)
	}
	if want := []string{"shop.detail", "shop.now"}; !reflect.DeepEqual(output.Reach[routeDetail], want) {
		t.Fatalf("reach %v, want %v", output.Reach[routeDetail], want)
	}
}

// Starting a generic root from its instances must not pull in instances of anything else.
func TestFingerprintStaysTheSameWhenAnUnrelatedFunctionInstantiatesAGeneric(t *testing.T) {
	source := "package shop\n\ntype product struct{}\n\nfunc (product) Validate() string { return \"ok\" }\n\ntype validator interface{ Validate() string }\n\n" +
		"func detail[T validator](x T) string { return x.Validate() }\n\nfunc Run() string { return detail(product{}) }\n\n" +
		"func Pick[T any](x T) T { return x }\n\nfunc other() int { return Pick[int](1) }\n"

	assertEditKeepsTheFingerprint(t, "a new instance of an unrelated generic", source,
		"return Pick[int](1)", "return Pick[int](1) + len(Pick[string](\"a\"))")
}

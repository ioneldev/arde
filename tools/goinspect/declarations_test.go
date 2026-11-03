package main

import (
	"fmt"
	"reflect"
	"sort"
	"strings"
	"testing"
)

func inspectFiles(t *testing.T, files map[string]string) declarationReport {
	t.Helper()
	report, err := inspectDeclarations(writeTree(t, files))
	assertNoError(t, err)
	return report
}

func inspectOneFile(t *testing.T, source string) declarationReport {
	t.Helper()
	return inspectFiles(t, map[string]string{"shop.go": source})
}

func declarationID(d declaration) string {
	return d.File + "::" + d.Kind + " " + d.Name
}

func sortedIDs(report declarationReport) []string {
	ids := []string{}
	for _, d := range report.Declarations {
		ids = append(ids, declarationID(d))
	}
	sort.Strings(ids)
	return ids
}

func hashesByID(t *testing.T, report declarationReport) map[string]string {
	t.Helper()
	hashes := map[string]string{}
	for _, d := range report.Declarations {
		id := declarationID(d)
		if _, duplicate := hashes[id]; duplicate {
			t.Fatalf("declaration id %q appears twice", id)
		}
		hashes[id] = d.Hash
	}
	return hashes
}

func changedIDs(t *testing.T, before, after declarationReport) []string {
	t.Helper()
	oldHashes, newHashes := hashesByID(t, before), hashesByID(t, after)
	changed := []string{}
	for id, newHash := range newHashes {
		if oldHash, existed := oldHashes[id]; !existed || oldHash != newHash {
			changed = append(changed, id)
		}
	}
	sort.Strings(changed)
	return changed
}

const everyKindSource = `package shop

import "fmt"

const Limit = 10

const (
	First = iota
	Second
)

var fxRates = map[string]float64{"EUR": 1}

var (
	alpha, beta = 1, 2
)

type product struct {
	Name string
}

type (
	Order struct{}
	ID    int
)

func Convert(amount float64) float64 { return amount }

func (h *handlers) product(w int) {}

func (h handlers) index() { fmt.Println() }
`

func TestDeclarationsNameEveryTopLevelKind(t *testing.T) {
	report := inspectOneFile(t, everyKindSource)

	want := []string{
		"shop.go::const First",
		"shop.go::const Limit",
		"shop.go::const Second",
		"shop.go::func Convert",
		"shop.go::func handlers.index",
		"shop.go::func handlers.product",
		"shop.go::type ID",
		"shop.go::type Order",
		"shop.go::type product",
		"shop.go::var alpha",
		"shop.go::var beta",
		"shop.go::var fxRates",
	}
	if got := sortedIDs(report); !reflect.DeepEqual(got, want) {
		t.Fatalf("got\n%v\nwant\n%v", got, want)
	}
}

func TestDeclarationHashStableUnderCommentEdit(t *testing.T) {
	commented := strings.NewReplacer(
		"const Limit = 10", "// Limit caps a basket.\nconst Limit = 10 // trailing",
		"func Convert(amount float64) float64 { return amount }", "/* block */\nfunc Convert(amount float64) float64 {\n\t// why\n\treturn   amount\n}",
		"type (\n\tOrder struct{}", "type (\n\t// Order is placed.\n\tOrder struct{}",
	).Replace(everyKindSource)

	before := inspectOneFile(t, everyKindSource)
	after := inspectOneFile(t, commented)

	if changed := changedIDs(t, before, after); len(changed) != 0 {
		t.Fatalf("a comment edit changed %v", changed)
	}
}

func TestDeclarationHashChangesOnlyForTheEditedDeclaration(t *testing.T) {
	edited := strings.Replace(everyKindSource, "const Limit = 10", "const Limit = 20", 1)

	changed := changedIDs(t, inspectOneFile(t, everyKindSource), inspectOneFile(t, edited))

	if want := []string{"shop.go::const Limit"}; !reflect.DeepEqual(changed, want) {
		t.Fatalf("changed %v, want %v", changed, want)
	}
}

func TestDeclarationHashIgnoresAllowedTags(t *testing.T) {
	plain := structSource("`json:\"rating\"`")
	annotated := []string{
		structSource("`json:\"rating\" oneOf:\"a,b\"`"),
		structSource("`json:\"rating\" binding:\"optional\"`"),
		structSource("`binding:\"optional\" json:\"rating\"`"),
	}

	for _, source := range annotated {
		if changed := changedIDs(t, inspectOneFile(t, plain), inspectOneFile(t, source)); len(changed) != 0 {
			t.Errorf("%s: allowed tag changed %v", source, changed)
		}
	}
}

func TestDeclarationHashCountsEveryOtherTagEditAsAChange(t *testing.T) {
	plain := structSource("`json:\"rating\"`")
	edited := map[string]string{
		"json edit":        structSource("`json:\"score\"`"),
		"binding required": structSource("`json:\"rating\" binding:\"required\"`"),
		"xoneOf":           structSource("`json:\"rating\" xoneOf:\"a\"`"),
	}

	for name, source := range edited {
		changed := changedIDs(t, inspectOneFile(t, plain), inspectOneFile(t, source))

		if want := []string{"shop.go::type Product"}; !reflect.DeepEqual(changed, want) {
			t.Errorf("%s: changed %v, want %v", name, changed, want)
		}
	}
}

func TestDeclarationHashOfAConstFollowsImplicitRepetition(t *testing.T) {
	group := func(body string) string { return "package p\n\nconst (\n" + body + ")\n" }
	base := group("\tA = iota\n\tB\n\tC\n")

	cases := map[string]struct {
		edited string
		want   []string
	}{
		"first value edited reaches the specs that repeat it": {
			group("\tA = iota + 1\n\tB\n\tC\n"),
			[]string{"shop.go::const A", "shop.go::const B", "shop.go::const C"},
		},
		"a spec inserted before shifts iota for those after it": {
			group("\tA = iota\n\tX = 5\n\tB\n\tC\n"),
			[]string{"shop.go::const B", "shop.go::const C", "shop.go::const X"},
		},
		"a repeated type edit reaches the specs that repeat it": {
			group("\tA Kind = iota\n\tB\n\tC\n"),
			[]string{"shop.go::const A", "shop.go::const B", "shop.go::const C"},
		},
	}
	for name, c := range cases {
		changed := changedIDs(t, inspectOneFile(t, base), inspectOneFile(t, c.edited))

		if !reflect.DeepEqual(changed, c.want) {
			t.Errorf("%s: changed %v, want %v", name, changed, c.want)
		}
	}
}

func TestDeclarationHashOfAConstThatIgnoresIotaDoesNotShiftWithPosition(t *testing.T) {
	before := inspectOneFile(t, "package p\n\nconst (\n\tA = \"a\"\n\tB = \"b\"\n)\n")
	after := inspectOneFile(t, "package p\n\nconst (\n\tZ = \"z\"\n\tA = \"a\"\n\tB = \"b\"\n)\n")

	changed := changedIDs(t, before, after)

	if want := []string{"shop.go::const Z"}; !reflect.DeepEqual(changed, want) {
		t.Fatalf("changed %v, want %v", changed, want)
	}
}

func TestFieldsReportTagAndLine(t *testing.T) {
	source := "package shop\n" + // line 1
		"\n" + // 2
		"type Product struct {\n" + // 3
		"\tID     string  `json:\"id\"`\n" + // 4
		"\tRating float64 `json:\"rating\" oneOf:\"a,b\"`\n" + // 5
		"\tNotes  []string\n" + // 6
		"\tTags   map[string]*int `json:\"tags\"`\n" + // 7
		"\tLow, High int\n" + // 8
		"}\n"

	report := inspectOneFile(t, source)

	id, rating, tags := "id", "rating", "tags"
	want := []structField{
		{File: "shop.go", Package: "shop", Struct: "Product", Name: "ID", Line: 4, Tag: `json:"id"`, JSONTag: &id, GoType: "string"},
		{File: "shop.go", Package: "shop", Struct: "Product", Name: "Rating", Line: 5, Tag: `json:"rating" oneOf:"a,b"`, JSONTag: &rating, GoType: "float64"},
		{File: "shop.go", Package: "shop", Struct: "Product", Name: "Notes", Line: 6, Tag: "", GoType: "[]string"},
		{File: "shop.go", Package: "shop", Struct: "Product", Name: "Tags", Line: 7, Tag: `json:"tags"`, JSONTag: &tags, GoType: "map[string]*int"},
		{File: "shop.go", Package: "shop", Struct: "Product", Name: "Low", Line: 8, Tag: "", GoType: "int"},
		{File: "shop.go", Package: "shop", Struct: "Product", Name: "High", Line: 8, Tag: "", GoType: "int"},
	}
	if !reflect.DeepEqual(report.Fields, want) {
		t.Fatalf("got\n%+v\nwant\n%+v", report.Fields, want)
	}
}

// jsonTagOfField declares one field with the tag as written and reads back its json key.
func jsonTagOfField(t *testing.T, tag string) *string {
	t.Helper()
	source := "package shop\n\ntype Line struct {\n\tItem string " + tag + "\n}\n"
	return inspectOneFile(t, source).Fields[0].JSONTag
}

func TestTheJSONKeyIsReadAsReflectReadsIt(t *testing.T) {
	cases := map[string]struct {
		tag  string
		want string
	}{
		"a name":                           {"`json:\"id\"`", "id"},
		"a name and options":               {"`json:\"note,omitempty\"`", "note,omitempty"},
		"options and no name":              {"`json:\",omitempty\"`", ",omitempty"},
		"among other keys":                 {"`binding:\"required\" json:\"qty,omitempty\" db:\"q\"`", "qty,omitempty"},
		"after text that looks like a key": {"`doc:\"see json:\" json:\"qty\"`", "qty"},
		"a Unicode escape in the tag":      {"`json:\"caf\\u00e9\"`", "café"},
		"an escaped quote in the tag":      {"`json:\"a\\\"b\"`", `a"b`},
		"a dash":                           {"`json:\"-\"`", "-"},
		"a dash that names a field":        {"`json:\"-,\"`", "-,"},
		"an empty value":                   {"`json:\"\"`", ""},
		"a tag in a quoted Go string":      {`"json:\"id\""`, "id"},
	}

	for name, test := range cases {
		got := jsonTagOfField(t, test.tag)

		if got == nil || *got != test.want {
			t.Errorf("%s: got %v, want %q", name, got, test.want)
		}
	}
}

func TestATagWithNoJSONKeyHasNone(t *testing.T) {
	cases := map[string]string{
		"another key":                       "`yaml:\"port\"`",
		"a key not quoted":                  "`json:port`",
		"a key after text reflect stops at": "`broken json:\"id\"`",
		"bytes that are not UTF-8":          "`json:\"caf\\xe9\"`",
	}

	for name, tag := range cases {
		if got := jsonTagOfField(t, tag); got != nil {
			t.Errorf("%s: got %q, want no json key", name, *got)
		}
	}
}

func TestAFieldWithNoTagHasNoJSONKey(t *testing.T) {
	report := inspectOneFile(t, "package shop\n\ntype Line struct {\n\tItem string\n}\n")

	if report.Fields[0].JSONTag != nil {
		t.Fatalf("got %q", *report.Fields[0].JSONTag)
	}
}

func TestTheReplyGivesNullForNoJSONKeyAndNamesThePackage(t *testing.T) {
	root := writeTree(t, map[string]string{"line.go": "package billing\n\ntype Line struct {\n\tItem string\n}\n"})

	_, stdout, _ := runCommand(t, "declarations", root)

	for _, want := range []string{`"json_tag":null`, `"package":"billing"`} {
		if !strings.Contains(stdout, want) {
			t.Errorf("want %s in %s", want, stdout)
		}
	}
}

func TestFieldsMarkEmbeddedFieldsByTheirTypeName(t *testing.T) {
	source := `package shop

type Page struct {
	Base
	*Meta ` + "`json:\"meta\"`" + `
	common.Audit
	Box[int]
	Name string
}
`

	report := inspectOneFile(t, source)

	got := map[string]structField{}
	for _, field := range report.Fields {
		got[field.Name] = field
	}
	wantEmbedded := map[string]string{"Base": "Base", "Meta": "*Meta", "Audit": "common.Audit", "Box": "Box[int]"}
	for name, goType := range wantEmbedded {
		field, found := got[name]
		if !found || !field.Embedded || field.GoType != goType {
			t.Errorf("embedded %s: got %+v", name, field)
		}
	}
	if got["Meta"].Tag != `json:"meta"` {
		t.Errorf("embedded field lost its tag: %+v", got["Meta"])
	}
	if got["Name"].Embedded || len(report.Fields) != 5 {
		t.Errorf("a named field must not be marked embedded, and nothing else may appear: %+v", report.Fields)
	}
}

func TestFieldsCoverEveryTopLevelStructAndNothingElse(t *testing.T) {
	source := `package shop

type Outer struct {
	Inner struct{ Hidden int }
}

type (
	Grouped struct{ A int }
	Alias   = struct{ B int }
	Number  int
	Reader  interface{ Read() }
)

type Page[T any] struct{ Items []T }

func f() {
	type local struct{ Skipped int }
}
`

	report := inspectOneFile(t, source)

	var seen []string
	for _, field := range report.Fields {
		seen = append(seen, field.Struct+"."+field.Name)
	}
	sort.Strings(seen)
	want := []string{"Alias.B", "Grouped.A", "Outer.Inner", "Page.Items"}
	if !reflect.DeepEqual(seen, want) {
		t.Fatalf("got %v, want %v", seen, want)
	}
}

func TestDeclarationIDsStayUniqueForRepeatedInitAndBlank(t *testing.T) {
	files := map[string]string{
		"a.go": "package p\n\nfunc init() {}\nfunc init() {}\nfunc init() {}\n\nvar _ = 1\nvar _ = 2\nconst _ = 3\ntype _ int\n",
		"b.go": "package p\n\nfunc init() {}\n",
	}

	report := inspectFiles(t, files)

	hashesByID(t, report) // fails on a duplicate
	want := []string{
		"a.go::const _",
		"a.go::func init",
		"a.go::func init#2",
		"a.go::func init#3",
		"a.go::type _",
		"a.go::var _",
		"a.go::var _#2",
		"b.go::func init",
	}
	if got := sortedIDs(report); !reflect.DeepEqual(got, want) {
		t.Fatalf("got\n%v\nwant\n%v", got, want)
	}
}

func TestRepeatedInitFunctionsKeepTheirOwnHash(t *testing.T) {
	report := inspectOneFile(t, "package p\n\nfunc init() { a() }\nfunc init() { b() }\n")

	hashes := hashesByID(t, report)

	if hashes["shop.go::func init"] == hashes["shop.go::func init#2"] {
		t.Fatal("two different init bodies share a hash")
	}
}

func TestMethodOnAGenericReceiverNamesItsType(t *testing.T) {
	source := `package p

func (s *Stack[T]) Push(v T) {}
func (s Stack[T]) Len() int { return 0 }
func (p Pair[K, V]) Key() K { var k K; return k }
func (p *(Plain)) Name() {}
`

	report := inspectOneFile(t, source)

	want := []string{
		"shop.go::func Pair.Key",
		"shop.go::func Plain.Name",
		"shop.go::func Stack.Len",
		"shop.go::func Stack.Push",
	}
	if got := sortedIDs(report); !reflect.DeepEqual(got, want) {
		t.Fatalf("got\n%v\nwant\n%v", got, want)
	}
}

func TestDeclarationsIncludeTestFiles(t *testing.T) {
	report := inspectFiles(t, map[string]string{
		"pricing_test.go": "package p\n\nfunc TestPrice(t *testing.T) {}\n",
	})

	want := []string{"pricing_test.go::func TestPrice"}
	if got := sortedIDs(report); !reflect.DeepEqual(got, want) {
		t.Fatalf("got %v, want %v", got, want)
	}
}

func TestDeclarationsSkipWhatTheGoToolIgnores(t *testing.T) {
	broken := "package p\nfunc ("
	report := inspectFiles(t, map[string]string{
		"keep.go":         "package p\nvar kept = 1\n",
		"vendor/lib.go":   broken,
		"testdata/in.go":  broken,
		"_scratch/in.go":  broken,
		".hidden/in.go":   broken,
		".git/objects.go": broken,
	})

	want := []string{"keep.go::var kept"}
	if got := sortedIDs(report); !reflect.DeepEqual(got, want) {
		t.Fatalf("got %v, want %v", got, want)
	}
}

func TestDeclarationsFailOnAFileThatDoesNotParseAndNameIt(t *testing.T) {
	root := writeTree(t, map[string]string{
		"ok.go":         "package p\nvar fine = 1\n",
		"pkg/broken.go": "package p\nfunc (",
	})

	report, err := inspectDeclarations(root)

	if err == nil {
		t.Fatalf("want an error, got %+v", report)
	}
	if !strings.Contains(err.Error(), "pkg/broken.go") {
		t.Fatalf("the error must name the file, got: %v", err)
	}
}

func TestDeclarationLinesAreTheLinesInTheFile(t *testing.T) {
	source := "package p\r\n" + // 1
		"\r\n" + // 2
		"//line generated.go:500\r\n" + // 3
		"func First() {}\r\n" + // 4
		"\r\n" + // 5
		"var second = 1\r\n" // 6

	lines := declarationLines(inspectOneFile(t, source))

	if want := map[string]int{"First": 4, "second": 6}; !reflect.DeepEqual(lines, want) {
		t.Fatalf("got %v, want %v", lines, want)
	}
}

func declarationLines(report declarationReport) map[string]int {
	lines := map[string]int{}
	for _, d := range report.Declarations {
		lines[d.Name] = d.Line
	}
	return lines
}

func TestEachVarNameKeepsItsOwnLine(t *testing.T) {
	source := "package p\n" + // 1
		"\n" + // 2
		"var (\n" + // 3
		"\tfirst,\n" + // 4
		"\tsecond = 1, 2\n" + // 5
		")\n"

	lines := declarationLines(inspectOneFile(t, source))

	if want := map[string]int{"first": 4, "second": 5}; !reflect.DeepEqual(lines, want) {
		t.Fatalf("got %v, want %v", lines, want)
	}
}

func TestEachConstNameKeepsItsOwnLine(t *testing.T) {
	source := "package p\n" + // 1
		"\n" + // 2
		"const (\n" + // 3
		"\tfirst,\n" + // 4
		"\tsecond = 1, 2\n" + // 5
		")\n"

	lines := declarationLines(inspectOneFile(t, source))

	if want := map[string]int{"first": 4, "second": 5}; !reflect.DeepEqual(lines, want) {
		t.Fatalf("got %v, want %v", lines, want)
	}
}

func TestEachFieldNameKeepsItsOwnLine(t *testing.T) {
	source := "package p\n" + // 1
		"\n" + // 2
		"type Range struct {\n" + // 3
		"\tLow,\n" + // 4
		"\tHigh int\n" + // 5
		"}\n"

	lines := map[string]int{}
	for _, field := range inspectOneFile(t, source).Fields {
		lines[field.Name] = field.Line
	}

	if want := map[string]int{"Low": 4, "High": 5}; !reflect.DeepEqual(lines, want) {
		t.Fatalf("got %v, want %v", lines, want)
	}
}

func TestDeclarationsAreSortedByFileThenLine(t *testing.T) {
	report := inspectFiles(t, map[string]string{
		"b.go":   "package p\nvar b1 = 1\nvar b2 = 2\n",
		"a/z.go": "package p\nvar z = 1\n",
		"a.go":   "package p\nvar a2 = 2\n\nvar a1 = 1\n",
	})

	var order []string
	for _, d := range report.Declarations {
		order = append(order, fmt.Sprintf("%s:%d", d.File, d.Line))
	}
	want := []string{"a.go:2", "a.go:4", "a/z.go:2", "b.go:2", "b.go:3"}
	if !reflect.DeepEqual(order, want) {
		t.Fatalf("got %v, want %v", order, want)
	}
}

func TestFieldsAreSortedByFileThenLine(t *testing.T) {
	report := inspectFiles(t, map[string]string{
		"b.go": "package p\ntype B struct{ X int }\n",
		"a.go": "package p\ntype A struct{\n Y int\n Z int\n}\n",
	})

	var order []string
	for _, f := range report.Fields {
		order = append(order, fmt.Sprintf("%s:%d:%s", f.File, f.Line, f.Name))
	}
	want := []string{"a.go:3:Y", "a.go:4:Z", "b.go:2:X"}
	if !reflect.DeepEqual(order, want) {
		t.Fatalf("got %v, want %v", order, want)
	}
}

func TestDeclarationsOfAnEmptyTreeHoldEmptyListsNotNull(t *testing.T) {
	report := inspectFiles(t, map[string]string{"README.md": "no go here"})

	want := declarationReport{Declarations: []declaration{}, Fields: []structField{}}
	if !reflect.DeepEqual(report, want) {
		t.Fatalf("got %#v, want %#v", report, want)
	}
}

func TestDeclarationHashIsIdenticalForCRLFAndLFCheckouts(t *testing.T) {
	crlf := strings.ReplaceAll(everyKindSource, "\n", "\r\n")

	changed := changedIDs(t, inspectOneFile(t, everyKindSource), inspectOneFile(t, crlf))

	if len(changed) != 0 {
		t.Fatalf("line endings changed %v", changed)
	}
}

func TestDeclarationHashFlagsSyntaxTheParserKeepsOnlyAsAPosition(t *testing.T) {
	for name, c := range syntaxHeldInPositions {
		changed := changedIDs(t, inspectOneFile(t, c.before), inspectOneFile(t, c.after))

		if !reflect.DeepEqual(changed, []string{c.changedID}) {
			t.Errorf("%s: changed %v, want %v", name, changed, c.changedID)
		}
	}
}

func TestDeclarationHashIgnoresMovedAliasesAndSpreads(t *testing.T) {
	before := inspectOneFile(t, sameSyntaxMovedBefore)
	after := inspectOneFile(t, sameSyntaxMovedAfter)

	if changed := changedIDs(t, before, after); len(changed) != 0 {
		t.Fatalf("moving code changed %v", changed)
	}
}

func TestDeclarationHashFlagsADroppedRequiredBehindARepeatedBindingKey(t *testing.T) {
	required := structSource("`binding:\"required\"`")
	edited := structSource("`binding:\"optional\" binding:\"required\"`")

	changed := changedIDs(t, inspectOneFile(t, required), inspectOneFile(t, edited))

	if want := []string{"shop.go::type Product"}; !reflect.DeepEqual(changed, want) {
		t.Fatalf("changed %v, want %v", changed, want)
	}
}

func TestFieldsOfAParenthesisedStructAreListed(t *testing.T) {
	report := inspectOneFile(t, "package shop\n\ntype P (struct{ X int })\n\ntype Q ((struct{ Y string }))\n")

	var seen []string
	for _, field := range report.Fields {
		seen = append(seen, field.Struct+"."+field.Name)
	}
	if want := []string{"P.X", "Q.Y"}; !reflect.DeepEqual(seen, want) {
		t.Fatalf("got %v, want %v", seen, want)
	}
}

func TestDeclarationsSkipGoFilesTheGoToolIgnores(t *testing.T) {
	broken := "package p\nfunc ("
	report := inspectFiles(t, map[string]string{
		"keep.go":        "package p\nvar kept = 1\n",
		"x_gen.go":       "package p\nvar generated = 1\n",
		"_gen.go":        broken,
		".swap.go":       broken,
		"pkg/_other.go":  "package p\nvar hidden = 1\n",
		"pkg/.editor.go": broken,
	})

	want := []string{"keep.go::var kept", "x_gen.go::var generated"}
	if got := sortedIDs(report); !reflect.DeepEqual(got, want) {
		t.Fatalf("got %v, want %v", got, want)
	}
}

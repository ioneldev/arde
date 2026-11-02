package main

import (
	"fmt"
	"reflect"
	"strings"
	"testing"
)

const priceSource = `package shop

import "fmt"

const maxQty = 10

// Price formats a quantity.
func Price(qty int) string {
	return fmt.Sprint(qty * maxQty)
}
`

func compareOneFile(t *testing.T, oldSource, newSource string) compareReport {
	t.Helper()
	return compareFiles(t, map[string]string{"shop.go": oldSource}, map[string]string{"shop.go": newSource})
}

func compareFiles(t *testing.T, oldFiles, newFiles map[string]string) compareReport {
	t.Helper()
	report, err := compareDirs(writeTree(t, oldFiles), writeTree(t, newFiles))
	assertNoError(t, err)
	return report
}

func structSource(fieldTagLiteral string) string {
	return fmt.Sprintf("package shop\n\ntype Product struct {\n\tRating float64 %s\n}\n", fieldTagLiteral)
}

func assertChanged(t *testing.T, report compareReport, want ...string) {
	t.Helper()
	if want == nil {
		want = []string{}
	}
	if !reflect.DeepEqual(report.Changed, want) {
		t.Fatalf("changed = %v, want %v", report.Changed, want)
	}
}

func TestCompareIgnoresCommentsAndFormatting(t *testing.T) {
	reformatted := `package shop
/* a block comment */
import   "fmt"   // trailing


const maxQty=10
func Price( qty int ) string {
		return fmt.Sprint( qty*maxQty ) // inline
}
`

	report := compareOneFile(t, priceSource, reformatted)

	assertChanged(t, report)
}

func TestCompareIgnoresOneOfAndBindingTags(t *testing.T) {
	cases := map[string][2]string{
		"oneOf added":                     {"`json:\"rating\"`", "`json:\"rating\" oneOf:\"a,b\"`"},
		"binding optional added":          {"`json:\"rating\"`", "`json:\"rating\" binding:\"optional\"`"},
		"both added, other order":         {"`json:\"rating\"`", "`oneOf:\"x\" json:\"rating\" binding:\"optional\"`"},
		"tag only holds an allowed key":   {"", "`oneOf:\"a\"`"},
		"tag only holds binding optional": {"", "`binding:\"optional\"`"},
		"oneOf values differ":             {"`oneOf:\"a\"`", "`oneOf:\"b\"`"},
		"same tag, interpreted string":    {"`json:\"rating\"`", "\"json:\\\"rating\\\"\""},
		"spacing between keys":            {"`json:\"r\" xml:\"r\"`", "`json:\"r\"   xml:\"r\"`"},
	}

	for name, sources := range cases {
		report := compareOneFile(t, structSource(sources[0]), structSource(sources[1]))

		if len(report.Changed) != 0 {
			t.Errorf("%s: want no change, got %v", name, report.Changed)
		}
	}
}

func TestCompareFlagsTagsThatOnlyLookAllowed(t *testing.T) {
	cases := map[string]string{
		"binding required":                  "`json:\"rating\" binding:\"required\"`",
		"binding with more than optional":   "`json:\"rating\" binding:\"optional,min=1\"`",
		"key that ends in oneOf":            "`json:\"rating\" xoneOf:\"a\"`",
		"key that starts with oneOf":        "`json:\"rating\" oneOfx:\"a\"`",
		"key that ends in binding":          "`json:\"rating\" Xbinding:\"optional\"`",
		"allowed key hidden in other value": "`json:\"rating,oneOf:\\\"a\\\"\"`",
	}

	for name, newTag := range cases {
		report := compareOneFile(t, structSource("`json:\"rating\"`"), structSource(newTag))

		if !reflect.DeepEqual(report.Changed, []string{"shop.go"}) {
			t.Errorf("%s: want shop.go changed, got %v", name, report.Changed)
		}
	}
}

func TestCompareFlagsEditedJSONTag(t *testing.T) {
	report := compareOneFile(t, structSource("`json:\"rating\"`"), structSource("`json:\"score\"`"))

	assertChanged(t, report, "shop.go")
}

func TestCompareTreatsAMalformedTagAsOpaque(t *testing.T) {
	malformed := structSource("`json:rating`")

	assertChanged(t, compareOneFile(t, malformed, malformed))
	assertChanged(t, compareOneFile(t, malformed, structSource("`json:rating oneOf:\"a\"`")), "shop.go")
}

func TestCompareFlagsOneTokenChange(t *testing.T) {
	report := compareOneFile(t, priceSource, strings.Replace(priceSource, "maxQty = 10", "maxQty = 20", 1))

	assertChanged(t, report, "shop.go")
}

func TestCompareFlagsBlankImport(t *testing.T) {
	withPprof := strings.Replace(priceSource, `import "fmt"`, "import (\n\t\"fmt\"\n\t_ \"net/http/pprof\"\n)", 1)

	report := compareOneFile(t, priceSource, withPprof)

	assertChanged(t, report, "shop.go")
}

func TestCompareIgnoresLineEndings(t *testing.T) {
	withRawString := priceSource + "\nvar banner = `first\nsecond`\n"
	crlf := strings.ReplaceAll(withRawString, "\n", "\r\n")

	report := compareOneFile(t, withRawString, crlf)

	assertChanged(t, report)
}

func TestCompareReportsAddedRemovedAndChangedFilesSorted(t *testing.T) {
	oldFiles := map[string]string{
		"gone_b.go":  "package p\n",
		"gone_a.go":  "package p\n",
		"same.go":    "package p\n",
		"edited.go":  "package p\nvar x = 1\n",
		"edited2.go": "package p\nvar x = 1\n",
	}
	newFiles := map[string]string{
		"new_b.go":     "package p\n",
		"new/dir/a.go": "package p\n",
		"new_a.go":     "package p\n",
		"same.go":      "package p\n",
		"edited.go":    "package p\nvar x = 2\n",
		"edited2.go":   "package p\nvar x = 3\n",
	}

	report := compareFiles(t, oldFiles, newFiles)

	want := compareReport{
		Changed: []string{"edited.go", "edited2.go"},
		Added:   []string{"new/dir/a.go", "new_a.go", "new_b.go"},
		Removed: []string{"gone_a.go", "gone_b.go"},
	}
	if !reflect.DeepEqual(report, want) {
		t.Fatalf("got %+v, want %+v", report, want)
	}
}

func TestCompareOfIdenticalTreesHoldsEmptyListsNotNull(t *testing.T) {
	files := map[string]string{"a.go": "package p\n"}

	report := compareFiles(t, files, files)

	want := compareReport{Changed: []string{}, Added: []string{}, Removed: []string{}}
	if !reflect.DeepEqual(report, want) {
		t.Fatalf("got %#v, want %#v", report, want)
	}
}

func TestCompareSkipsOnlyTheGitDirectory(t *testing.T) {
	oldFiles := map[string]string{
		".git/x.go":     "package p\nvar x = 1\n",
		"vendor/x.go":   "package p\nvar x = 1\n",
		"testdata/x.go": "package p\nvar x = 1\n",
		"_old/x.go":     "package p\nvar x = 1\n",
		".dot/x.go":     "package p\nvar x = 1\n",
	}
	newFiles := map[string]string{
		".git/x.go":     "package p\nvar x = 2\n",
		"vendor/x.go":   "package p\nvar x = 2\n",
		"testdata/x.go": "package p\nvar x = 2\n",
		"_old/x.go":     "package p\nvar x = 2\n",
		".dot/x.go":     "package p\nvar x = 2\n",
	}

	report := compareFiles(t, oldFiles, newFiles)

	assertChanged(t, report, ".dot/x.go", "_old/x.go", "testdata/x.go", "vendor/x.go")
}

func TestCompareLooksAtGoFilesOnly(t *testing.T) {
	report := compareFiles(t,
		map[string]string{"go.mod": "module a\n", "notes.md": "one"},
		map[string]string{"go.mod": "module b\n", "notes.md": "two"},
	)

	assertChanged(t, report)
}

func TestCompareFallsBackToBytesForFilesThatDoNotParse(t *testing.T) {
	broken := "package p\nfunc ("
	brokenWithComment := "package p\nfunc ( // a comment only\n"
	valid := "package p\n"

	cases := map[string]struct {
		oldSource, newSource string
		wantChanged          bool
	}{
		"broken on both sides, identical":              {broken, broken, false},
		"broken on both sides, differing in a comment": {broken, brokenWithComment, true},
		"parses before, broken after":                  {valid, broken, true},
		"broken before, parses after":                  {broken, valid, true},
	}

	for name, c := range cases {
		report := compareOneFile(t, c.oldSource, c.newSource)

		if gotChanged := len(report.Changed) == 1; gotChanged != c.wantChanged {
			t.Errorf("%s: changed = %v, want %v", name, report.Changed, c.wantChanged)
		}
	}
}

func TestCompareRejectsARootThatDoesNotExist(t *testing.T) {
	_, err := compareDirs(writeTree(t, nil), "/nonexistent/goinspect/root")

	if err == nil {
		t.Fatal("want an error")
	}
}

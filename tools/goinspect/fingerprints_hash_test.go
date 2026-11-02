package main

import (
	"strings"
	"testing"
)

// fingerprintOfSource fingerprints the route "detail" in a module made of one file.
func fingerprintOfSource(t *testing.T, source string) string {
	t.Helper()
	root := writeModule(t, "shop", map[string]string{"shop.go": source})
	hash := fingerprintsOf(t, root, shopDetail).Fingerprints[shopDetail]
	if hash == "" {
		t.Fatalf("detail did not resolve in:\n%s", source)
	}
	return hash
}

func editedSource(t *testing.T, name, source, old, replacement string) string {
	t.Helper()
	if strings.Count(source, old) != 1 {
		t.Fatalf("%s: the source must hold %q exactly once", name, old)
	}
	return strings.Replace(source, old, replacement, 1)
}

func assertEditMovesTheFingerprint(t *testing.T, name, source, old, replacement string) {
	t.Helper()
	edited := editedSource(t, name, source, old, replacement)
	if fingerprintOfSource(t, source) == fingerprintOfSource(t, edited) {
		t.Errorf("%s: editing %q to %q left the fingerprint unchanged", name, old, replacement)
	}
}

func assertEditKeepsTheFingerprint(t *testing.T, name, source, old, replacement string) {
	t.Helper()
	edited := editedSource(t, name, source, old, replacement)
	if fingerprintOfSource(t, source) != fingerprintOfSource(t, edited) {
		t.Errorf("%s: editing %q to %q moved the fingerprint", name, old, replacement)
	}
}

type sourceEdit struct {
	source string
	old    string
	new    string
}

// The SSA listing cuts a string over 20 bytes and prints a float to 6 digits, so an edit past
// those points shows only in the constant itself.
func TestFingerprintChangesWhenAConstantChangesPastWhatTheListingShows(t *testing.T) {
	cases := map[string]sourceEdit{
		"the last byte of a 21-byte string": {
			source: "package shop\n\nfunc detail() string { return \"abcdefghijklmnopqrstu\" }\n",
			old:    "qrstu", new: "qrstv",
		},
		"a SQL string": {
			source: "package shop\n\nfunc detail() string { return \"SELECT id FROM items WHERE active = 1\" }\n",
			old:    "active = 1", new: "active = 0",
		},
		"a URL": {
			source: "package shop\n\nfunc detail() string { return \"https://example.test/v1/rates\" }\n",
			old:    "/v1/", new: "/v2/",
		},
		"a named constant holding a long string": {
			source: "package shop\n\nconst query = \"SELECT id FROM items WHERE active = 1\"\n\nfunc detail() string { return query }\n",
			old:    "active = 1", new: "active = 0",
		},
		"a 21-byte map key": {
			source: "package shop\n\nfunc detail() int { return map[string]int{\"abcdefghijklmnopqrstu\": 1}[\"x\"] }\n",
			old:    "qrstu", new: "qrstv",
		},
		"a switch case string": {
			source: "package shop\n\nfunc detail(s string) int {\n\tswitch s {\n\tcase \"abcdefghijklmnopqrstu\":\n\t\treturn 1\n\t}\n\treturn 0\n}\n",
			old:    "qrstu", new: "qrstv",
		},
		"a float at its seventh digit": {
			source: "package shop\n\nfunc detail() float64 { return 4.975312 }\n",
			old:    "4.975312", new: "4.975313",
		},
		"a large float": {
			source: "package shop\n\nfunc detail() float64 { return 1234567.0 }\n",
			old:    "1234567.0", new: "1234568.0",
		},
		"a small float": {
			source: "package shop\n\nfunc detail() float64 { return 0.1912345 }\n",
			old:    "0.1912345", new: "0.1912349",
		},
	}

	for name, test := range cases {
		assertEditMovesTheFingerprint(t, name, test.source, test.old, test.new)
	}
}

// SSA prints a type declared inside a function by its name alone.
func TestFingerprintChangesWhenATypeDeclaredInsideTheFunctionChanges(t *testing.T) {
	source := "package shop\n\nfunc detail() any {\n\ttype response struct {\n\t\tName string `json:\"name\"`\n\t}\n\treturn response{Name: \"a\"}\n}\n"
	cases := map[string]sourceEdit{
		"a renamed tag":      {source: source, old: "json:\"name\"", new: "json:\"title\""},
		"an added omitempty": {source: source, old: "json:\"name\"", new: "json:\"name,omitempty\""},
		"an added field":     {source: source, old: "Name string `json:\"name\"`\n", new: "Name string `json:\"name\"`\n\t\tPrice int\n"},
	}

	for name, test := range cases {
		assertEditMovesTheFingerprint(t, name, test.source, test.old, test.new)
	}
}

func TestFingerprintStaysTheSameForEditsThatChangeNoMeaning(t *testing.T) {
	source := "package shop\n\nfunc detail() any {\n\t// the response\n\ttype response struct {\n\t\tName string `json:\"name\"`\n\t}\n\treturn response{Name: \"a\"}\n}\n"
	cases := map[string]sourceEdit{
		"a comment in the function":  {source: source, old: "// the response", new: "// the wire shape\n\t/* and more */"},
		"a trailing comment":         {source: source, old: "return response{Name: \"a\"}", new: "return response{Name: \"a\"} // done"},
		"a binding:\"optional\" tag": {source: source, old: "`json:\"name\"`", new: "`json:\"name\" binding:\"optional\"`"},
		"a oneOf tag":                {source: source, old: "`json:\"name\"`", new: "`json:\"name\" oneOf:\"a,b\"`"},
		"both tags ARDE adds":        {source: source, old: "`json:\"name\"`", new: "`json:\"name\" oneOf:\"a,b\" binding:\"optional\"`"},
		"different spacing":          {source: source, old: "return response{Name: \"a\"}", new: "return   response{Name:   \"a\"}"},
	}

	for name, test := range cases {
		assertEditKeepsTheFingerprint(t, name, test.source, test.old, test.new)
	}
}

// A spread argument and an alias are kept by the parser as a token position, so syntax that is
// not normalised the way `compare` does would change with the line number.
func TestFingerprintStaysTheSameWhenAFunctionWithALongStringMovesDownAFile(t *testing.T) {
	source := "package shop\n\nfunc detail() any {\n" +
		"\ttype count = int\n" +
		"\ttype response struct {\n\t\tName string `json:\"name\"`\n\t\tTotal count\n\t}\n" +
		"\tsum := func(parts ...int) int { return len(parts) }\n" +
		"\tparts := []int{1, 2}\n" +
		"\tquery := \"SELECT id FROM items WHERE active = 1 AND stock > 0\"\n" +
		"\treturn response{Name: query, Total: sum(parts...)}\n}\n"

	assertEditKeepsTheFingerprint(t, "unrelated code above", source, "func detail() any {",
		"func unrelated() int {\n\n\n\treturn 7\n}\n\nfunc detail() any {")
}

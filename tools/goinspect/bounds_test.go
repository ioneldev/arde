package main

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"go/ast"
	"go/parser"
	"runtime"
	"strings"
	"testing"
)

const nameEndingInB = `[{"kind":"param","call":"b$","args":{"name":0}}]`

// bytesAllocatedBy reports everything the call allocated, freed or not, so a buffer that is built
// and dropped still counts.
func bytesAllocatedBy(call func()) uint64 {
	var before, after runtime.MemStats
	runtime.ReadMemStats(&before)
	call()
	runtime.ReadMemStats(&after)
	return after.TotalAlloc - before.TotalAlloc
}

func deepExpression(t *testing.T, depth int) ast.Expr {
	t.Helper()
	expression, err := parser.ParseExpr(strings.Repeat("!", depth) + "true")
	if err != nil {
		t.Fatal(err)
	}
	return expression
}

func fieldsIn(t *testing.T, source string) []structField {
	t.Helper()
	root := writeTree(t, map[string]string{"t.go": source})
	exitCode, stdout, stderr := runCommand(t, "declarations", root)
	if exitCode != 0 || stderr != "" {
		t.Fatalf("exit %d, stderr %q", exitCode, stderr)
	}
	var report declarationReport
	if err := json.Unmarshal([]byte(stdout), &report); err != nil {
		t.Fatalf("not a declarations report: %v", err)
	}
	return report.Fields
}

func TestAHashIsOfTheDumpOfEachNodeInTurn(t *testing.T) {
	first, second := deepExpression(t, 3), deepExpression(t, 5)
	var dump bytes.Buffer
	for _, node := range []ast.Expr{first, second} {
		if err := ast.Fprint(&dump, nil, node, omitPositions); err != nil {
			t.Fatal(err)
		}
	}
	sum := sha256.Sum256(dump.Bytes())

	if got, want := hashOfDump(first, second), hex.EncodeToString(sum[:]); got != want {
		t.Fatalf("got %s, want %s", got, want)
	}
}

func TestHashesStayWhatTheyWereBeforeTheDumpStreamed(t *testing.T) {
	root := writeTree(t, map[string]string{"t.go": "package p\n\nfunc F(a int) int { return a + 1 }\n"})
	exitCode, stdout, stderr := runCommand(t, "declarations", root)
	if exitCode != 0 || stderr != "" {
		t.Fatalf("exit %d, stderr %q", exitCode, stderr)
	}
	const hashFromTheBufferedVersion = "273f51bbb51be595b8a223f084ac30216855ff36707f2325757740e81229f282"
	if !strings.Contains(stdout, hashFromTheBufferedVersion) {
		t.Fatalf("the hash of F moved: %s", stdout)
	}
}

// The dump of a tree this deep is 72 MB, and a buffer that held it would allocate more than that as
// it grew.
func TestHashingDoesNotHoldTheDumpInMemory(t *testing.T) {
	expression := deepExpression(t, 4000)
	var dump countingWriter
	if err := ast.Fprint(&dump, nil, expression, omitPositions); err != nil {
		t.Fatal(err)
	}

	allocated := bytesAllocatedBy(func() { hashOfDump(expression) })

	if allocated >= dump.written {
		t.Fatalf("allocated %d bytes for a dump of %d bytes", allocated, dump.written)
	}
}

type countingWriter struct{ written uint64 }

func (writer *countingWriter) Write(content []byte) (int, error) {
	writer.written += uint64(len(content))
	return len(content), nil
}

func nestedCalls(depth int) string {
	return "package p\n\nvar X = " + strings.Repeat("pick(", depth) + `"inner"` + strings.Repeat(")", depth) + "\n"
}

func TestMatchPrintsAnArgumentUpToTheCap(t *testing.T) {
	argument := strings.Repeat("a", maxPrintedBytes)

	found := matchesIn(t, map[string]string{"a.go": "package p\n\nvar X = pick(" + argument + ")\n"}, oneArgumentPattern)

	if got := found[0].Args["name"]; got != argument {
		t.Fatalf("an argument of exactly the cap must print whole, got %d bytes", len(got))
	}
}

func TestMatchDoesNotPrintAnArgumentPastTheCap(t *testing.T) {
	argument := strings.Repeat("a", maxPrintedBytes+1)

	found := matchesIn(t, map[string]string{"a.go": "package p\n\nvar X = pick(" + argument + ")\n"}, oneArgumentPattern)

	if got := found[0].Args["name"]; got != tooLongToPrint {
		t.Fatalf("got %d bytes of text", len(got))
	}
}

func TestMatchStillReportsTheLiteralOfAnArgumentTooLongToPrint(t *testing.T) {
	long := strings.Repeat("x", maxPrintedBytes+1)
	root := writeTree(t, map[string]string{"a.go": "package p\n\nvar X = pick(\"" + long + "\")\n"})
	patternsFile := writeTree(t, map[string]string{"patterns.json": oneArgumentPattern}) + "/patterns.json"

	report, err := findMatches(root, patternsFile)

	assertNoError(t, err)
	if got := report.Matches[0].Literals["name"]; got != long {
		t.Fatalf("got a literal of %d bytes", len(got))
	}
}

// Printing every level of a nested call costs the square of the file. Past the cap each level
// costs one short text, so the reply grows with the file.
func TestNestedCallsGiveAReplyThatGrowsWithTheFile(t *testing.T) {
	source := nestedCalls(4000)
	root := writeTree(t, map[string]string{"a.go": source})
	patternsFile := writeTree(t, map[string]string{"patterns.json": oneArgumentPattern}) + "/patterns.json"

	report, err := findMatches(root, patternsFile)

	assertNoError(t, err)
	encoded, err := json.Marshal(report)
	assertNoError(t, err)
	if len(report.Matches) != 4000 {
		t.Fatalf("every level is still a match, got %d", len(report.Matches))
	}
	if limit := 60 * len(source); len(encoded) > limit {
		t.Fatalf("a reply of %d bytes from a source of %d", len(encoded), len(source))
	}
}

func chainOfCalls(links int) string {
	return "package p\n\nvar X = a" + strings.Repeat(".b()", links) + "\n"
}

func TestAShortChainIsNamedAsWritten(t *testing.T) {
	found := matchesIn(t, map[string]string{"a.go": chainOfCalls(2)}, nameEndingInB)

	assertCallees(t, found, "a.b", "a.b().b")
}

func TestAChainPastTheCapKeepsItsLastNameSoAPatternOnTheEndStillMatches(t *testing.T) {
	links := maxPrintedBytes // Four bytes each, so the outer calls are past the cap.

	found := matchesIn(t, map[string]string{"a.go": chainOfCalls(links)}, nameEndingInB)

	if len(found) != links {
		t.Fatalf("every link must still match, got %d of %d", len(found), links)
	}
	// Calls are found from the outside in, so the first is the longest chain and the last the shortest.
	if got, want := found[0].Callee, tooLongToPrint+".b"; got != want {
		t.Fatalf("the longest chain reads %.40q, want %q", got, want)
	}
	if got, want := found[links-1].Callee, "a.b"; got != want {
		t.Fatalf("the shortest chain reads %.40q, want %q", got, want)
	}
}

func TestALongChainGivesAReplyThatGrowsWithTheFile(t *testing.T) {
	source := chainOfCalls(16000)
	root := writeTree(t, map[string]string{"a.go": source})
	patternsFile := writeTree(t, map[string]string{"patterns.json": nameEndingInB}) + "/patterns.json"

	report, err := findMatches(root, patternsFile)

	assertNoError(t, err)
	encoded, err := json.Marshal(report)
	assertNoError(t, err)
	if limit := 60 * len(source); len(encoded) > limit {
		t.Fatalf("a reply of %d bytes from a source of %d", len(encoded), len(source))
	}
}

func TestACallToAVeryLongExpressionThatIsNoChainIsNotPrinted(t *testing.T) {
	source := "package p\n\nvar X = func() int {\n\treturn " + strings.Repeat("1+", maxPrintedBytes) + "1\n}()\n"

	found := matchesIn(t, map[string]string{"a.go": source}, `[{"kind":"param","call":"literal|too long","args":{"name":0}}]`)

	assertCallees(t, found, tooLongToPrint)
}

func assertCallees(t *testing.T, found []matchedCall, want ...string) {
	t.Helper()
	got := make(map[string]bool, len(found))
	for _, match := range found {
		got[match.Callee] = true
	}
	for _, callee := range want {
		if !got[callee] {
			t.Fatalf("no callee reads %q in %v", callee, found)
		}
	}
}

func manyNames(names int) string {
	listed := make([]string, names)
	for index := range listed {
		listed[index] = fmt.Sprintf("F%d", index)
	}
	return strings.Join(listed, ", ")
}

// One type or tag is repeated for every name beside it, so a long one costs its length many times.
func TestAFieldTypePastTheCapIsNotPrintedForEachName(t *testing.T) {
	longType := `[len("` + strings.Repeat("x", maxPrintedBytes) + `")]byte`
	source := "package p\n\ntype T struct {\n\t" + manyNames(3) + " " + longType + "\n}\n"

	fields := fieldsIn(t, source)

	if len(fields) != 3 {
		t.Fatalf("got %d fields", len(fields))
	}
	for _, field := range fields {
		if field.GoType != tooLongToPrint {
			t.Fatalf("got a type of %d bytes", len(field.GoType))
		}
	}
}

func TestAFieldTagPastTheCapIsNotPrintedForEachName(t *testing.T) {
	longTag := "`json:\"" + strings.Repeat("x", maxPrintedBytes) + "\"`"
	source := "package p\n\ntype T struct {\n\t" + manyNames(3) + " int " + longTag + "\n}\n"

	fields := fieldsIn(t, source)

	if len(fields) != 3 {
		t.Fatalf("got %d fields", len(fields))
	}
	for _, field := range fields {
		if field.Tag != tooLongToPrint || field.JSONTag != nil {
			t.Fatalf("got a tag of %d bytes and a json key of %v", len(field.Tag), field.JSONTag)
		}
	}
}

func TestAFieldTagOrTypeUpToTheCapIsKept(t *testing.T) {
	tag := `json:"` + strings.Repeat("x", 300) + `"`
	fieldType := `[len("` + strings.Repeat("y", 300) + `")]byte`
	source := "package p\n\ntype T struct {\n\tF " + fieldType + " `" + tag + "`\n}\n"

	fields := fieldsIn(t, source)

	if fields[0].Tag != tag || fields[0].GoType != fieldType {
		t.Fatalf("got %+v", fields[0])
	}
}

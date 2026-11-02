package main

import (
	"bytes"
	"encoding/json"
	"strings"
	"testing"
)

func runCommand(t *testing.T, args ...string) (exitCode int, stdout, stderr string) {
	t.Helper()
	var out, errOut bytes.Buffer
	exitCode = run(args, &out, &errOut)
	return exitCode, out.String(), errOut.String()
}

func decodeObject(t *testing.T, text string) map[string]any {
	t.Helper()
	var decoded map[string]any
	if err := json.Unmarshal([]byte(text), &decoded); err != nil {
		t.Fatalf("stdout is not one JSON object: %v\n%s", err, text)
	}
	return decoded
}

func TestCompareCommandPrintsTheThreeListsAsJSON(t *testing.T) {
	oldRoot := writeTree(t, map[string]string{"a.go": "package p\nvar x = 1\n", "gone.go": "package p\n"})
	newRoot := writeTree(t, map[string]string{"a.go": "package p\nvar x = 2\n", "new.go": "package p\n"})

	exitCode, stdout, stderr := runCommand(t, "compare", "--old", oldRoot, "--new", newRoot)

	if exitCode != 0 || stderr != "" {
		t.Fatalf("exit %d, stderr %q", exitCode, stderr)
	}
	want := `{"changed":["a.go"],"added":["new.go"],"removed":["gone.go"]}` + "\n"
	if stdout != want {
		t.Fatalf("got %s, want %s", stdout, want)
	}
}

func TestDeclarationsCommandPrintsDeclarationsAndFieldsAsJSON(t *testing.T) {
	root := writeTree(t, map[string]string{"shop.go": "package p\n\ntype Item struct {\n\tName string `json:\"name\"`\n}\n"})

	exitCode, stdout, stderr := runCommand(t, "declarations", root)

	if exitCode != 0 || stderr != "" {
		t.Fatalf("exit %d, stderr %q", exitCode, stderr)
	}
	decoded := decodeObject(t, stdout)
	declarations := decoded["declarations"].([]any)[0].(map[string]any)
	for _, key := range []string{"file", "kind", "name", "hash", "line"} {
		if _, found := declarations[key]; !found {
			t.Errorf("declaration lacks %q: %v", key, declarations)
		}
	}
	fields := decoded["fields"].([]any)[0].(map[string]any)
	for _, key := range []string{"file", "struct", "name", "line", "tag", "go_type", "embedded"} {
		if _, found := fields[key]; !found {
			t.Errorf("field lacks %q: %v", key, fields)
		}
	}
}

func TestOutputKeepsAngleBracketsReadable(t *testing.T) {
	root := writeTree(t, map[string]string{"shop.go": "package p\n\ntype Feed struct {\n\tEvents <-chan int\n}\n"})

	_, stdout, _ := runCommand(t, "declarations", root)

	if !strings.Contains(stdout, `"go_type":"<-chan int"`) {
		t.Fatalf("angle brackets must stay as written, got: %s", stdout)
	}
}

func TestBadInputExitsOneWithAMessageAndNothingOnStdout(t *testing.T) {
	goodRoot := writeTree(t, map[string]string{"a.go": "package p\n"})
	halfBrokenRoot := writeTree(t, map[string]string{"a.go": "package p\nvar fine = 1\n", "z.go": "package p\nfunc ("})
	missing := goodRoot + "/does-not-exist"

	cases := map[string][]string{
		"no command":                  {},
		"unknown command":             {"explode"},
		"compare without arguments":   {"compare"},
		"compare without --new":       {"compare", "--old", goodRoot},
		"compare without --old":       {"compare", "--new", goodRoot},
		"compare with a stray word":   {"compare", "--old", goodRoot, "--new", goodRoot, "extra"},
		"compare with unknown flag":   {"compare", "--old", goodRoot, "--new", goodRoot, "--fast"},
		"compare with missing root":   {"compare", "--old", missing, "--new", goodRoot},
		"declarations without a root": {"declarations"},
		"declarations with two roots": {"declarations", goodRoot, goodRoot},
		"declarations missing root":   {"declarations", missing},
		"declarations, a file that does not parse after one that does": {"declarations", halfBrokenRoot},
	}

	for name, args := range cases {
		exitCode, stdout, stderr := runCommand(t, args...)

		if exitCode != 1 {
			t.Errorf("%s: exit %d, want 1", name, exitCode)
		}
		if stdout != "" {
			t.Errorf("%s: stdout must stay empty, got %q", name, stdout)
		}
		if strings.TrimSpace(stderr) == "" {
			t.Errorf("%s: want a message on stderr", name)
		}
	}
}

func TestParseFailureMessageNamesTheFile(t *testing.T) {
	root := writeTree(t, map[string]string{"pkg/broken.go": "package p\nfunc ("})

	_, _, stderr := runCommand(t, "declarations", root)

	if !strings.Contains(stderr, "pkg/broken.go") {
		t.Fatalf("stderr must name the file, got %q", stderr)
	}
}

package main

import (
	"encoding/json"
	"os"
	"path/filepath"
	"reflect"
	"slices"
	"strings"
	"testing"
)

const emptyPackage = "package p\n"

func TestWalkListsGoFilesWithRelativeSlashPathsSorted(t *testing.T) {
	root := writeTree(t, map[string]string{
		"b.go":         emptyPackage,
		"a.go":         emptyPackage,
		"a/inner.go":   emptyPackage,
		"z/deep/x.go":  emptyPackage,
		"README.md":    "not go",
		"a/notes.text": "not go",
	})

	got, err := listGoFiles(root, skipOnlyGit)

	assertNoError(t, err)
	// "a.go" sorts before "a/inner.go" as a string, though a directory walk visits "a/" first.
	want := []string{"a.go", "a/inner.go", "b.go", "z/deep/x.go"}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("got %v, want %v", got, want)
	}
}

func TestWalkSkipsGitDirectoryUnderEveryPolicy(t *testing.T) {
	root := writeTree(t, map[string]string{
		"keep.go":            emptyPackage,
		".git/hooks/x.go":    emptyPackage,
		"sub/.git/nested.go": emptyPackage,
	})

	for name, policy := range map[string]walkPolicy{
		"git only":             skipOnlyGit,
		"go tool ignored dirs": skipWhatTheGoToolIgnores,
	} {
		got, err := listGoFiles(root, policy)

		assertNoError(t, err)
		if !reflect.DeepEqual(got, []string{"keep.go"}) {
			t.Errorf("%s: got %v, want only keep.go", name, got)
		}
	}
}

func TestWalkSkipsWhatTheGoToolIgnores(t *testing.T) {
	root := writeTree(t, map[string]string{
		"keep.go":              emptyPackage,
		"vendor/lib/lib.go":    emptyPackage,
		"testdata/sample.go":   emptyPackage,
		"pkg/testdata/deep.go": emptyPackage,
		"_scratch/x.go":        emptyPackage,
		".hidden/x.go":         emptyPackage,
		"vendored/kept.go":     emptyPackage,
	})

	got, err := listGoFiles(root, skipWhatTheGoToolIgnores)

	assertNoError(t, err)
	want := []string{"keep.go", "vendored/kept.go"}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("got %v, want %v", got, want)
	}
}

func TestWalkKeepsVendorAndTestdataWhenOnlyGitIsSkipped(t *testing.T) {
	root := writeTree(t, map[string]string{
		"vendor/lib.go":   emptyPackage,
		"testdata/x.go":   emptyPackage,
		"_scratch/x.go":   emptyPackage,
		".hidden/x.go":    emptyPackage,
		".git/ignored.go": emptyPackage,
	})

	got, err := listGoFiles(root, skipOnlyGit)

	assertNoError(t, err)
	want := []string{".hidden/x.go", "_scratch/x.go", "testdata/x.go", "vendor/lib.go"}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("got %v, want %v", got, want)
	}
}

func TestWalkNeverSkipsTheRootItself(t *testing.T) {
	for _, rootName := range []string{"vendor", "_scratch", ".hidden", "testdata"} {
		root := filepath.Join(t.TempDir(), rootName)
		writeFiles(t, root, map[string]string{"x.go": emptyPackage})

		got, err := listGoFiles(root, skipWhatTheGoToolIgnores)

		assertNoError(t, err)
		if !reflect.DeepEqual(got, []string{"x.go"}) {
			t.Errorf("root %q: got %v, want x.go", rootName, got)
		}
	}
}

func TestWalkSkipsFilesThatAreNotRegular(t *testing.T) {
	root := writeTree(t, map[string]string{
		"real.go":   emptyPackage,
		"elsewhere": "unused",
	})
	outside := writeTree(t, map[string]string{"outside.go": emptyPackage})
	linkOrSkip(t, filepath.Join(root, "real.go"), filepath.Join(root, "link_to_file.go"))
	linkOrSkip(t, outside, filepath.Join(root, "link_to_dir"))

	got, err := listGoFiles(root, skipOnlyGit)

	assertNoError(t, err)
	if !reflect.DeepEqual(got, []string{"real.go"}) {
		t.Fatalf("got %v, want only real.go", got)
	}
}

func TestWalkDescendsIntoADirectoryNamedLikeAGoFile(t *testing.T) {
	root := writeTree(t, map[string]string{"generated.go/inner.go": emptyPackage})

	got, err := listGoFiles(root, skipOnlyGit)

	assertNoError(t, err)
	if !reflect.DeepEqual(got, []string{"generated.go/inner.go"}) {
		t.Fatalf("got %v", got)
	}
}

func TestWalkFollowsARootThatIsASymlink(t *testing.T) {
	target := writeTree(t, map[string]string{"x.go": emptyPackage})
	root := filepath.Join(t.TempDir(), "link")
	linkOrSkip(t, target, root)

	got, err := listGoFiles(root, skipOnlyGit)

	assertNoError(t, err)
	if !reflect.DeepEqual(got, []string{"x.go"}) {
		t.Fatalf("a symlinked root must be walked, not reported as empty: got %v", got)
	}
}

func TestWalkReportsAnEmptyDirectoryAsNoFiles(t *testing.T) {
	got, err := listGoFiles(t.TempDir(), skipOnlyGit)

	assertNoError(t, err)
	if got == nil || len(got) != 0 {
		t.Fatalf("want an empty non-nil list, got %#v", got)
	}
}

func TestWalkRejectsARootThatIsMissingOrNotADirectory(t *testing.T) {
	file := filepath.Join(writeTree(t, map[string]string{"x.go": emptyPackage}), "x.go")
	missing := filepath.Join(t.TempDir(), "nope")

	for _, root := range []string{missing, file} {
		if _, err := listGoFiles(root, skipOnlyGit); err == nil {
			t.Errorf("root %q: want an error", root)
		}
	}
}

func linkOrSkip(t *testing.T, target, link string) {
	t.Helper()
	if err := os.Symlink(target, link); err != nil {
		t.Skipf("cannot create symlinks here: %v", err)
	}
}

func TestWalkRejectsAnEmptyRoot(t *testing.T) {
	// An empty path would otherwise scan the current directory and report its files.
	if _, err := listGoFiles("", skipOnlyGit); err == nil {
		t.Fatal("want an error for an empty root")
	}
}

func TestWalkSkipsGoFilesTheGoToolIgnores(t *testing.T) {
	root := writeTree(t, map[string]string{
		"keep.go":          emptyPackage,
		"x_gen.go":         emptyPackage,
		"pkg/keep_test.go": emptyPackage,
		"_gen.go":          emptyPackage,
		".swap.go":         emptyPackage,
		"pkg/_scratch.go":  emptyPackage,
		"pkg/.hidden.go":   emptyPackage,
	})

	got, err := listGoFiles(root, skipWhatTheGoToolIgnores)

	assertNoError(t, err)
	want := []string{"keep.go", "pkg/keep_test.go", "x_gen.go"}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("got %v, want %v", got, want)
	}
}

func TestWalkKeepsGoFilesTheGoToolIgnoresWhenOnlyGitIsSkipped(t *testing.T) {
	root := writeTree(t, map[string]string{
		"keep.go":         emptyPackage,
		"_gen.go":         emptyPackage,
		".swap.go":        emptyPackage,
		"pkg/_scratch.go": emptyPackage,
	})

	got, err := listGoFiles(root, skipOnlyGit)

	assertNoError(t, err)
	want := []string{".swap.go", "_gen.go", "keep.go", "pkg/_scratch.go"}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("got %v, want %v", got, want)
	}
}

func TestWalkSkipsTestFilesOnTopOfWhatTheGoToolIgnores(t *testing.T) {
	root := writeTree(t, map[string]string{
		"keep.go":           emptyPackage,
		"keep_test.go":      emptyPackage,
		"pkg/inner_test.go": emptyPackage,
		"pkg/testing.go":    emptyPackage,
		"vendor/lib.go":     emptyPackage,
		"_gen.go":           emptyPackage,
	})

	got, err := listGoFiles(root, skipTestsAndWhatTheGoToolIgnores)

	assertNoError(t, err)
	want := []string{"keep.go", "pkg/testing.go"}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("got %v, want %v", got, want)
	}
}

func TestWalkSkipsADirectoryWithItsOwnGoModAndNeverTheRoot(t *testing.T) {
	root := writeTree(t, map[string]string{
		"go.mod":          "module example.com/m\n",
		"keep.go":         emptyPackage,
		"sub/ok.go":       emptyPackage,
		"tools/go.mod":    "module example.com/tools\n",
		"tools/x.go":      emptyPackage,
		"tools/deep/y.go": emptyPackage,
	})

	for name, policy := range map[string]walkPolicy{
		"declarations": skipWhatTheGoToolIgnores,
		"match":        skipTestsAndWhatTheGoToolIgnores,
	} {
		got, err := listGoFiles(root, policy)

		assertNoError(t, err)
		if want := []string{"keep.go", "sub/ok.go"}; !reflect.DeepEqual(got, want) {
			t.Errorf("%s: got %v, want %v", name, got, want)
		}
	}
}

func TestWalkKeepsAnEmptyGoModDirectoryNamedGoModWhichIsNotAModule(t *testing.T) {
	root := writeTree(t, map[string]string{
		"keep.go":         emptyPackage,
		"odd/go.mod/z.go": emptyPackage,
	})

	got, err := listGoFiles(root, skipWhatTheGoToolIgnores)

	assertNoError(t, err)
	if want := []string{"keep.go", "odd/go.mod/z.go"}; !reflect.DeepEqual(got, want) {
		t.Fatalf("got %v, want %v", got, want)
	}
}

func TestWalkKeepsNestedModulesWhenOnlyGitIsSkipped(t *testing.T) {
	root := writeTree(t, map[string]string{
		"keep.go":      emptyPackage,
		"tools/go.mod": "module example.com/tools\n",
		"tools/x.go":   emptyPackage,
	})

	got, err := listGoFiles(root, skipOnlyGit)

	assertNoError(t, err)
	if want := []string{"keep.go", "tools/x.go"}; !reflect.DeepEqual(got, want) {
		t.Fatalf("a compare must not miss a file in a nested module: got %v, want %v", got, want)
	}
}

// A file is left out only when its constraint positively requires the tag ignore: the tag alone,
// or an && with the tag on either side. Anything else stays in, because a file that is not read
// is a route or a field that is missing with no message, and one that is read wrongly shows up
// as a finding. In particular a constraint with a "!" is false in an evaluation that sets every
// tag but ignore, and go list builds `!windows` and `ignore || !linux`.
const generatorBody = "package p\n\nfunc Gen() { pick(\"x\") }\n"

var filesByBuildConstraint = map[string]struct {
	content string
	skipped bool
}{
	"go:build ignore":                     {"//go:build ignore\n\n" + generatorBody, true},
	"go:build ignore, no blank line":      {"//go:build ignore\n" + generatorBody, true},
	"go:build ignore && linux":            {"//go:build ignore && linux\n\n" + generatorBody, true},
	"go:build linux && ignore":            {"//go:build linux && ignore\n\n" + generatorBody, true},
	"go:build three terms with ignore":    {"//go:build linux && ignore && !windows\n\n" + generatorBody, true},
	"go:build in parentheses":             {"//go:build (ignore && linux)\n\n" + generatorBody, true},
	"+build ignore":                       {"// +build ignore\n\n" + generatorBody, true},
	"+build ignore with a note":           {"// +build ignore\n// a note\n\n" + generatorBody, true},
	"+build ignore, linux (comma is and)": {"// +build ignore,linux\n\n" + generatorBody, true},
	"+build ignore then a second line":    {"// +build ignore\n// +build linux\n\n" + generatorBody, true},
	"+build linux then ignore":            {"// +build linux\n// +build ignore\n\n" + generatorBody, true},
	"ignore after a doc comment":          {"// Command gen makes tables.\n//\n//go:build ignore\n\n" + generatorBody, true},
	"byte order mark first":               {"\ufeff//go:build ignore\n\n" + generatorBody, true},
	"CRLF line endings":                   {"//go:build ignore\r\n\r\n" + generatorBody, true},

	"not windows":                           {"//go:build !windows\n\n" + generatorBody, false},
	"not cgo":                               {"//go:build !cgo\n\n" + generatorBody, false},
	"not a custom tag":                      {"//go:build !custom\n\n" + generatorBody, false},
	"ignore or not linux":                   {"//go:build ignore || !linux\n\n" + generatorBody, false},
	"ignore or linux":                       {"//go:build ignore || linux\n\n" + generatorBody, false},
	"not ignore":                            {"//go:build !ignore\n\n" + generatorBody, false},
	"linux and not windows":                 {"//go:build linux && !windows\n\n" + generatorBody, false},
	"an or in parentheses and a tag":        {"//go:build (ignore || linux) && !windows\n\n" + generatorBody, false},
	"+build not cgo":                        {"// +build !cgo\n\n" + generatorBody, false},
	"+build ignore linux (space is or)":     {"// +build ignore linux\n\n" + generatorBody, false},
	"go:build wins over +build":             {"//go:build linux\n// +build ignore\n\n" + generatorBody, false},
	"+build without a blank line":           {"// +build ignore\n" + generatorBody, false},
	"after the package clause":              {"package p\n\n//go:build ignore\n\nfunc Gen() { pick(\"x\") }\n", false},
	"go:build ignore after a block comment": {"/* licence */\n//go:build ignore\n\n" + generatorBody, true},
	"go:build ignore after a block comment of several lines": {"/*\n * Licence.\n */\n\n//go:build ignore\n\n" + generatorBody, true},
	"go:build ignore after two block comments":               {"/* a */\n/* b */\n//go:build ignore\n\n" + generatorBody, true},
	"go:build ignore inside a block comment":                 {"/*\n//go:build ignore\n*/\n\n" + generatorBody, false},
	"go:build ignore after a block comment on its line":      {"/* a */ //go:build ignore\n\n" + generatorBody, false},
	"go:build ignore after code":                             {"package p\n/* a */\n//go:build ignore\n\nfunc Gen() { pick(\"x\") }\n", false},
	"+build ignore under a block comment":                    {"/* licence */\n// +build ignore\n\n" + generatorBody, false},
	"a platform":                                             {"//go:build windows\n\n" + generatorBody, false},
	"a custom tag":                                           {"//go:build e2e\n\n" + generatorBody, false},
	"no constraint":                                          {generatorBody, false},
	"a constraint that is malformed":                         {"//go:build (ignore\n\n" + generatorBody, false},
}

func TestWalkSkipsFilesThatRequireTheIgnoreTagAndNoOthers(t *testing.T) {
	for name, scenario := range filesByBuildConstraint {
		root := writeTree(t, map[string]string{"gen.go": scenario.content, "keep.go": emptyPackage})

		got, err := listGoFiles(root, skipWhatTheGoToolIgnores)

		assertNoError(t, err)
		want := []string{"gen.go", "keep.go"}
		if scenario.skipped {
			want = []string{"keep.go"}
		}
		if !reflect.DeepEqual(got, want) {
			t.Errorf("%s: got %v, want %v", name, got, want)
		}
	}
}

func TestDeclarationsAndMatchReadEveryFileThatDoesNotRequireTheIgnoreTag(t *testing.T) {
	for name, scenario := range filesByBuildConstraint {
		root := writeTree(t, map[string]string{"gen.go": scenario.content, "keep.go": emptyPackage})

		declared := slices.Contains(declarationFiles(t, root), "gen.go")
		matched := slices.Contains(matchFiles(t, root), "gen.go")

		if declared == scenario.skipped || matched == scenario.skipped {
			t.Errorf("%s: declarations read it: %v, match read it: %v, but skipped is %v",
				name, declared, matched, scenario.skipped)
		}
	}
}

func TestCompareStillSeesEveryFileWhateverItsConstraint(t *testing.T) {
	for name, scenario := range filesByBuildConstraint {
		edited := strings.Replace(scenario.content, `pick("x")`, `pick("y")`, 1)
		oldRoot := writeTree(t, map[string]string{"gen.go": scenario.content})
		newRoot := writeTree(t, map[string]string{"gen.go": edited})

		exitCode, stdout, stderr := runCommand(t, "compare", "--old", oldRoot, "--new", newRoot)

		if exitCode != 0 || stderr != "" {
			t.Fatalf("%s: exit %d, stderr %q", name, exitCode, stderr)
		}
		if !strings.Contains(stdout, `"changed":["gen.go"]`) {
			t.Errorf("%s: compare must see the edit, got %s", name, stdout)
		}
	}
}

func declarationFiles(t *testing.T, root string) []string {
	t.Helper()
	exitCode, stdout, stderr := runCommand(t, "declarations", root)
	if exitCode != 0 || stderr != "" {
		t.Fatalf("declarations: exit %d, stderr %q", exitCode, stderr)
	}
	var report struct {
		Declarations []struct {
			File string `json:"file"`
		} `json:"declarations"`
	}
	if err := json.Unmarshal([]byte(stdout), &report); err != nil {
		t.Fatalf("not a declarations report: %v", err)
	}
	files := []string{}
	for _, declaration := range report.Declarations {
		files = append(files, declaration.File)
	}
	return files
}

func matchFiles(t *testing.T, root string) []string {
	t.Helper()
	patternsFile := writeTree(t, map[string]string{"patterns.json": oneArgumentPattern}) + "/patterns.json"
	exitCode, stdout, stderr := runCommand(t, "match", "--patterns", patternsFile, root)
	if exitCode != 0 || stderr != "" {
		t.Fatalf("match: exit %d, stderr %q", exitCode, stderr)
	}
	var report struct {
		Matches []struct {
			File string `json:"file"`
		} `json:"matches"`
	}
	if err := json.Unmarshal([]byte(stdout), &report); err != nil {
		t.Fatalf("not a match report: %v", err)
	}
	files := []string{}
	for _, match := range report.Matches {
		files = append(files, match.File)
	}
	return files
}

func TestWalkKeepsPerPlatformFilesAndCustomTagsWhateverTheMachine(t *testing.T) {
	root := writeTree(t, map[string]string{
		"only_windows.go": emptyPackage,
		"only_plan9.go":   emptyPackage,
		"tagged.go":       "//go:build integration\n\npackage p\n",
	})

	got, err := listGoFiles(root, skipWhatTheGoToolIgnores)

	assertNoError(t, err)
	want := []string{"only_plan9.go", "only_windows.go", "tagged.go"}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("got %v, want %v", got, want)
	}
}

func TestWalkKeepsIgnoreTaggedFilesWhenOnlyGitIsSkipped(t *testing.T) {
	root := writeTree(t, map[string]string{
		"gen.go":  "//go:build ignore\n\npackage main\n",
		"keep.go": emptyPackage,
	})

	got, err := listGoFiles(root, skipOnlyGit)

	assertNoError(t, err)
	if want := []string{"gen.go", "keep.go"}; !reflect.DeepEqual(got, want) {
		t.Fatalf("a compare must not miss a changed generator: got %v, want %v", got, want)
	}
}

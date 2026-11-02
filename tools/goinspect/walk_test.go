package main

import (
	"os"
	"path/filepath"
	"reflect"
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

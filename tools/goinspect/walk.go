package main

import (
	"bytes"
	"errors"
	"fmt"
	"go/build/constraint"
	"io/fs"
	"os"
	"path/filepath"
	"sort"
	"strings"
)

// walkPolicy says what a walk leaves out. Directories and files are judged by name alone, and
// separately: a directory name decides whether to descend, a file name whether to list.
type walkPolicy struct {
	skipsDirectory func(name string) bool
	skipsFile      func(name string) bool
	// skipsNestedModules leaves out a directory that holds its own go.mod, because that is
	// another module and `./...` stops at it. The root is never left out.
	skipsNestedModules bool
	// skipsFilesBuiltOnlyWithTheIgnoreTag leaves out a file that no machine builds unless it is
	// asked for -tags ignore, which is how a generator program is kept out of its package.
	skipsFilesBuiltOnlyWithTheIgnoreTag bool
}

// skipOnlyGit is for callers that must not miss a file, such as compare.
var skipOnlyGit = walkPolicy{
	skipsDirectory: func(name string) bool { return name == ".git" },
	skipsFile:      func(string) bool { return false },
}

// skipWhatTheGoToolIgnores mirrors how `go build ./...` treats a tree: it passes over vendor and
// testdata, over any directory or .go file whose name starts with "_" or ".", over a directory
// that is another module, and over a file that only `-tags ignore` builds. A file for one
// platform, or behind any other tag, stays in, because some machine builds it.
var skipWhatTheGoToolIgnores = walkPolicy{
	skipsDirectory: func(name string) bool {
		return name == "vendor" || name == "testdata" || startsWithUnderscoreOrDot(name)
	},
	skipsFile:                           startsWithUnderscoreOrDot,
	skipsNestedModules:                  true,
	skipsFilesBuiltOnlyWithTheIgnoreTag: true,
}

// skipTestsAndWhatTheGoToolIgnores is for callers that read the service itself: a _test.go file
// is never part of it.
var skipTestsAndWhatTheGoToolIgnores = withTestFilesSkipped(skipWhatTheGoToolIgnores)

func withTestFilesSkipped(policy walkPolicy) walkPolicy {
	skipsByName := policy.skipsFile
	policy.skipsFile = func(name string) bool {
		return skipsByName(name) || strings.HasSuffix(name, "_test.go")
	}
	return policy
}

func startsWithUnderscoreOrDot(name string) bool {
	return strings.HasPrefix(name, "_") || strings.HasPrefix(name, ".")
}

// listGoFiles returns the regular .go files under root as sorted, slash-separated paths relative
// to root. The root itself is never skipped, whatever its name.
func listGoFiles(root string, policy walkPolicy) ([]string, error) {
	walkRoot, err := resolveDirectory(root)
	if err != nil {
		return nil, err
	}
	files := []string{}
	err = filepath.WalkDir(walkRoot, func(path string, entry fs.DirEntry, walkErr error) error {
		if walkErr != nil {
			return walkErr
		}
		if entry.IsDir() {
			if path != walkRoot && skipsDirectoryAt(path, entry.Name(), policy) {
				return filepath.SkipDir
			}
			return nil
		}
		if !isListedFile(entry, policy) {
			return nil
		}
		builtOnlyWithIgnore, err := isSkippedForItsBuildConstraint(path, policy)
		if err != nil {
			return err
		}
		if builtOnlyWithIgnore {
			return nil
		}
		relative, err := filepath.Rel(walkRoot, path)
		if err != nil {
			return err
		}
		files = append(files, filepath.ToSlash(relative))
		return nil
	})
	if err != nil {
		return nil, err
	}
	// WalkDir orders entries per directory, so "a/" comes before "a.go". Sorting the final
	// strings gives one order that does not depend on the walk.
	sort.Strings(files)
	return files, nil
}

func skipsDirectoryAt(path, name string, policy walkPolicy) bool {
	return policy.skipsDirectory(name) || (policy.skipsNestedModules && holdsGoMod(path))
}

// holdsGoMod is true for a go.mod file only: a directory that happens to be named go.mod does not
// make its parent a module.
func holdsGoMod(directory string) bool {
	info, err := os.Stat(filepath.Join(directory, "go.mod"))
	return err == nil && info.Mode().IsRegular()
}

func isSkippedForItsBuildConstraint(path string, policy walkPolicy) (bool, error) {
	if !policy.skipsFilesBuiltOnlyWithTheIgnoreTag {
		return false, nil
	}
	content, err := os.ReadFile(path)
	if err != nil {
		return false, err
	}
	return isBuiltOnlyWithTheIgnoreTag(content), nil
}

// isBuiltOnlyWithTheIgnoreTag is true when the file's build constraint is false whatever the
// platform and the other tags are, and true only if the tag "ignore" is. It reads the leading
// comments the way the go command does, and answers false for anything it cannot read, so that
// a doubtful file stays in the inventory.
func isBuiltOnlyWithTheIgnoreTag(content []byte) bool {
	expressions := leadingBuildConstraints(content)
	builtWithoutIgnore := func(tag string) bool { return tag != "ignore" }
	for _, expression := range expressions {
		if !expression.Eval(builtWithoutIgnore) {
			return true
		}
	}
	return false
}

// leadingBuildConstraints returns the //go:build line if the file has one, else its // +build
// lines. Both must come before the package clause with nothing but blank lines and line comments
// above them, and a // +build line counts only if a blank line follows it.
func leadingBuildConstraints(content []byte) []constraint.Expr {
	content = bytes.TrimPrefix(content, []byte("\ufeff"))
	var goBuild constraint.Expr
	var confirmed, pending []constraint.Expr
	for _, raw := range strings.Split(string(content), "\n") {
		line := strings.TrimSpace(raw)
		if line == "" {
			confirmed, pending = append(confirmed, pending...), nil
			continue
		}
		if !strings.HasPrefix(line, "//") {
			break
		}
		if constraint.IsGoBuild(line) && goBuild == nil {
			goBuild = parseConstraint(line)
		} else if constraint.IsPlusBuild(line) {
			if expression := parseConstraint(line); expression != nil {
				pending = append(pending, expression)
			}
		}
	}
	if goBuild != nil {
		return []constraint.Expr{goBuild}
	}
	return confirmed
}

func parseConstraint(line string) constraint.Expr {
	expression, err := constraint.Parse(line)
	if err != nil {
		return nil
	}
	return expression
}

func isListedFile(entry fs.DirEntry, policy walkPolicy) bool {
	return entry.Type().IsRegular() &&
		strings.HasSuffix(entry.Name(), ".go") &&
		!policy.skipsFile(entry.Name())
}

// resolveDirectory follows a symlinked root, which WalkDir would otherwise report as one
// non-directory entry and so list as empty.
func resolveDirectory(root string) (string, error) {
	if root == "" {
		// filepath.EvalSymlinks turns "" into the current directory, so an unset path variable
		// on the caller's side would report the wrong tree.
		return "", errors.New("the directory path is empty; pass the directory to read")
	}
	resolved, err := filepath.EvalSymlinks(root)
	if err != nil {
		return "", err
	}
	info, err := os.Stat(resolved)
	if err != nil {
		return "", err
	}
	if !info.IsDir() {
		return "", fmt.Errorf("%s is not a directory", root)
	}
	return resolved, nil
}

func readGoFile(root, relativePath string) ([]byte, error) {
	return os.ReadFile(filepath.Join(root, filepath.FromSlash(relativePath)))
}

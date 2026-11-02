package main

import (
	"bytes"
	"errors"
	"fmt"
	"go/build/constraint"
	"io/fs"
	"os"
	"path/filepath"
	"slices"
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
	// skipsFilesThatRequireTheIgnoreTag leaves out a file whose build constraint requires the tag
	// ignore, which is how a generator program is kept out of its package. See
	// requiresTheIgnoreTag for what counts.
	skipsFilesThatRequireTheIgnoreTag bool
}

// skipOnlyGit is for callers that must not miss a file, such as compare.
var skipOnlyGit = walkPolicy{
	skipsDirectory: func(name string) bool { return name == ".git" },
	skipsFile:      func(string) bool { return false },
}

// skipWhatTheGoToolIgnores mirrors how `go build ./...` treats a tree: it passes over vendor and
// testdata, over any directory or .go file whose name starts with "_" or ".", over a directory
// that is another module, and over a file whose build constraint requires the tag ignore. Any
// other file stays in, a file for one platform and one behind a negated tag included, because
// some machine builds it.
var skipWhatTheGoToolIgnores = walkPolicy{
	skipsDirectory: func(name string) bool {
		return name == "vendor" || name == "testdata" || startsWithUnderscoreOrDot(name)
	},
	skipsFile:                         startsWithUnderscoreOrDot,
	skipsNestedModules:                true,
	skipsFilesThatRequireTheIgnoreTag: true,
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
		requiresIgnore, err := skipsForItsBuildConstraint(path, policy)
		if err != nil {
			return err
		}
		if requiresIgnore {
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

func skipsForItsBuildConstraint(path string, policy walkPolicy) (bool, error) {
	if !policy.skipsFilesThatRequireTheIgnoreTag {
		return false, nil
	}
	content, err := os.ReadFile(path)
	if err != nil {
		return false, err
	}
	return constraintsRequireTheIgnoreTag(content), nil
}

// constraintsRequireTheIgnoreTag is true when the file's leading constraints, read the way the go
// command reads them, include one that requires the tag ignore. The lines of a // +build block
// are ANDed, so one of them is enough. A file with no constraint, or one this cannot read, is false,
// so that a doubtful file stays in the inventory.
func constraintsRequireTheIgnoreTag(content []byte) bool {
	return slices.ContainsFunc(leadingBuildConstraints(content), requiresTheIgnoreTag)
}

// requiresTheIgnoreTag is true for the tag ignore itself and for an && that has it on either side.
// The expression is not evaluated, because "ignore || !linux" and "!windows" are false when every
// tag but ignore is set, and a go build of either includes the file. Anything with a ! or an ||
// is therefore false here, and the file stays in.
func requiresTheIgnoreTag(expression constraint.Expr) bool {
	switch typed := expression.(type) {
	case *constraint.TagExpr:
		return typed.Tag == "ignore"
	case *constraint.AndExpr:
		return requiresTheIgnoreTag(typed.X) || requiresTheIgnoreTag(typed.Y)
	}
	return false
}

// leadingBuildConstraints reads the top of a file the way go/build does. A //go:build line counts
// anywhere before the first line of code, whatever block comments sit above it. A // +build line
// counts only among the line comments and blank lines at the very top, before the first block
// comment or code, and only if a blank line follows it. The //go:build line wins if both exist.
func leadingBuildConstraints(content []byte) []constraint.Expr {
	content = bytes.TrimPrefix(content, []byte("\ufeff"))
	var goBuild constraint.Expr
	var confirmed, pending []constraint.Expr
	headerEnded, inBlockComment := false, false
	for _, raw := range strings.Split(string(content), "\n") {
		line := strings.TrimSpace(raw)
		if line == "" && !headerEnded {
			confirmed, pending = append(confirmed, pending...), nil
			continue
		}
		if !strings.HasPrefix(line, "//") {
			headerEnded = true
		}
		if !inBlockComment {
			goBuild, pending = readConstraintLine(line, goBuild, pending, headerEnded)
		}
		var reachedCode bool
		inBlockComment, reachedCode = afterComments(line, inBlockComment)
		if reachedCode {
			break
		}
	}
	if goBuild != nil {
		return []constraint.Expr{goBuild}
	}
	return confirmed
}

func readConstraintLine(
	line string, goBuild constraint.Expr, pending []constraint.Expr, headerEnded bool,
) (constraint.Expr, []constraint.Expr) {
	if constraint.IsGoBuild(line) && goBuild == nil {
		return parseConstraint(line), pending
	}
	if !headerEnded && constraint.IsPlusBuild(line) {
		if expression := parseConstraint(line); expression != nil {
			pending = append(pending, expression)
		}
	}
	return goBuild, pending
}

// afterComments skips the comments at the start of the line, and says whether a block comment is
// still open at its end and whether code came first.
func afterComments(line string, inBlockComment bool) (stillInBlock, reachedCode bool) {
	for line != "" {
		switch {
		case inBlockComment:
			end := strings.Index(line, "*/")
			if end < 0 {
				return true, false
			}
			inBlockComment = false
			line = strings.TrimSpace(line[end+2:])
		case strings.HasPrefix(line, "//"):
			return false, false
		case strings.HasPrefix(line, "/*"):
			inBlockComment = true
			line = strings.TrimSpace(line[2:])
		default:
			return false, true
		}
	}
	return inBlockComment, false
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

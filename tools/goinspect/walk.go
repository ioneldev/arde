package main

import (
	"errors"
	"fmt"
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
}

// skipOnlyGit is for callers that must not miss a file, such as compare.
var skipOnlyGit = walkPolicy{
	skipsDirectory: func(name string) bool { return name == ".git" },
	skipsFile:      func(string) bool { return false },
}

// skipWhatTheGoToolIgnores mirrors how `go build ./...` treats a tree: it passes over vendor and
// testdata, and over any directory or .go file whose name starts with "_" or ".".
var skipWhatTheGoToolIgnores = walkPolicy{
	skipsDirectory: func(name string) bool {
		return name == "vendor" || name == "testdata" || startsWithUnderscoreOrDot(name)
	},
	skipsFile: startsWithUnderscoreOrDot,
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
			if path != walkRoot && policy.skipsDirectory(entry.Name()) {
				return filepath.SkipDir
			}
			return nil
		}
		if !isListedFile(entry, policy) {
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

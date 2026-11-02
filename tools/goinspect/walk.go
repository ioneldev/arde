package main

import (
	"fmt"
	"io/fs"
	"os"
	"path/filepath"
	"sort"
	"strings"
)

// directoryFilter says whether the walk should skip a directory, given its name alone.
type directoryFilter func(name string) bool

func skipGitDirectory(name string) bool {
	return name == ".git"
}

// skipDirectoriesTheGoToolIgnores mirrors how `go build ./...` treats a tree.
func skipDirectoriesTheGoToolIgnores(name string) bool {
	return name == "vendor" ||
		name == "testdata" ||
		strings.HasPrefix(name, "_") ||
		strings.HasPrefix(name, ".")
}

// listGoFiles returns the regular .go files under root as sorted, slash-separated paths relative
// to root. The root itself is never skipped, whatever its name.
func listGoFiles(root string, skipDirectory directoryFilter) ([]string, error) {
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
			if path != walkRoot && skipDirectory(entry.Name()) {
				return filepath.SkipDir
			}
			return nil
		}
		if !entry.Type().IsRegular() || !strings.HasSuffix(entry.Name(), ".go") {
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

// resolveDirectory follows a symlinked root, which WalkDir would otherwise report as one
// non-directory entry and so list as empty.
func resolveDirectory(root string) (string, error) {
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

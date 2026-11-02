package main

import "bytes"

type compareReport struct {
	Changed []string `json:"changed"`
	Added   []string `json:"added"`
	Removed []string `json:"removed"`
}

type treePair struct {
	oldRoot string
	newRoot string
}

// compareDirs may report a change that is not one, and never misses a change to code. Files with
// the same relative path are compared as normalised syntax trees, and by raw bytes when either
// side does not parse. It looks at regular .go files only, and at comments not at all: a directive
// comment such as //go:build or //go:embed changes a build, and the caller must check those itself.
func compareDirs(oldRoot, newRoot string) (compareReport, error) {
	oldFiles, err := listGoFiles(oldRoot, skipOnlyGit)
	if err != nil {
		return compareReport{}, err
	}
	newFiles, err := listGoFiles(newRoot, skipOnlyGit)
	if err != nil {
		return compareReport{}, err
	}
	trees := treePair{oldRoot: oldRoot, newRoot: newRoot}
	changed, err := trees.changedAmong(filesInBoth(oldFiles, newFiles))
	if err != nil {
		return compareReport{}, err
	}
	return compareReport{
		Changed: changed,
		Added:   filesMissingFrom(newFiles, oldFiles),
		Removed: filesMissingFrom(oldFiles, newFiles),
	}, nil
}

func (trees treePair) changedAmong(paths []string) ([]string, error) {
	changed := []string{}
	for _, path := range paths {
		isChanged, err := trees.fileChanged(path)
		if err != nil {
			return nil, err
		}
		if isChanged {
			changed = append(changed, path)
		}
	}
	return changed, nil
}

func (trees treePair) fileChanged(path string) (bool, error) {
	oldSource, err := readGoFile(trees.oldRoot, path)
	if err != nil {
		return false, err
	}
	newSource, err := readGoFile(trees.newRoot, path)
	if err != nil {
		return false, err
	}
	oldPrint, oldParseErr := normalisedFingerprint(path, oldSource)
	newPrint, newParseErr := normalisedFingerprint(path, newSource)
	if oldParseErr != nil || newParseErr != nil {
		return !bytes.Equal(oldSource, newSource), nil
	}
	return oldPrint != newPrint, nil
}

func filesInBoth(files, others []string) []string {
	present := setOf(others)
	return keepFiles(files, func(path string) bool { return present[path] })
}

func filesMissingFrom(files, others []string) []string {
	present := setOf(others)
	return keepFiles(files, func(path string) bool { return !present[path] })
}

func keepFiles(files []string, keep func(path string) bool) []string {
	kept := []string{}
	for _, path := range files {
		if keep(path) {
			kept = append(kept, path)
		}
	}
	return kept
}

func setOf(values []string) map[string]bool {
	set := make(map[string]bool, len(values))
	for _, value := range values {
		set[value] = true
	}
	return set
}

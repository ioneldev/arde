package main

import (
	"bytes"
	"cmp"
	"encoding/json"
	"errors"
	"fmt"
	"go/ast"
	"go/printer"
	"go/types"
	"io"
	"maps"
	"os"
	"regexp"
	"slices"
	"unicode/utf8"
)

const maxCallPatternLength = 500

// patternSpec is one entry of the profile's patterns file, as written.
type patternSpec struct {
	Kind string         `json:"kind"`
	Call string         `json:"call"`
	Args map[string]int `json:"args"`
}

// callPattern is a patternSpec that passed validation, with its regex compiled once.
type callPattern struct {
	kind              string
	call              *regexp.Regexp
	argumentPositions map[string]int
}

type callMatch struct {
	Kind string            `json:"kind"`
	Call string            `json:"call"`
	File string            `json:"file"`
	Line int               `json:"line"`
	Args map[string]string `json:"args"`
}

type matchReport struct {
	Matches []callMatch `json:"matches"`
}

// findMatches reads every pattern before it opens a Go file, so a bad pattern stops the run even
// in a tree that holds no calls to match.
func findMatches(root, patternsPath string) (matchReport, error) {
	patterns, err := loadPatterns(patternsPath)
	if err != nil {
		return matchReport{}, err
	}
	files, err := listGoFiles(root, skipTestsAndWhatTheGoToolIgnores)
	if err != nil {
		return matchReport{}, err
	}
	report := matchReport{Matches: []callMatch{}}
	for _, path := range files {
		found, err := matchesInFile(root, path, patterns)
		if err != nil {
			return matchReport{}, err
		}
		report.Matches = append(report.Matches, found...)
	}
	sortMatches(report.Matches)
	return report, nil
}

func loadPatterns(path string) ([]callPattern, error) {
	content, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	specs, err := decodePatternSpecs(content)
	if err != nil {
		return nil, fmt.Errorf("patterns file %s: %w; it must be a JSON array of objects with kind, call and args", path, err)
	}
	patterns := make([]callPattern, 0, len(specs))
	for index, spec := range specs {
		pattern, err := compilePattern(spec)
		if err != nil {
			return nil, fmt.Errorf("patterns file %s: pattern %d (kind %q): %w; fix that pattern and run again", path, index+1, spec.Kind, err)
		}
		patterns = append(patterns, pattern)
	}
	return patterns, nil
}

// decodePatternSpecs refuses what would otherwise read as fewer patterns than the file holds: a
// field it does not know (a misspelt "args" would decode as no arguments), anything after the
// array, and a file that holds null.
func decodePatternSpecs(content []byte) ([]patternSpec, error) {
	decoder := json.NewDecoder(bytes.NewReader(content))
	decoder.DisallowUnknownFields()
	var specs []patternSpec
	if err := decoder.Decode(&specs); err != nil {
		return nil, err
	}
	if specs == nil {
		return nil, errors.New("the file holds null")
	}
	if _, err := decoder.Token(); err != io.EOF {
		return nil, errors.New("there is content after the array")
	}
	return specs, nil
}

func compilePattern(spec patternSpec) (callPattern, error) {
	if spec.Kind == "" {
		return callPattern{}, errors.New("kind is empty")
	}
	if spec.Call == "" {
		return callPattern{}, errors.New("call is empty, and an empty regex matches every call")
	}
	if spec.Args == nil {
		return callPattern{}, errors.New("args is missing or null; write {} for a call whose arguments do not matter")
	}
	if length := utf8.RuneCountInString(spec.Call); length > maxCallPatternLength {
		return callPattern{}, fmt.Errorf("call is %d characters long, and the limit is %d", length, maxCallPatternLength)
	}
	call, err := regexp.Compile(spec.Call)
	if err != nil {
		return callPattern{}, fmt.Errorf("call does not compile: %w", err)
	}
	for _, name := range slices.Sorted(maps.Keys(spec.Args)) {
		if spec.Args[name] < 0 {
			return callPattern{}, fmt.Errorf("args[%q] is %d, and an argument position cannot be negative", name, spec.Args[name])
		}
	}
	return callPattern{kind: spec.Kind, call: call, argumentPositions: spec.Args}, nil
}

func matchesInFile(root, path string, patterns []callPattern) ([]callMatch, error) {
	content, err := readGoFile(root, path)
	if err != nil {
		return nil, err
	}
	source, err := parseSource(path, content)
	if err != nil {
		return nil, fmt.Errorf(
			"cannot parse %s, which would leave its calls out of the inventory; fix the syntax error and run again: %w",
			path, err)
	}
	matcher := fileMatcher{path: path, source: source, patterns: patterns}
	found := []callMatch{}
	ast.Inspect(source.syntax, func(node ast.Node) bool {
		if call, isCall := node.(*ast.CallExpr); isCall {
			found = append(found, matcher.matchesOf(call)...)
		}
		return true
	})
	return found, nil
}

type fileMatcher struct {
	path     string
	source   *parsedSource
	patterns []callPattern
}

// nameOfCallee writes the callee on one line, without the parentheses and type arguments around
// it. A split `mux.\n HandleFunc`, `reply[int]` and `(reply)` then all read as the name
// a pattern is written against. Without type information `handlers[0](w)` looks the same as an
// instantiation, so it reads as `handlers`.
func nameOfCallee(callee ast.Expr) string {
	for {
		switch inner := ast.Unparen(callee).(type) {
		case *ast.IndexExpr:
			callee = inner.X
		case *ast.IndexListExpr:
			callee = inner.X
		default:
			return types.ExprString(inner)
		}
	}
}

func (matcher fileMatcher) matchesOf(call *ast.CallExpr) []callMatch {
	callee := nameOfCallee(call.Fun)
	matches := []callMatch{}
	for _, pattern := range matcher.patterns {
		if pattern.call.MatchString(callee) {
			matches = append(matches, callMatch{
				Kind: pattern.kind,
				Call: callee,
				File: matcher.path,
				// The call's own parenthesis, because a chained call starts on the line of the chain.
				Line: matcher.source.lineOf(call.Lparen),
				Args: matcher.source.argumentTexts(call, pattern.argumentPositions),
			})
		}
	}
	return matches
}

// argumentTexts leaves out a name whose position is past the last argument, and the caller
// decides what a missing name means.
func (source *parsedSource) argumentTexts(call *ast.CallExpr, positions map[string]int) map[string]string {
	texts := map[string]string{}
	for name, position := range positions {
		if position < len(call.Args) {
			texts[name] = source.textOf(call.Args[position])
		}
	}
	return texts
}

func (source *parsedSource) textOf(expression ast.Expr) string {
	var text bytes.Buffer
	if err := printer.Fprint(&text, source.fileSet, expression); err != nil {
		panic(fmt.Sprintf("printing an expression into memory cannot fail: %v", err))
	}
	return text.String()
}

func sortMatches(matches []callMatch) {
	slices.SortStableFunc(matches, func(a, b callMatch) int {
		return cmp.Or(cmp.Compare(a.File, b.File), cmp.Compare(a.Line, b.Line), cmp.Compare(a.Kind, b.Kind))
	})
}

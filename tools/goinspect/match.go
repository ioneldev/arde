package main

import (
	"bytes"
	"cmp"
	"encoding/json"
	"errors"
	"fmt"
	"go/ast"
	"go/printer"
	"go/token"
	"go/types"
	"io"
	"maps"
	"os"
	"regexp"
	"slices"
	"strconv"
	"unicode/utf8"
)

const maxCallPatternLength = 500

// maxPrintedBytes is how much source an expression may span and still be printed. Printing every
// level of a nested call costs the square of the file's size, and all ARDE reads from this text is
// a plain name or the first words of a status.
const maxPrintedBytes = 1024

// tooLongToPrint stands in a reply for an expression past that size.
const tooLongToPrint = "(too long to print)"

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
	// Literals holds the value of each argument that is one string literal, decoded as the
	// compiler decodes it. An argument that is anything else is in Args only.
	Literals map[string]string `json:"literals"`
}

// patternName says which pattern is meant. Two kinds can share a regex.
type patternName struct {
	Kind string `json:"kind"`
	Call string `json:"call"`
}

// matchReport lists a pattern that matched nothing, because a profile with a dead pattern otherwise
// looks the same as a repo with none of those calls.
type matchReport struct {
	Matches           []callMatch   `json:"matches"`
	UnmatchedPatterns []patternName `json:"unmatched_patterns"`
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
	report.UnmatchedPatterns = patternsWithNoMatch(patterns, report.Matches)
	return report, nil
}

func patternsWithNoMatch(patterns []callPattern, matches []callMatch) []patternName {
	unmatched := []patternName{}
	for _, pattern := range patterns {
		matched := slices.ContainsFunc(matches, func(match callMatch) bool {
			return match.Kind == pattern.kind && pattern.call.MatchString(match.Call)
		})
		if !matched {
			unmatched = append(unmatched, patternName{Kind: pattern.kind, Call: pattern.call.String()})
		}
	}
	return unmatched
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
			return boundedCalleeName(inner)
		}
	}
}

// boundedCalleeName keeps the last name of a chain too long to print, so that a pattern on the end
// of the chain still finds the call.
func boundedCalleeName(callee ast.Expr) string {
	selector, isSelector := callee.(*ast.SelectorExpr)
	if isSelector && isTooLongToPrint(callee) {
		return tooLongToPrint + "." + selector.Sel.Name
	}
	return boundedExprString(callee)
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
				Line:     matcher.source.lineOf(call.Lparen),
				Args:     matcher.source.argumentTexts(call, pattern.argumentPositions),
				Literals: literalValues(call, pattern.argumentPositions),
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

// literalValues leaves out a name whose argument is not one string literal: a name, a sum, a
// call and a parenthesised literal all have no value to read without evaluating code.
func literalValues(call *ast.CallExpr, positions map[string]int) map[string]string {
	values := map[string]string{}
	for name, position := range positions {
		if position >= len(call.Args) {
			continue
		}
		literal, isLiteral := call.Args[position].(*ast.BasicLit)
		if !isLiteral || literal.Kind != token.STRING {
			continue
		}
		// The scanner has already accepted the literal, so Unquote is not expected to fail.
		if value, err := strconv.Unquote(literal.Value); err == nil {
			values[name] = value
		}
	}
	return values
}

func (source *parsedSource) textOf(expression ast.Expr) string {
	if isTooLongToPrint(expression) {
		return tooLongToPrint
	}
	var text bytes.Buffer
	if err := printer.Fprint(&text, source.fileSet, expression); err != nil {
		panic(fmt.Sprintf("printing an expression into memory cannot fail: %v", err))
	}
	return text.String()
}

func boundedExprString(expression ast.Expr) string {
	if isTooLongToPrint(expression) {
		return tooLongToPrint
	}
	return types.ExprString(expression)
}

func isTooLongToPrint(expression ast.Expr) bool {
	return int(expression.End()-expression.Pos()) > maxPrintedBytes
}

func sortMatches(matches []callMatch) {
	slices.SortStableFunc(matches, func(a, b callMatch) int {
		return cmp.Or(cmp.Compare(a.File, b.File), cmp.Compare(a.Line, b.Line), cmp.Compare(a.Kind, b.Kind))
	})
}

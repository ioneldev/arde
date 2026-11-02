// goinspect answers the questions about Go source that ARDE's Python side cannot, because the
// parser lives in Go's standard library. Every command prints one JSON document on stdout. A
// failure prints a message on stderr, exits 1 and leaves stdout empty.
package main

import (
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"os"
	"slices"
	"strings"
)

const usage = "usage: goinspect compare --old DIR --new DIR | goinspect declarations DIR | " +
	"goinspect match --patterns FILE DIR | goinspect fingerprints --roots a,b DIR"

type command func(args []string) (any, error)

var commands = map[string]command{
	"compare":      runCompare,
	"declarations": runDeclarations,
	"fingerprints": runFingerprints,
	"match":        runMatch,
}

func main() {
	os.Exit(run(os.Args[1:], os.Stdout, os.Stderr))
}

// run writes to stdout only after the command has finished, so a failure part way never leaves
// half a document behind.
func run(args []string, stdout, stderr io.Writer) int {
	result, err := execute(args)
	if err == nil {
		err = writeJSON(stdout, result)
	}
	if err != nil {
		fmt.Fprintf(stderr, "goinspect: %v\n", err)
		return 1
	}
	return 0
}

func execute(args []string) (any, error) {
	if len(args) == 0 {
		return nil, errors.New(usage)
	}
	handler, known := commands[args[0]]
	if !known {
		return nil, fmt.Errorf("unknown command %q; %s", args[0], usage)
	}
	return handler(args[1:])
}

func runCompare(args []string) (any, error) {
	flags := newFlagSet("compare")
	oldRoot := flags.String("old", "", "directory holding the tree before the change")
	newRoot := flags.String("new", "", "directory holding the tree after the change")
	if err := parseFlags(flags, args); err != nil {
		return nil, err
	}
	if *oldRoot == "" || *newRoot == "" || flags.NArg() != 0 {
		return nil, errors.New(usage)
	}
	return compareDirs(*oldRoot, *newRoot)
}

func runMatch(args []string) (any, error) {
	flags := newFlagSet("match")
	patternsPath := flags.String("patterns", "", "JSON file holding the call patterns")
	if err := parseFlags(flags, args); err != nil {
		return nil, err
	}
	if *patternsPath == "" || flags.NArg() != 1 {
		return nil, errors.New(usage)
	}
	return findMatches(flags.Arg(0), *patternsPath)
}

func runFingerprints(args []string) (any, error) {
	flags := newFlagSet("fingerprints")
	rootsText := flags.String("roots", "", "comma-separated names of the functions or methods to fingerprint")
	if err := parseFlags(flags, args); err != nil {
		return nil, err
	}
	if flags.NArg() != 1 {
		return nil, errors.New(usage)
	}
	roots, err := splitRoots(*rootsText)
	if err != nil {
		return nil, err
	}
	return fingerprintRoots(flags.Arg(0), roots)
}

func splitRoots(rootsText string) ([]string, error) {
	roots := strings.Split(rootsText, ",")
	if slices.Contains(roots, "") {
		return nil, fmt.Errorf("--roots %q holds an empty name; pass function or method names separated by commas", rootsText)
	}
	return roots, nil
}

func newFlagSet(command string) *flag.FlagSet {
	flags := flag.NewFlagSet(command, flag.ContinueOnError)
	flags.SetOutput(io.Discard)
	return flags
}

func parseFlags(flags *flag.FlagSet, args []string) error {
	if err := flags.Parse(args); err != nil {
		return fmt.Errorf("%v; %s", err, usage)
	}
	return nil
}

func runDeclarations(args []string) (any, error) {
	if len(args) != 1 {
		return nil, errors.New(usage)
	}
	return inspectDeclarations(args[0])
}

// writeJSON keeps < and > as written. Python decodes the escaped form too, so this only keeps a
// type such as <-chan int readable in a trace. Encode marshals the whole value before it writes,
// so a value that cannot be encoded leaves stdout empty.
func writeJSON(writer io.Writer, value any) error {
	encoder := json.NewEncoder(writer)
	encoder.SetEscapeHTML(false)
	return encoder.Encode(value)
}

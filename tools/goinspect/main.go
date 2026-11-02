// goinspect answers the questions about Go source that ARDE's Python side cannot, because the
// parser lives in Go's standard library. Every command prints one JSON document on stdout. A
// failure prints a message on stderr and leaves stdout empty. It exits 1, or 2 when what the
// profile gave it is to blame.
package main

import (
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"os"
)

const usage = "usage: goinspect compare --old DIR --new DIR | goinspect declarations DIR | " +
	"goinspect match --patterns FILE DIR | goinspect routes --patterns FILE --main PKG DIR | " +
	"goinspect fingerprints --roots FILE --main PKG DIR"

// exitProfileProblem is the exit code of a failure that a better profile would avoid. The Go runtime
// exits 2 on a panic as well, so the client also reads the start of the message.
const exitProfileProblem = 2

// profileProblem is a failure of what the profile gave goinspect. The text before its cause is part
// of the contract with the client, which tells the two subjects apart by it.
type profileProblem struct {
	subject string
	cause   error
}

func (problem profileProblem) Error() string {
	return "bad " + problem.subject + ": " + problem.cause.Error()
}

func (problem profileProblem) Unwrap() error {
	return problem.cause
}

func badPatterns(cause error) error {
	return profileProblem{subject: "patterns", cause: cause}
}

func badMainPackage(cause error) error {
	return profileProblem{subject: "main_package", cause: cause}
}

type command func(args []string) (any, error)

var commands = map[string]command{
	"compare":      runCompare,
	"declarations": runDeclarations,
	"fingerprints": runFingerprints,
	"match":        runMatch,
	"routes":       runRoutes,
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
		return exitCodeOf(err)
	}
	return 0
}

func exitCodeOf(err error) int {
	var problem profileProblem
	if errors.As(err, &problem) {
		return exitProfileProblem
	}
	return 1
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

func runRoutes(args []string) (any, error) {
	flags := newFlagSet("routes")
	patternsPath := flags.String("patterns", "", "JSON file holding the call patterns")
	mainPackage := flags.String("main", "", "the package that holds func main, as go list reads it")
	if err := parseFlags(flags, args); err != nil {
		return nil, err
	}
	if *patternsPath == "" || *mainPackage == "" || flags.NArg() != 1 {
		return nil, errors.New(usage)
	}
	return findRoutes(routeRequest{dir: flags.Arg(0), patternsPath: *patternsPath, mainPackage: *mainPackage})
}

func runFingerprints(args []string) (any, error) {
	flags := newFlagSet("fingerprints")
	rootsPath := flags.String("roots", "", "JSON file holding the ids of the functions to fingerprint")
	mainPackage := flags.String("main", "", "the package that holds func main, as go list reads it")
	if err := parseFlags(flags, args); err != nil {
		return nil, err
	}
	if *rootsPath == "" || *mainPackage == "" || flags.NArg() != 1 {
		return nil, errors.New(usage)
	}
	roots, err := loadRoots(*rootsPath)
	if err != nil {
		return nil, err
	}
	return fingerprintRoots(fingerprintRequest{dir: flags.Arg(0), mainPackage: *mainPackage, roots: roots})
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

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
)

const usage = "usage: goinspect compare --old DIR --new DIR | goinspect declarations DIR"

type command func(args []string) (any, error)

var commands = map[string]command{
	"compare":      runCompare,
	"declarations": runDeclarations,
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
	flags := flag.NewFlagSet("compare", flag.ContinueOnError)
	flags.SetOutput(io.Discard)
	oldRoot := flags.String("old", "", "directory holding the tree before the change")
	newRoot := flags.String("new", "", "directory holding the tree after the change")
	if err := flags.Parse(args); err != nil {
		return nil, fmt.Errorf("%v; %s", err, usage)
	}
	if *oldRoot == "" || *newRoot == "" || flags.NArg() != 0 {
		return nil, errors.New(usage)
	}
	return compareDirs(*oldRoot, *newRoot)
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

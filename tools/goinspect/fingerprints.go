package main

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"fmt"
	"go/types"
	"os"
	"path/filepath"
	"slices"
	"strings"

	"golang.org/x/tools/go/packages"
	"golang.org/x/tools/go/ssa"
	"golang.org/x/tools/go/ssa/ssautil"
)

type fingerprintReport struct {
	Fingerprints map[string]string   `json:"fingerprints"`
	Reach        map[string][]string `json:"reach"`
	Unresolved   []string            `json:"unresolved"`
}

// moduleProgram is the module's own packages as SSA. Only their function bodies are built; the
// packages they import come from export data and have none.
type moduleProgram struct {
	modulePath     string
	program        *ssa.Program
	sourcePackages []*ssa.Package
}

// fingerprintRoots hashes, for each named function, the SSA of everything it can reach by static
// calls inside the module. It sees function bodies only: a package-level var, a type or a const
// that a function uses is not part of its fingerprint, and `declarations` covers those.
func fingerprintRoots(dir string, roots []string) (fingerprintReport, error) {
	module, err := loadModuleProgram(dir)
	if err != nil {
		return fingerprintReport{}, err
	}
	report := fingerprintReport{
		Fingerprints: map[string]string{},
		Reach:        map[string][]string{},
		Unresolved:   []string{},
	}
	declared := module.declaredFunctionsByName()
	for _, root := range slices.Compact(slices.Sorted(slices.Values(roots))) {
		starts := declared[root]
		if len(starts) == 0 {
			report.Unresolved = append(report.Unresolved, root)
			continue
		}
		reached := module.reachFrom(starts)
		report.Fingerprints[root] = hashFunctions(reached)
		report.Reach[root] = namesOf(reached)
	}
	return report, nil
}

func loadModuleProgram(dir string) (moduleProgram, error) {
	resolved, err := resolveDirectory(dir)
	if err != nil {
		return moduleProgram{}, err
	}
	if err := requireGoMod(resolved); err != nil {
		return moduleProgram{}, err
	}
	loaded, err := loadPackages(resolved)
	if err != nil {
		return moduleProgram{}, err
	}
	modulePath, err := modulePathOf(loaded)
	if err != nil {
		return moduleProgram{}, err
	}
	program, ssaPackages := ssautil.Packages(loaded, ssa.BuilderMode(0))
	program.Build()
	return moduleProgram{modulePath: modulePath, program: program, sourcePackages: ssaPackages}, nil
}

func requireGoMod(dir string) error {
	_, err := os.Stat(filepath.Join(dir, "go.mod"))
	if errors.Is(err, os.ErrNotExist) {
		return fmt.Errorf("%s has no go.mod; pass the directory that holds the module's go.mod", dir)
	}
	return err
}

// loadPackages checks each package's Errors as well as packages.Load's own error, because Load
// reports a package that does not compile as data and returns no error at all.
func loadPackages(dir string) ([]*packages.Package, error) {
	config := &packages.Config{Mode: packages.LoadSyntax | packages.NeedModule, Dir: dir}
	loaded, err := packages.Load(config, "./...")
	if err != nil {
		return nil, fmt.Errorf("cannot load the packages under %s: %w", dir, err)
	}
	if problems := loadProblems(loaded); len(problems) > 0 {
		return nil, fmt.Errorf(
			"the packages under %s do not load, and a call graph built on them would leave functions out; fix these and run again:\n%s",
			dir, strings.Join(problems, "\n"))
	}
	if len(loaded) == 0 {
		return nil, fmt.Errorf("no Go package under %s; pass the directory that holds the module's code", dir)
	}
	return loaded, nil
}

func loadProblems(loaded []*packages.Package) []string {
	problems := []string{}
	packages.Visit(loaded, nil, func(loadedPackage *packages.Package) {
		for _, problem := range loadedPackage.Errors {
			problems = append(problems, problem.Error())
		}
	})
	slices.Sort(problems)
	return slices.Compact(problems)
}

func modulePathOf(loaded []*packages.Package) (string, error) {
	for _, loadedPackage := range loaded {
		if loadedPackage.Module != nil {
			return loadedPackage.Module.Path, nil
		}
	}
	return "", errors.New("the go command named no module for these packages; check the module line in go.mod")
}

// declaredFunctionsByName keys the module's declared functions and methods by their bare name.
// It leaves out generated wrappers: a root `init` must find the init functions someone wrote,
// not the package initialiser the compiler makes.
func (module moduleProgram) declaredFunctionsByName() map[string][]*ssa.Function {
	byName := map[string][]*ssa.Function{}
	for _, ssaPackage := range module.sourcePackages {
		for _, member := range ssaPackage.Members {
			for _, function := range module.functionsDeclaredBy(member) {
				if function.Synthetic == "" {
					byName[function.Name()] = append(byName[function.Name()], function)
				}
			}
		}
	}
	return byName
}

func (module moduleProgram) functionsDeclaredBy(member ssa.Member) []*ssa.Function {
	switch member := member.(type) {
	case *ssa.Function:
		return []*ssa.Function{member}
	case *ssa.Type:
		return module.methodsDeclaredOn(member)
	}
	return nil
}

func (module moduleProgram) methodsDeclaredOn(declaredType *ssa.Type) []*ssa.Function {
	named, isNamed := declaredType.Type().(*types.Named)
	if !isNamed {
		return nil
	}
	methods := []*ssa.Function{}
	for method := range named.Methods() {
		// FuncValue is nil for the methods of an interface, which have no body.
		if function := module.program.FuncValue(method); function != nil {
			methods = append(methods, function)
		}
	}
	return methods
}

func (module moduleProgram) isInModule(function *ssa.Function) bool {
	if function.Pkg == nil {
		return false
	}
	path := function.Pkg.Pkg.Path()
	return path == module.modulePath || strings.HasPrefix(path, module.modulePath+"/")
}

// reachFrom returns the module functions the starts can reach, sorted by name. The edges are the
// static calls in each function's SSA, and a function's closures count as reached with it.
// Closures need that rule because a static call graph has no edge into a function value, such as
// the comparator handed to sort.Slice. Interface calls and calls through other function values
// are still not followed, which is a documented limit.
func (module moduleProgram) reachFrom(starts []*ssa.Function) []*ssa.Function {
	walk := &reachWalk{module: module, visited: map[*ssa.Function]bool{}}
	for _, start := range starts {
		walk.visit(start)
	}
	slices.SortFunc(walk.reached, func(a, b *ssa.Function) int { return strings.Compare(a.String(), b.String()) })
	return walk.reached
}

type reachWalk struct {
	module  moduleProgram
	visited map[*ssa.Function]bool
	reached []*ssa.Function
}

func (walk *reachWalk) visit(function *ssa.Function) {
	if walk.visited[function] {
		return
	}
	walk.visited[function] = true
	// A generated wrapper (a method value, a method expression, a promoted method, a generic
	// instantiation) belongs to no package. The walk goes through it to the real function behind
	// it, and leaves it out of the result.
	isWrapper := function.Pkg == nil
	if !isWrapper && !walk.module.isInModule(function) {
		return
	}
	if !isWrapper {
		walk.reached = append(walk.reached, function)
	}
	for _, callee := range staticCalleesOf(function) {
		walk.visit(callee)
	}
	for _, closure := range function.AnonFuncs {
		walk.visit(closure)
	}
}

func staticCalleesOf(function *ssa.Function) []*ssa.Function {
	callees := []*ssa.Function{}
	for _, block := range function.Blocks {
		for _, instruction := range block.Instrs {
			call, isCall := instruction.(ssa.CallInstruction)
			if !isCall {
				continue
			}
			if callee := call.Common().StaticCallee(); callee != nil {
				callees = append(callees, callee)
			}
		}
	}
	return callees
}

func hashFunctions(functions []*ssa.Function) string {
	hash := sha256.New()
	for _, function := range functions {
		// The NUL ends each entry, so one function's text cannot run into the next one's name.
		fmt.Fprintf(hash, "%s\n%s\x00", function.String(), listingWithoutPositions(function))
	}
	return hex.EncodeToString(hash.Sum(nil))
}

// listingWithoutPositions drops the "# " lines of the SSA listing. They hold the file, line and
// column, so keeping them would change a hash whenever code moves.
func listingWithoutPositions(function *ssa.Function) string {
	var listing bytes.Buffer
	ssa.WriteFunction(&listing, function)
	kept := []string{}
	for _, line := range strings.Split(listing.String(), "\n") {
		if !strings.HasPrefix(line, "# ") {
			kept = append(kept, line)
		}
	}
	return strings.Join(kept, "\n")
}

func namesOf(functions []*ssa.Function) []string {
	names := make([]string, 0, len(functions))
	for _, function := range functions {
		names = append(names, function.String())
	}
	return names
}

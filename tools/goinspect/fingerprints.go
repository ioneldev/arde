package main

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"go/ast"
	"go/parser"
	"go/token"
	"go/types"
	"io"
	"os"
	"path/filepath"
	"slices"
	"strings"

	"golang.org/x/tools/go/packages"
	"golang.org/x/tools/go/ssa"
	"golang.org/x/tools/go/ssa/ssautil"
)

// fingerprintRequest names the functions to hash by id, as `routes` prints them. A bare name would
// let a variable hash an unrelated function that shares its name.
type fingerprintRequest struct {
	dir         string
	mainPackage string
	roots       []string
}

type fingerprintReport struct {
	Fingerprints map[string]string   `json:"fingerprints"`
	Reach        map[string][]string `json:"reach"`
	// Unresolved holds the ids that name no function of the service.
	Unresolved []string `json:"unresolved"`
	// WithoutBody holds the ids that reach code with no body to hash: see hasFunctionWithoutBody.
	WithoutBody []string `json:"without_body"`
}

// moduleProgram is the module's own packages as SSA. Only their function bodies are built; the
// packages they import come from export data and have none.
type moduleProgram struct {
	modulePath     string
	program        *ssa.Program
	sourcePackages []*ssa.Package
	// instancesByOrigin lets a walk that starts at a generic function also enter the instances
	// of it, because the concrete type behind a type parameter is known only in an instance.
	instancesByOrigin map[*ssa.Function][]*ssa.Function
	// hashInputs holds what hashFunctions feeds the hash for a function, so a function reached
	// by many routes is rendered once. Rendering dumps the syntax through reflection, which is
	// slow, and it is the same text for every route.
	hashInputs map[*ssa.Function]string
}

// fingerprintRoots hashes, for each function named, everything it can reach inside the module.
// SSA decides what is reached, and three inputs say what each reached function does, because
// none of them is enough alone: see hashFunctions. It hashes what the functions say and not the
// package state they use: a package-level var or type is outside a fingerprint, and
// `declarations` covers those. A const is inside, because SSA writes its value into every use,
// except one used only as an array length inside a type declared in the function: SSA prints that
// type by name, and the syntax holds only the const's name.
func fingerprintRoots(request fingerprintRequest) (fingerprintReport, error) {
	module, err := loadModuleProgram(request.dir, request.mainPackage)
	if err != nil {
		return fingerprintReport{}, err
	}
	report := fingerprintReport{
		Fingerprints: map[string]string{},
		Reach:        map[string][]string{},
		Unresolved:   []string{},
		WithoutBody:  []string{},
	}
	declared := module.declaredFunctionsByID()
	for _, root := range slices.Compact(slices.Sorted(slices.Values(request.roots))) {
		start, isDeclared := declared[root]
		if !isDeclared {
			report.Unresolved = append(report.Unresolved, root)
			continue
		}
		reached := module.reachFrom(start)
		if hasFunctionWithoutBody(reached) {
			report.WithoutBody = append(report.WithoutBody, root)
			continue
		}
		report.Fingerprints[root] = module.hashFunctions(reached)
		report.Reach[root] = namesOf(reached)
	}
	return report, nil
}

// loadModuleProgram builds main_package's graph and no more: a generic function's instances come
// from every package in the program, so a second main would move this service's hashes.
func loadModuleProgram(dir, mainPackage string) (moduleProgram, error) {
	service, err := loadService(dir, mainPackage)
	if err != nil {
		return moduleProgram{}, err
	}
	program, ssaPackages := ssautil.Packages(service.packages, ssa.InstantiateGenerics)
	program.Build()
	return moduleProgram{
		modulePath:        service.modulePath,
		program:           program,
		sourcePackages:    ssaPackages,
		instancesByOrigin: instancesByOrigin(program),
		hashInputs:        map[*ssa.Function]string{},
	}, nil
}

// loadRoots reads ids from a file, because an id can hold a comma: (*shop.pair[K, V]).serve.
func loadRoots(path string) ([]string, error) {
	content, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	var roots []string
	if err := json.Unmarshal(content, &roots); err != nil {
		return nil, fmt.Errorf("roots file %s: %w; it must be a JSON array of function ids", path, err)
	}
	if roots == nil {
		return nil, fmt.Errorf("roots file %s holds null; it must be a JSON array of function ids", path)
	}
	return roots, nil
}

func instancesByOrigin(program *ssa.Program) map[*ssa.Function][]*ssa.Function {
	instances := map[*ssa.Function][]*ssa.Function{}
	for function := range ssautil.AllFunctions(program) {
		if origin := function.Origin(); origin != nil {
			instances[origin] = append(instances[origin], function)
		}
	}
	return instances
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
func loadPackages(dir string, patterns []string) ([]*packages.Package, error) {
	config := &packages.Config{Mode: packages.LoadSyntax | packages.NeedModule, Dir: dir, ParseFile: parseWithoutComments}
	loaded, err := packages.Load(config, patterns...)
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

// parseWithoutComments parses the way `declarations` does, so the syntax a function carries has
// no comments in it. The loader's own parse keeps doc comments on the tree, and they would move
// a hash.
func parseWithoutComments(fileSet *token.FileSet, filename string, source []byte) (*ast.File, error) {
	return parser.ParseFile(fileSet, filename, source, parser.AllErrors|parser.SkipObjectResolution)
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

// declaredFunctionsByID leaves out what SSA generates, such as the package initialiser. Several
// init functions share an id, which costs nothing, because a handler cannot be one.
func (module moduleProgram) declaredFunctionsByID() map[string]*ssa.Function {
	byID := map[string]*ssa.Function{}
	for _, ssaPackage := range module.sourcePackages {
		for _, member := range ssaPackage.Members {
			for _, function := range module.functionsDeclaredBy(member) {
				if declared, isDeclared := function.Object().(*types.Func); isDeclared && function.Synthetic == "" {
					byID[functionID(declared)] = function
				}
			}
		}
	}
	return byID
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

// reachFrom returns the module functions the start can reach, sorted by name. An edge is any
// function an instruction names, whether it calls it or hands it over, such as the comparator
// given to slices.SortFunc: a fingerprint that moves too often is safe, and one that does not move
// is not. A function's closures count as reached with it, because SSA has no edge into one that
// is only passed around. Calls through an interface, or through a function value that is not
// named where it is used, are not followed, and that is a documented limit.
func (module moduleProgram) reachFrom(start *ssa.Function) []*ssa.Function {
	walk := &reachWalk{module: module, visited: map[*ssa.Function]bool{}}
	walk.visit(start)
	// A generic root has no concrete type behind its type parameter, and its instances do. A new
	// instance of a generic root can move the root, which only over-reports.
	for _, instance := range module.instancesByOrigin[start] {
		walk.visit(instance)
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
	// SSA gives a function it generates no package: a wrapper for a method value, a method
	// expression or a promoted method, and an instance of a generic function. The walk goes
	// through it to the code behind it, and leaves it out of the result.
	isGenerated := function.Pkg == nil
	if !isGenerated && !walk.module.isInModule(function) {
		return
	}
	if !isGenerated {
		walk.reached = append(walk.reached, function)
	}
	// An instance is built for one type argument, so x.Size() in it is a call SSA can resolve.
	// The generic body it came from stays in the hash too, because an edit there changes both.
	if origin := function.Origin(); origin != nil {
		walk.visit(origin)
	}
	for _, mentioned := range functionsMentionedBy(function) {
		walk.visit(mentioned)
	}
	for _, closure := range function.AnonFuncs {
		walk.visit(closure)
	}
}

func functionsMentionedBy(function *ssa.Function) []*ssa.Function {
	mentioned := []*ssa.Function{}
	for _, block := range function.Blocks {
		for _, instruction := range block.Instrs {
			for _, operand := range instruction.Operands(nil) {
				if target, isFunction := (*operand).(*ssa.Function); isFunction {
					mentioned = append(mentioned, target)
				}
			}
		}
	}
	return mentioned
}

// hasFunctionWithoutBody is true when the walk reached code under the module's path that belongs
// to another module, such as a nested module or a module that a replace points at. It comes from
// export data and has no body, so an edit to it cannot show in a hash. The root then gets no
// fingerprint, and the caller counts it as changed on every run. That over-reports and never
// misses, and it does not stop a repo with such a layout from being documented. A function with
// syntax but no blocks is assembly, which stays in: its body is outside the hash.
func hasFunctionWithoutBody(functions []*ssa.Function) bool {
	return slices.ContainsFunc(functions, func(function *ssa.Function) bool {
		return function.Blocks == nil && function.Syntax() == nil
	})
}

// hashFunctions hashes three inputs for each function, in name order. None is enough alone:
//   - the SSA listing shows what each name resolved to after type checking;
//   - every constant in full, because the listing is a debug printer that cuts a string past 20
//     bytes and prints a float to 6 digits, and because SSA writes named constants into the code;
//   - the function's own syntax, because the listing shows a type declared inside the function
//     by its name alone, so its fields and tags would never reach the hash.
func (module moduleProgram) hashFunctions(functions []*ssa.Function) string {
	hash := sha256.New()
	for _, function := range functions {
		io.WriteString(hash, module.hashInputOf(function))
	}
	return hex.EncodeToString(hash.Sum(nil))
}

func (module moduleProgram) hashInputOf(function *ssa.Function) string {
	if rendered, known := module.hashInputs[function]; known {
		return rendered
	}
	// The NUL ends each input, so one cannot run into the next.
	rendered := fmt.Sprintf("%s\x00%s\x00%s\x00%s\x00",
		function.String(), listingWithoutPositions(function), exactConstantsOf(function), syntaxFingerprint(function))
	module.hashInputs[function] = rendered
	return rendered
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

// exactConstantsOf writes every constant operand of the function in full, in instruction order.
func exactConstantsOf(function *ssa.Function) string {
	var constants strings.Builder
	for _, block := range function.Blocks {
		for _, instruction := range block.Instrs {
			for _, operand := range instruction.Operands(nil) {
				if constant, isConstant := (*operand).(*ssa.Const); isConstant && constant.Value != nil {
					constants.WriteString(constant.Value.ExactString())
					constants.WriteByte('\n')
				}
			}
		}
	}
	return constants.String()
}

// syntaxFingerprint normalises the function's syntax in place the way `compare` and
// `declarations` do, so comments, moved code and the tags ARDE adds move nothing in the syntax.
// The SSA listing prints an anonymous struct type with its tags, so a tag ARDE adds to one of
// those still moves a fingerprint. ARDE writes its tags on named structs. By now every error has
// been reported and the build is done, so nothing else reads the tree.
func syntaxFingerprint(function *ssa.Function) string {
	syntax := function.Syntax()
	if syntax == nil {
		return ""
	}
	normaliseTree(syntax)
	return fingerprint(syntax)
}

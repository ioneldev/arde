package main

import (
	"fmt"
	"go/ast"
	"go/token"
	"go/types"
	"path/filepath"
	"slices"
	"strings"

	"golang.org/x/tools/go/packages"
)

// serviceGraphMode asks for the import graph alone, which the go command answers without compiling.
const serviceGraphMode = packages.NeedName | packages.NeedFiles | packages.NeedImports | packages.NeedDeps | packages.NeedModule

// loadedService is one service as the type checker read it: main_package and every package of its
// module that it imports, directly or not. A folder filter would be wrong twice over, because a
// blank import can add routes from a folder nobody names, and a second main shares the module.
type loadedService struct {
	root       string
	modulePath string
	packages   []*packages.Package
	folders    []string
}

func loadService(dir, mainPackage string) (loadedService, error) {
	root, err := resolveDirectory(dir)
	if err != nil {
		return loadedService{}, err
	}
	if err := requireGoMod(root); err != nil {
		return loadedService{}, err
	}
	entry, err := findMainPackage(root, mainPackage)
	if err != nil {
		return loadedService{}, err
	}
	service := loadedService{root: root, modulePath: entry.Module.Path}
	service.folders = service.moduleFoldersImportedBy(entry)
	service.packages, err = loadPackages(root, folderPatterns(service.folders))
	if err != nil {
		return loadedService{}, err
	}
	return service, nil
}

// findMainPackage leaves it to the go command to say what the text names. A file has no module in
// its answer, because go list builds an ad hoc package for it.
func findMainPackage(root, mainPackage string) (*packages.Package, error) {
	config := &packages.Config{Mode: serviceGraphMode, Dir: root}
	found, err := packages.Load(config, mainPackage)
	if err != nil {
		return nil, fmt.Errorf("main_package %s cannot be loaded: %w", mainPackage, err)
	}
	if len(found) != 1 {
		return nil, fmt.Errorf("main_package %s names %d packages, and a service is one package; correct it and run again", mainPackage, len(found))
	}
	if problems := loadProblems(found); len(problems) > 0 {
		return nil, fmt.Errorf("main_package %s does not load; fix these and run again:\n%s", mainPackage, strings.Join(problems, "\n"))
	}
	if found[0].Module == nil {
		return nil, fmt.Errorf("main_package %s is not a package of a module; name the folder that holds the package", mainPackage)
	}
	return found[0], nil
}

// moduleFoldersImportedBy compares modules and not import paths, because a nested module's path
// starts with this one's.
func (service loadedService) moduleFoldersImportedBy(entry *packages.Package) []string {
	folders := []string{}
	packages.Visit([]*packages.Package{entry}, nil, func(visited *packages.Package) {
		if visited.Module == nil || visited.Module.Path != service.modulePath {
			return
		}
		if folder, isInside := service.relativePath(visited.Dir); isInside {
			folders = append(folders, folder)
		}
	})
	slices.Sort(folders)
	return slices.Compact(folders)
}

func folderPatterns(folders []string) []string {
	patterns := make([]string, 0, len(folders))
	for _, folder := range folders {
		patterns = append(patterns, "./"+folder)
	}
	return patterns
}

func (service loadedService) relativePath(path string) (string, bool) {
	relative, err := filepath.Rel(service.root, path)
	if err != nil || !filepath.IsLocal(relative) {
		return "", false
	}
	return filepath.ToSlash(relative), true
}

// buildFiles are the files this machine's build compiles, which is fewer than the files in those
// folders: one for another platform or behind a tag stays out.
func (service loadedService) buildFiles() []string {
	files := []string{}
	for _, loaded := range service.packages {
		for _, path := range loaded.GoFiles {
			if file, isInside := service.relativePath(path); isInside {
				files = append(files, file)
			}
		}
	}
	slices.Sort(files)
	return files
}

func (service loadedService) holds(function *types.Func) bool {
	return function.Pkg() != nil && slices.ContainsFunc(service.packages, func(loaded *packages.Package) bool {
		return loaded.Types == function.Pkg()
	})
}

type sourcePlace struct {
	File string `json:"file"`
	Line int    `json:"line"`
}

// placeOf reads the file as written, because a //line directive can name any path and this one
// decides where the renderer writes. Only a file cgo made sits outside the repo as written, and
// there the directive is the one way back to the source. It reports false for what cgo added,
// which has no place in the repo.
func (service loadedService) placeOf(pos token.Pos) (sourcePlace, bool) {
	fileSet := service.packages[0].Fset
	for _, followDirectives := range []bool{false, true} {
		position := fileSet.PositionFor(pos, followDirectives)
		if file, isInside := service.relativePath(position.Filename); isInside {
			return sourcePlace{File: file, Line: position.Line}, true
		}
	}
	return sourcePlace{}, false
}

// serviceCall is one call in the service's source, with what the type checker knows about it.
type serviceCall struct {
	syntax *ast.CallExpr
	info   *types.Info
	// enclosing is the declared function around the call, and nil at package level.
	enclosing *types.Func
	callee    string
	place     sourcePlace
}

func (service loadedService) calls() []serviceCall {
	calls := []serviceCall{}
	for _, loaded := range service.packages {
		for _, file := range loaded.Syntax {
			for _, declaration := range file.Decls {
				enclosing := functionDeclaredBy(declaration, loaded.TypesInfo)
				ast.Inspect(declaration, func(node ast.Node) bool {
					call, isCall := node.(*ast.CallExpr)
					if !isCall {
						return true
					}
					// The call's own parenthesis, as in match, so the two commands agree on a line.
					if place, isInTheRepo := service.placeOf(call.Lparen); isInTheRepo {
						calls = append(calls, serviceCall{
							syntax: call, info: loaded.TypesInfo, enclosing: enclosing,
							callee: nameOfCallee(call.Fun), place: place,
						})
					}
					return true
				})
			}
		}
	}
	return calls
}

func functionDeclaredBy(declaration ast.Decl, info *types.Info) *types.Func {
	function, isFunction := declaration.(*ast.FuncDecl)
	if !isFunction {
		return nil
	}
	declared, _ := info.Defs[function.Name].(*types.Func)
	return declared
}

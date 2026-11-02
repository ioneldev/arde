package main

import (
	"cmp"
	"go/ast"
	"go/constant"
	"go/types"
	"maps"
	"slices"
	"strings"
	"unicode/utf8"

	"golang.org/x/tools/go/types/typeutil"
)

const routeKind = "route"

const stripPrefixFunction = "net/http.StripPrefix"

var fileServerFunctions = []string{"net/http.FileServer", "net/http.FileServerFS"}

// registrationFunctions are the calls that add a route to net/http, named as the type checker
// names them, so a receiver can be called anything and a look-alike method is not one.
var registrationFunctions = []string{
	"net/http.Handle",
	"net/http.HandleFunc",
	"(*net/http.ServeMux).Handle",
	"(*net/http.ServeMux).HandleFunc",
}

var registrationPositions = map[string]int{"path": 0, "handler": 1}

type routeRequest struct {
	dir          string
	patternsPath string
	mainPackage  string
}

// routeSite is one call that registers a route. Literals holds the path when the compiler knows
// it, and Args the path and the handler as written. A handler with a root reads as the root's name.
type routeSite struct {
	callMatch
	HandlerID   *string `json:"handler_id"`
	HandlerFile *string `json:"handler_file"`
	HandlerLine *int    `json:"handler_line"`
}

// wrapperCall is a call inside a function that a pattern of the same kind matches. It hands on what
// its caller gave it, and the pattern reads the value at the calls to that function.
type wrapperCall struct {
	Kind string `json:"kind"`
	sourcePlace
}

type routeReport struct {
	PackageFolders   []string      `json:"package_folders"`
	BuildFiles       []string      `json:"build_files"`
	Sites            []routeSite   `json:"sites"`
	StrippedPrefixes []sourcePlace `json:"stripped_prefixes"`
	WrapperPrefixes  []sourcePlace `json:"wrapper_prefixes"`
	WrapperCalls     []wrapperCall `json:"wrapper_calls"`
}

// wrappersByKind holds, for each pattern kind, the declared functions a pattern of that kind matched
// at a call, with the argument positions of the first pattern that did.
type wrappersByKind map[string]map[*types.Func]map[string]int

// findRoutes reads every pattern before it loads anything, as findMatches does.
func findRoutes(request routeRequest) (routeReport, error) {
	patterns, err := loadPatterns(request.patternsPath)
	if err != nil {
		return routeReport{}, err
	}
	service, err := loadService(request.dir, request.mainPackage)
	if err != nil {
		return routeReport{}, err
	}
	calls := service.calls()
	reader := routeReader{service: service, patterns: patterns, wrappers: wrappersIn(calls, patterns)}
	return reader.report(calls), nil
}

func wrappersIn(calls []serviceCall, patterns []callPattern) wrappersByKind {
	wrappers := wrappersByKind{}
	for _, call := range calls {
		target := typeutil.StaticCallee(call.info, call.syntax)
		if target == nil {
			continue
		}
		for _, pattern := range patterns {
			if !pattern.call.MatchString(call.callee) {
				continue
			}
			if wrappers[pattern.kind] == nil {
				wrappers[pattern.kind] = map[*types.Func]map[string]int{}
			}
			if _, known := wrappers[pattern.kind][target.Origin()]; !known {
				wrappers[pattern.kind][target.Origin()] = pattern.argumentPositions
			}
		}
	}
	return wrappers
}

type routeReader struct {
	service  loadedService
	patterns []callPattern
	wrappers wrappersByKind
}

func (reader routeReader) report(calls []serviceCall) routeReport {
	report := routeReport{
		PackageFolders:   reader.service.folders,
		BuildFiles:       reader.service.buildFiles(),
		Sites:            []routeSite{},
		StrippedPrefixes: []sourcePlace{},
		WrapperPrefixes:  []sourcePlace{},
		WrapperCalls:     []wrapperCall{},
	}
	for _, call := range calls {
		report.Sites = append(report.Sites, reader.sitesOf(call)...)
		report.WrapperCalls = append(report.WrapperCalls, reader.wrapperCallsOf(call)...)
		if call.isCallTo(stripPrefixFunction) && !call.stripsAFileServer() {
			report.StrippedPrefixes = append(report.StrippedPrefixes, call.place)
		}
		if reader.addsToAWrappedPath(call) {
			report.WrapperPrefixes = append(report.WrapperPrefixes, call.place)
		}
	}
	report.sort()
	return report
}

func (report *routeReport) sort() {
	slices.SortStableFunc(report.Sites, func(a, b routeSite) int {
		return cmp.Or(cmp.Compare(a.File, b.File), cmp.Compare(a.Line, b.Line))
	})
	slices.SortFunc(report.StrippedPrefixes, comparePlaces)
	slices.SortFunc(report.WrapperPrefixes, comparePlaces)
	slices.SortFunc(report.WrapperCalls, func(a, b wrapperCall) int {
		return cmp.Or(comparePlaces(a.sourcePlace, b.sourcePlace), cmp.Compare(a.Kind, b.Kind))
	})
	report.WrapperCalls = slices.Compact(report.WrapperCalls)
}

func comparePlaces(a, b sourcePlace) int {
	return cmp.Or(cmp.Compare(a.File, b.File), cmp.Compare(a.Line, b.Line))
}

func (call serviceCall) isCallTo(names ...string) bool {
	target := typeutil.StaticCallee(call.info, call.syntax)
	return target != nil && slices.Contains(names, target.FullName())
}

// stripsAFileServer is true for http.StripPrefix around a direct call to a file server. No route
// sits under one, so nothing's path is left unread, and this is the usual way to serve static files.
// A variable is not followed, so a stripped handler can never be taken for a file server by name.
func (call serviceCall) stripsAFileServer() bool {
	if len(call.syntax.Args) != 2 {
		return false
	}
	handler, isCall := ast.Unparen(call.syntax.Args[1]).(*ast.CallExpr)
	if !isCall {
		return false
	}
	return serviceCall{syntax: handler, info: call.info}.isCallTo(fileServerFunctions...)
}

func (call serviceCall) matches(pattern callPattern) bool {
	return pattern.call.MatchString(call.callee)
}

func (reader routeReader) sitesOf(call serviceCall) []routeSite {
	sites := []routeSite{}
	for _, positions := range reader.routePositionsOf(call) {
		if site, isRoute := reader.siteAt(call, positions); isRoute {
			sites = append(sites, site)
		}
	}
	return sites
}

// routePositionsOf says where a call holds its path and handler. A pattern wins, so that a wrong
// position shows as a route that cannot be read. A function a pattern matched at one call counts at
// every call, because the site inside it is passed over, and a call on a receiver the pattern does
// not name would otherwise leave no trace of its route.
func (reader routeReader) routePositionsOf(call serviceCall) []map[string]int {
	positions := []map[string]int{}
	for _, pattern := range reader.patterns {
		if pattern.kind == routeKind && call.matches(pattern) {
			positions = append(positions, pattern.argumentPositions)
		}
	}
	if len(positions) > 0 {
		return positions
	}
	if target := typeutil.StaticCallee(call.info, call.syntax); target != nil {
		if wrapperPositions, isWrapper := reader.wrappers[routeKind][target.Origin()]; isWrapper {
			return []map[string]int{wrapperPositions}
		}
	}
	if call.isCallTo(registrationFunctions...) {
		return []map[string]int{registrationPositions}
	}
	return nil
}

// siteAt reports false for a call that adds no route of its own: a mount, or a call inside a
// wrapper that hands on a path it was given.
func (reader routeReader) siteAt(call serviceCall, positions map[string]int) (routeSite, bool) {
	handlers := handlerReader{info: call.info, service: reader.service}
	handler := handlers.innermost(argumentAt(call.syntax, positions, "handler"))
	if handler != nil && handlers.isMount(handler) {
		return routeSite{}, false
	}
	path, pathIsKnown := knownString(call.info, argumentAt(call.syntax, positions, "path"))
	if _, isInsideAWrapper := reader.wrappers[routeKind][call.enclosing]; isInsideAWrapper && !pathIsKnown {
		return routeSite{}, false
	}
	site := routeSite{callMatch: reader.matchAt(call, positions)}
	if pathIsKnown {
		site.Literals["path"] = path
	}
	if handler != nil {
		reader.nameTheRoot(&site, handlers.rootOf(handler))
	}
	return site, true
}

// addsToAWrappedPath is true for a site inside a wrapper whose path holds a constant with a slash.
// The pattern reads the path at the wrapper's calls, and that text would be missing what the wrapper
// adds. A method such as "GET "+path has no slash, and the wrapper's name gives the method.
func (reader routeReader) addsToAWrappedPath(call serviceCall) bool {
	if _, isInsideAWrapper := reader.wrappers[routeKind][call.enclosing]; !isInsideAWrapper {
		return false
	}
	for _, positions := range reader.routePositionsOf(call) {
		path := argumentAt(call.syntax, positions, "path")
		if _, isKnown := knownString(call.info, path); !isKnown && holdsAConstantWithASlash(call.info, path) {
			return true
		}
	}
	return false
}

func holdsAConstantWithASlash(info *types.Info, expression ast.Expr) bool {
	found := false
	if expression == nil {
		return false
	}
	ast.Inspect(expression, func(node ast.Node) bool {
		if part, isExpression := node.(ast.Expr); isExpression {
			text, isKnown := knownString(info, part)
			found = found || (isKnown && strings.Contains(text, "/"))
		}
		return !found
	})
	return found
}

func (reader routeReader) matchAt(call serviceCall, positions map[string]int) callMatch {
	source := reader.service.printerSource()
	return callMatch{
		Kind:     routeKind,
		Call:     call.callee,
		File:     call.place.File,
		Line:     call.place.Line,
		Args:     source.argumentTexts(call.syntax, positions),
		Literals: map[string]string{},
	}
}

// nameTheRoot leaves a function with no place in the repo unnamed, because it has no declaration
// a comment could go on and nothing of the repo's to hash.
func (reader routeReader) nameTheRoot(site *routeSite, root handlerRoot) {
	if root.function == nil {
		return
	}
	place, isInTheRepo := reader.service.placeOf(root.function.Pos())
	if !isInTheRepo {
		return
	}
	id := functionID(root.function)
	site.Args["handler"] = reader.service.printerSource().textOf(root.name)
	site.HandlerID, site.HandlerFile, site.HandlerLine = &id, &place.File, &place.Line
}

// functionID names one function exactly, across packages. fingerprints finds a root by this text.
func functionID(function *types.Func) string {
	return function.Origin().FullName()
}

func (service loadedService) printerSource() *parsedSource {
	return &parsedSource{fileSet: service.packages[0].Fset}
}

func (reader routeReader) wrapperCallsOf(call serviceCall) []wrapperCall {
	found := []wrapperCall{}
	for _, kind := range slices.Sorted(maps.Keys(reader.wrappers)) {
		if _, isInside := reader.wrappers[kind][call.enclosing]; kind == routeKind || !isInside {
			continue
		}
		if slices.ContainsFunc(reader.patterns, func(pattern callPattern) bool {
			return pattern.kind == kind && call.matches(pattern)
		}) {
			found = append(found, wrapperCall{Kind: kind, sourcePlace: call.place})
		}
	}
	return found
}

func argumentAt(call *ast.CallExpr, positions map[string]int, name string) ast.Expr {
	position, isNamed := positions[name]
	if !isNamed || position >= len(call.Args) {
		return nil
	}
	return call.Args[position]
}

// knownString is the value of a string the compiler knows, a const or a sum of consts included.
// Bytes that are not UTF-8 have no value, because JSON would turn them into something else. A value
// past the cap reads as the stand-in, since no path that long can be read downstream and a constant
// can reach two gigabytes.
func knownString(info *types.Info, argument ast.Expr) (string, bool) {
	if argument == nil {
		return "", false
	}
	value := info.Types[argument].Value
	if value == nil || value.Kind() != constant.String {
		return "", false
	}
	text := constant.StringVal(value)
	if len(text) > maxPrintedBytes {
		return tooLongToPrint, true
	}
	return text, utf8.ValidString(text)
}

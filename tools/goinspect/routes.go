package main

import (
	"cmp"
	"go/ast"
	"go/constant"
	"go/types"
	"maps"
	"slices"
	"unicode/utf8"

	"golang.org/x/tools/go/types/typeutil"
)

const routeKind = "route"

const stripPrefixFunction = "net/http.StripPrefix"

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
	calls, err := service.calls()
	if err != nil {
		return routeReport{}, err
	}
	reader := routeReader{service: service, patterns: patterns, wrappers: wrappersIn(calls, patterns)}
	return reader.report(calls)
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

func (reader routeReader) report(calls []serviceCall) (routeReport, error) {
	report := routeReport{
		PackageFolders:   reader.service.folders,
		BuildFiles:       reader.service.buildFiles(),
		Sites:            []routeSite{},
		StrippedPrefixes: []sourcePlace{},
		WrapperCalls:     []wrapperCall{},
	}
	for _, call := range calls {
		sites, err := reader.sitesOf(call)
		if err != nil {
			return routeReport{}, err
		}
		report.Sites = append(report.Sites, sites...)
		report.WrapperCalls = append(report.WrapperCalls, reader.wrapperCallsOf(call)...)
		if call.isCallTo(stripPrefixFunction) {
			report.StrippedPrefixes = append(report.StrippedPrefixes, call.place)
		}
	}
	report.sort()
	return report, nil
}

func (report *routeReport) sort() {
	slices.SortStableFunc(report.Sites, func(a, b routeSite) int {
		return cmp.Or(cmp.Compare(a.File, b.File), cmp.Compare(a.Line, b.Line))
	})
	slices.SortFunc(report.StrippedPrefixes, comparePlaces)
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

func (call serviceCall) matches(pattern callPattern) bool {
	return pattern.call.MatchString(call.callee)
}

func (reader routeReader) sitesOf(call serviceCall) ([]routeSite, error) {
	sites := []routeSite{}
	for _, positions := range reader.routePositionsOf(call) {
		site, isRoute, err := reader.siteAt(call, positions)
		if err != nil {
			return nil, err
		}
		if isRoute {
			sites = append(sites, site)
		}
	}
	return sites, nil
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
func (reader routeReader) siteAt(call serviceCall, positions map[string]int) (routeSite, bool, error) {
	handlers := handlerReader{info: call.info, service: reader.service}
	handler := handlers.innermost(argumentAt(call.syntax, positions, "handler"))
	if handler != nil && handlers.isMount(handler) {
		return routeSite{}, false, nil
	}
	path, pathIsKnown := knownString(call.info, argumentAt(call.syntax, positions, "path"))
	if _, isInsideAWrapper := reader.wrappers[routeKind][call.enclosing]; isInsideAWrapper && !pathIsKnown {
		return routeSite{}, false, nil
	}
	site := routeSite{callMatch: reader.matchAt(call, positions)}
	if pathIsKnown {
		site.Literals["path"] = path
	}
	if handler == nil {
		return site, true, nil
	}
	err := reader.nameTheRoot(&site, handlers.rootOf(handler))
	return site, true, err
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

func (reader routeReader) nameTheRoot(site *routeSite, root handlerRoot) error {
	if root.function == nil {
		return nil
	}
	place, err := reader.service.placeOf(root.function.Pos())
	if err != nil {
		return err
	}
	id := functionID(root.function)
	site.Args["handler"] = reader.service.printerSource().textOf(root.name)
	site.HandlerID, site.HandlerFile, site.HandlerLine = &id, &place.File, &place.Line
	return nil
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

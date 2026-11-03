package main

import (
	"cmp"
	"go/ast"
	"go/constant"
	"go/token"
	"go/types"
	"maps"
	"regexp"
	"slices"
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

// The problems that stop a route from being read. Each names a site in unread_sites.
const (
	// A site inside a wrapper builds its path from more than the wrapper's parameters.
	buildsItsPath = "builds its path"
	// A site inside a wrapper hands its path to another wrapper, whose own form would apply too.
	handsOnItsPath = "hands on its path"
	// A call to a wrapper registers no route: its handler is a mux, or no site inside the wrapper
	// registers a path it is given.
	registersNothing = "registers nothing"
	// A call hands a mux to a wrapper that does more with it than register it at its path.
	mountsAMux = "mounts a mux"
	// The argument a route pattern names as the method is not a constant at this call.
	methodIsNotKnown = "method is not a constant"
	// The argument a route pattern names as the method is an empty string at this call.
	methodIsEmpty = "method is empty"
)

// aMethodAndASpace is the constant a wrapper may put before its path parameter, such as "GET ".
var aMethodAndASpace = regexp.MustCompile(`^[A-Z]+ $`)

type routeRequest struct {
	dir          string
	patternsPath string
	mainPackage  string
}

// routeSite is one call that registers a route. Literals holds the path it registers when the
// compiler knows it, and Args the path and the handler as written. A handler with an anchor reads
// as the anchor's name. Roots holds the id of every function of the module the route's fingerprint
// covers: the functions the handler names, and the ones a middleware variable it calls was filled
// with. It is empty when there is no anchor, except for a call through such a variable.
type routeSite struct {
	callMatch
	HandlerID   *string  `json:"handler_id"`
	HandlerFile *string  `json:"handler_file"`
	HandlerLine *int     `json:"handler_line"`
	Roots       []string `json:"roots"`
	// HandlerIdentity is a hash of what the handler holds: two sites with one anchor and a
	// different identity register different handlers.
	HandlerIdentity string `json:"handler_identity"`
	// ConstantsHash is a hash of the constants the handler holds, which the route's fingerprint
	// covers, or empty when it holds none.
	ConstantsHash string `json:"constants_hash"`
}

// wrapperCall is a call inside a function that a pattern of the same kind matches. It hands on what
// its caller gave it, and the pattern reads the value at the calls to that function.
type wrapperCall struct {
	Kind string `json:"kind"`
	sourcePlace
}

// unreadSite is a call whose route cannot be read, and the problem that says why. WrittenAt is
// where a wrapper writes a parameter its site registers, when that is why.
type unreadSite struct {
	sourcePlace
	Problem   string       `json:"problem"`
	WrittenAt *sourcePlace `json:"written_at"`
}

type routeReport struct {
	PackageFolders   []string      `json:"package_folders"`
	BuildFiles       []string      `json:"build_files"`
	Sites            []routeSite   `json:"sites"`
	StrippedPrefixes []sourcePlace `json:"stripped_prefixes"`
	UnreadSites      []unreadSite  `json:"unread_sites"`
	WrapperCalls     []wrapperCall `json:"wrapper_calls"`
}

// wrappersByKind holds, for each pattern kind, the declared functions a pattern of that kind matched
// at a call, with the argument positions of the first pattern that did.
type wrappersByKind map[string]map[*types.Func]map[string]int

// wrapperForm is how a site inside a wrapper builds the path it registers from the wrapper's
// parameters, so that each call to the wrapper can build the same path from its own constants.
type wrapperForm struct {
	// methodBefore is the constant before the path parameter, such as "GET ", or empty.
	methodBefore string
	// methodFromTheCall is method+" "+path, with the method read at each call.
	methodFromTheCall bool
	// named holds the functions of the module the site names, whose code shapes every request.
	named []*types.Func
	// passesTheHandlerOn is true when the site registers the wrapper's handler parameter, alone or
	// behind calls that only wrap it, and holdsTheHandler when the parameter is anywhere in what
	// the site registers.
	passesTheHandlerOn bool
	holdsTheHandler    bool
	// ownHandler is what a site that holds no handler the call gives registers, such as a CORS
	// preflight beside each route, read at the site: every call's route there runs it, and none
	// runs the call's handler.
	ownHandler *registeredAtTheSite
}

// registeredAtTheSite is a handler a site inside a wrapper names itself, and its text as written.
type registeredAtTheSite struct {
	reading handlerReading
	text    string
}

// mountsAtThePath is true for the form a mux can be handed to: the path parameter alone, with the
// handler parameter passed on, which reads as registering the mux at the call's path.
func (form wrapperForm) mountsAtThePath() bool {
	return form.methodBefore == "" && !form.methodFromTheCall && form.passesTheHandlerOn
}

func (form wrapperForm) registered(path, method string) string {
	if form.methodFromTheCall {
		return method + " " + path
	}
	return form.methodBefore + path
}

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
	reader := routeReader{
		service:  service,
		patterns: patterns,
		wrappers: wrappersIn(calls, patterns),
		bodies:   service.functionBodies(),
	}
	return reader.withWrapperForms(calls).report(calls), nil
}

func wrappersIn(calls []serviceCall, patterns []callPattern) wrappersByKind {
	wrappers := wrappersByKind{}
	for _, call := range calls {
		target := call.target()
		if target == nil {
			continue
		}
		for _, pattern := range patterns {
			if !call.matches(pattern) {
				continue
			}
			if wrappers[pattern.kind] == nil {
				wrappers[pattern.kind] = map[*types.Func]map[string]int{}
			}
			if _, known := wrappers[pattern.kind][target]; !known {
				wrappers[pattern.kind][target] = pattern.argumentPositions
			}
		}
	}
	return wrappers
}

type routeReader struct {
	service  loadedService
	patterns []callPattern
	wrappers wrappersByKind
	bodies   functionBodies
	// forms holds, for each route wrapper of the module, the form of each site inside it that
	// registers a path it is given. A wrapper with a site that cannot be read is in unreadWrappers.
	forms          map[*types.Func][]wrapperForm
	unreadWrappers map[*types.Func]bool
}

// withWrapperForms reads the sites inside every wrapper before any call to one is read.
func (reader routeReader) withWrapperForms(calls []serviceCall) routeReader {
	reader.forms = map[*types.Func][]wrapperForm{}
	reader.unreadWrappers = map[*types.Func]bool{}
	for _, call := range calls {
		for _, read := range reader.routeCallsOf(call) {
			inner, isInside := reader.innerSiteAt(read)
			if !isInside {
				continue
			}
			if inner.problem != "" {
				reader.unreadWrappers[call.enclosing] = true
			} else {
				reader.forms[call.enclosing] = append(reader.forms[call.enclosing], inner.form)
			}
		}
	}
	return reader
}

func (reader routeReader) report(calls []serviceCall) routeReport {
	report := routeReport{
		PackageFolders:   reader.service.folders,
		BuildFiles:       reader.service.buildFiles(),
		Sites:            []routeSite{},
		StrippedPrefixes: []sourcePlace{},
		UnreadSites:      reader.usesOutOfSight(),
		WrapperCalls:     []wrapperCall{},
	}
	for _, call := range calls {
		reading := reader.readCall(call)
		report.Sites = append(report.Sites, reading.sites...)
		report.UnreadSites = append(report.UnreadSites, reading.unread...)
		report.WrapperCalls = append(report.WrapperCalls, reader.wrapperCallsOf(call)...)
		if call.isCallTo(stripPrefixFunction) && !call.stripsAFileServer() {
			report.StrippedPrefixes = append(report.StrippedPrefixes, call.place)
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
	slices.SortFunc(report.UnreadSites, func(a, b unreadSite) int {
		return cmp.Or(comparePlaces(a.sourcePlace, b.sourcePlace), cmp.Compare(a.Problem, b.Problem))
	})
	report.UnreadSites = slices.Compact(report.UnreadSites)
	slices.SortFunc(report.WrapperCalls, func(a, b wrapperCall) int {
		return cmp.Or(comparePlaces(a.sourcePlace, b.sourcePlace), cmp.Compare(a.Kind, b.Kind))
	})
	report.WrapperCalls = slices.Compact(report.WrapperCalls)
}

func comparePlaces(a, b sourcePlace) int {
	return cmp.Or(cmp.Compare(a.File, b.File), cmp.Compare(a.Line, b.Line))
}

// target is the function a call reaches, as declared, or nil for a call the compiler cannot name.
func (call serviceCall) target() *types.Func {
	if target := typeutil.StaticCallee(call.info, call.syntax); target != nil {
		return target.Origin()
	}
	return nil
}

func (call serviceCall) isCallTo(names ...string) bool {
	target := call.target()
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

// routeCall is a call read with the argument positions of one route pattern.
type routeCall struct {
	serviceCall
	positions map[string]int
}

func (call routeCall) argument(name string) ast.Expr {
	position, isNamed := call.positions[name]
	if !isNamed || position >= len(call.syntax.Args) {
		return nil
	}
	return call.syntax.Args[position]
}

func (call routeCall) namesA(name string) bool {
	_, isNamed := call.positions[name]
	return isNamed
}

// methodOf is the method the call passes where its route pattern names one, when the compiler
// knows it.
func (call routeCall) methodOf() (string, bool) {
	if !call.namesA("method") {
		return "", false
	}
	return knownString(call.info, call.argument("method"))
}

// problemWithTheMethod is why the method a route pattern names at this call cannot be read, or
// empty when it can, or when the pattern names none.
func (call routeCall) problemWithTheMethod() string {
	method, isKnown := call.methodOf()
	switch {
	case !call.namesA("method"):
		return ""
	case !isKnown:
		return methodIsNotKnown
	case method == "":
		return methodIsEmpty
	}
	return ""
}

// callReading is what one call adds to the report.
type callReading struct {
	sites  []routeSite
	unread []unreadSite
}

func (call routeCall) unreadAs(problem string) callReading {
	return callReading{unread: []unreadSite{{sourcePlace: call.place, Problem: problem}}}
}

func (call routeCall) unreadFor(inner innerSite) callReading {
	return callReading{unread: []unreadSite{{sourcePlace: call.place, Problem: inner.problem, WrittenAt: inner.writtenAt}}}
}

func (reader routeReader) readCall(call serviceCall) callReading {
	reading := callReading{}
	for _, read := range reader.routeCallsOf(call) {
		found := reader.readAt(read)
		reading.sites = append(reading.sites, found.sites...)
		reading.unread = append(reading.unread, found.unread...)
	}
	return reading
}

// routeCallsOf reads the call once for each set of positions it registers a route with.
func (reader routeReader) routeCallsOf(call serviceCall) []routeCall {
	reads := []routeCall{}
	for _, positions := range reader.routePositionsOf(call) {
		reads = append(reads, routeCall{serviceCall: call, positions: positions})
	}
	return reads
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
	if wrapperPositions, isWrapper := reader.wrappers[routeKind][call.target()]; isWrapper {
		return []map[string]int{wrapperPositions}
	}
	if call.isCallTo(registrationFunctions...) {
		return []map[string]int{registrationPositions}
	}
	return nil
}

// readAt reads a call one of three ways: a site inside a wrapper that hands on its path, a call
// to a wrapper, whose sites say what it registers, or a call that registers its route itself.
func (reader routeReader) readAt(call routeCall) callReading {
	if inner, isInside := reader.innerSiteAt(call); isInside {
		if inner.problem != "" {
			return call.unreadFor(inner)
		}
		return callReading{}
	}
	if reader.isRouteWrapper(call.target()) {
		return reader.wrapperCallAt(call)
	}
	return reader.registrationAt(call)
}

// isRouteWrapper is true for a function of the module that a route pattern matched. A function of
// another module that one matched registers its route itself, as a router's method does.
func (reader routeReader) isRouteWrapper(function *types.Func) bool {
	_, isMatched := reader.wrappers[routeKind][function]
	return isMatched && function != nil && reader.service.holds(function)
}

// innerSite is a site inside a wrapper whose path is not a constant: the form it registers its
// path in, or the problem that stops it being read, with the place of the write behind it.
type innerSite struct {
	form      wrapperForm
	problem   string
	writtenAt *sourcePlace
}

// innerSiteAt reads a site inside a wrapper that registers a path it is given. The pattern reads
// the path at the wrapper's calls, so the site must build it from the wrapper's parameters in a
// form those calls can repeat. It reports false for any other call.
func (reader routeReader) innerSiteAt(call routeCall) (innerSite, bool) {
	_, pathIsKnown := knownString(call.info, call.argument("path"))
	if pathIsKnown || !reader.isRouteWrapper(call.enclosing) {
		return innerSite{}, false
	}
	if reader.isRouteWrapper(call.target()) {
		return innerSite{problem: handsOnItsPath}, true
	}
	form, isRead := reader.formOf(call)
	if !isRead {
		return innerSite{problem: buildsItsPath}, true
	}
	if written, isWritten := reader.writeToTheParametersOf(call.enclosing); isWritten {
		return innerSite{problem: buildsItsPath, writtenAt: written}, true
	}
	handlers := reader.handlersOf(call)
	form.named = handlers.functionsNamedIn(call.syntax)
	if position, isNamed := reader.wrappers[routeKind][call.enclosing]["handler"]; isNamed {
		handlerParameter, registered := parameterAt(call.enclosing, position), call.argument("handler")
		if handlerParameter != nil && registered != nil {
			form.passesTheHandlerOn = handlers.refersTo(handlers.innermost(registered), handlerParameter)
			form.holdsTheHandler = handlers.mentions(registered, handlerParameter)
		}
	}
	if !form.holdsTheHandler {
		form.ownHandler = &registeredAtTheSite{reading: reader.handlerAt(call), text: reader.textOfTheHandlerAt(call)}
	}
	return innerSite{form: form}, true
}

func (reader routeReader) textOfTheHandlerAt(call routeCall) string {
	handler := call.argument("handler")
	if handler == nil {
		return ""
	}
	return reader.service.printerSource().textOf(handler)
}

// writeToTheParametersOf finds where a wrapper writes its path or method parameter. The parameter
// stays the same variable after a write, so only a wrapper that never writes them can be trusted
// to register the values its callers pass. A write with no place in the repo still counts.
func (reader routeReader) writeToTheParametersOf(wrapper *types.Func) (*sourcePlace, bool) {
	positions := reader.wrappers[routeKind][wrapper]
	read := []*types.Var{}
	for _, name := range []string{"path", "method"} {
		if position, isNamed := positions[name]; isNamed {
			read = append(read, parameterAt(wrapper, position))
		}
	}
	written, isWritten := reader.bodies.firstWriteTo(wrapper, read)
	if !isWritten {
		return nil, false
	}
	if place, isInTheRepo := reader.service.placeOf(written); isInTheRepo {
		return &place, true
	}
	return nil, true
}

// formOf reads three forms and no others: the wrapper's path parameter alone, a constant method
// and a space before it, and the wrapper's method parameter, a space and its path parameter.
func (reader routeReader) formOf(site routeCall) (wrapperForm, bool) {
	parameters := wrapperParameters{
		wrapper:     site.enclosing,
		positions:   reader.wrappers[routeKind][site.enclosing],
		typedSyntax: typedSyntax{info: site.info},
	}
	path := ast.Unparen(site.argument("path"))
	if parameters.isTheOneNamed("path", path) {
		return wrapperForm{}, true
	}
	sum, isSum := path.(*ast.BinaryExpr)
	if !isSum || sum.Op != token.ADD || !parameters.isTheOneNamed("path", sum.Y) {
		return wrapperForm{}, false
	}
	if method, isKnown := knownString(site.info, sum.X); isKnown {
		return wrapperForm{methodBefore: method}, aMethodAndASpace.MatchString(method)
	}
	methodAndSpace, isSum := ast.Unparen(sum.X).(*ast.BinaryExpr)
	if !isSum || methodAndSpace.Op != token.ADD || !parameters.isTheOneNamed("method", parameters.withoutAStringConversion(methodAndSpace.X)) {
		return wrapperForm{}, false
	}
	space, isKnown := knownString(site.info, methodAndSpace.Y)
	return wrapperForm{methodFromTheCall: true}, isKnown && space == " "
}

// wrapperParameters names a wrapper's parameters by the argument names its route pattern gives.
type wrapperParameters struct {
	wrapper   *types.Func
	positions map[string]int
	typedSyntax
}

func (parameters wrapperParameters) isTheOneNamed(name string, expression ast.Expr) bool {
	position, isNamed := parameters.positions[name]
	return isNamed && parameters.refersTo(expression, parameterAt(parameters.wrapper, position))
}

// withoutAStringConversion takes string(m) to m, so a method parameter of a named string type,
// such as Method, reads as the method.
func (syntax typedSyntax) withoutAStringConversion(expression ast.Expr) ast.Expr {
	conversion, isCall := ast.Unparen(expression).(*ast.CallExpr)
	if !isCall || len(conversion.Args) != 1 || !syntax.info.Types[conversion.Fun].IsType() {
		return expression
	}
	if basic, isBasic := syntax.info.TypeOf(conversion.Fun).Underlying().(*types.Basic); isBasic && basic.Info()&types.IsString != 0 {
		return conversion.Args[0]
	}
	return expression
}

// parameterAt is the parameter an argument at that position is passed to: the last one for the
// extra arguments of a variadic function, and nil past the end.
func parameterAt(function *types.Func, position int) *types.Var {
	signature := function.Origin().Signature()
	parameters := signature.Params()
	if signature.Variadic() && position >= parameters.Len()-1 {
		return parameters.At(parameters.Len() - 1)
	}
	if position >= parameters.Len() {
		return nil
	}
	return parameters.At(position)
}

// typedSyntax is the syntax of one package with what the type checker says about it.
type typedSyntax struct {
	info *types.Info
}

// refersTo is true for an expression that is the variable itself, in parentheses or not.
func (syntax typedSyntax) refersTo(expression ast.Expr, variable *types.Var) bool {
	identifier, isIdentifier := ast.Unparen(expression).(*ast.Ident)
	return isIdentifier && variable != nil && syntax.info.Uses[identifier] == variable
}

// wrapperCallAt reads a call to a wrapper as one route for each site inside the wrapper, with the
// path that site builds from this call's constants. A wrapper whose own site cannot be read gives
// nothing here, because that site stops the run.
func (reader routeReader) wrapperCallAt(call routeCall) callReading {
	wrapper := call.target()
	if reader.unreadWrappers[wrapper] {
		return callReading{}
	}
	handler := reader.handlerAt(call)
	if handler.isMount {
		return reader.mountAt(call)
	}
	if len(reader.forms[wrapper]) == 0 {
		return call.unreadAs(registersNothing)
	}
	if problem := call.problemWithTheMethod(); problem != "" {
		return call.unreadAs(problem)
	}
	method, methodIsKnown := call.methodOf()
	path, pathIsKnown := knownString(call.info, call.argument("path"))
	if !pathIsKnown {
		return callReading{sites: []routeSite{reader.siteAt(call, handler)}}
	}
	reading := callReading{}
	for _, form := range reader.forms[wrapper] {
		if form.methodFromTheCall && !methodIsKnown {
			return call.unreadAs(methodIsNotKnown)
		}
		if form.ownHandler != nil && form.ownHandler.reading.isMount {
			continue
		}
		site := reader.siteThrough(call, form, handler)
		site.Literals["path"] = form.registered(path, method)
		reading.sites = append(reading.sites, site)
	}
	return reading
}

// siteThrough reads the route one site of a wrapper registers at this call. The wrapper's code and
// the functions its site names shape every request the route answers, so they are roots too.
func (reader routeReader) siteThrough(call routeCall, form wrapperForm, given handlerReading) routeSite {
	shapedBy := append([]*types.Func{call.target()}, form.named...)
	if form.ownHandler == nil {
		return reader.siteAt(call, given.alsoRootedAt(shapedBy))
	}
	site := reader.siteAt(call, form.ownHandler.reading.alsoRootedAt(shapedBy))
	if site.HandlerID == nil {
		site.Args["handler"] = form.ownHandler.text
	}
	return site
}

// mountAt reads a mux handed to a wrapper as a mount at the call's path when every site of the
// wrapper registers it there, as mux.Handle(path, mux) would, so it adds no route of its own. A
// wrapper that does more with the mux stops the run, and so does one whose sites do not register
// the handler it is given at all.
func (reader routeReader) mountAt(call routeCall) callReading {
	forms := reader.forms[call.target()]
	if len(forms) > 0 && !slices.ContainsFunc(forms, func(form wrapperForm) bool { return !form.mountsAtThePath() }) {
		return callReading{}
	}
	if slices.ContainsFunc(forms, func(form wrapperForm) bool { return form.holdsTheHandler }) {
		return call.unreadAs(mountsAMux)
	}
	return call.unreadAs(registersNothing)
}

// registrationAt reads a call that registers its route itself, such as mux.HandleFunc or a
// router's method that a pattern names. A mount adds no route of its own, because its mux's routes
// are listed where they are registered.
func (reader routeReader) registrationAt(call routeCall) callReading {
	handler := reader.handlerAt(call)
	if handler.isMount {
		return callReading{}
	}
	registered, pathIsKnown := knownString(call.info, call.argument("path"))
	if problem := call.problemWithTheMethod(); problem != "" {
		return call.unreadAs(problem)
	}
	if method, methodIsKnown := call.methodOf(); methodIsKnown {
		registered = method + " " + registered
	}
	site := reader.siteAt(call, handler)
	if pathIsKnown {
		site.Literals["path"] = registered
	}
	return callReading{sites: []routeSite{site}}
}

func (reader routeReader) handlersOf(call routeCall) handlerReader {
	return handlerReader{info: call.info, service: reader.service, bodies: reader.bodies}
}

func (reader routeReader) handlerAt(call routeCall) handlerReading {
	handlers := reader.handlersOf(call)
	position, isNamed := call.positions["handler"]
	if !isNamed {
		return handlerReading{}
	}
	return handlers.read(registration{call: call.syntax, handler: position, others: call.otherPositions()})
}

// otherPositions are the arguments the route pattern names as neither the path, the method nor the
// handler.
func (call routeCall) otherPositions() []int {
	others := []int{}
	for position := range call.syntax.Args {
		if !slices.Contains(slices.Collect(maps.Values(call.positions)), position) {
			others = append(others, position)
		}
	}
	return others
}

// siteAt leaves the path to the caller, which knows what the call registers.
func (reader routeReader) siteAt(call routeCall, handler handlerReading) routeSite {
	site := routeSite{
		callMatch: reader.matchAt(call), Roots: []string{}, HandlerIdentity: handler.identity, ConstantsHash: handler.constants,
	}
	reader.nameTheAnchor(&site, handler)
	return site
}

func (reader routeReader) matchAt(call routeCall) callMatch {
	source := reader.service.printerSource()
	return callMatch{
		Kind:     routeKind,
		Callee:   call.callee,
		File:     call.place.File,
		Line:     call.place.Line,
		Args:     source.argumentTexts(call.syntax, call.positions),
		Literals: map[string]string{},
	}
}

// nameTheAnchor leaves a function with no place in the repo unnamed, because it has no declaration
// a comment could go on and nothing of the repo's to hash, and then the route has no roots either,
// because a hash of the rest of its code would not move for an edit to the anchor. A handler read
// with roots and no anchor, as one called through a middleware variable is, keeps its roots.
func (reader routeReader) nameTheAnchor(site *routeSite, handler handlerReading) {
	anchor := handler.anchor
	if anchor.function == nil {
		site.Roots = reader.idsInTheRepo(handler.roots)
		return
	}
	place, isInTheRepo := reader.service.placeOf(anchor.function.Pos())
	if !isInTheRepo {
		return
	}
	id := functionID(anchor.function)
	site.Args["handler"] = reader.service.printerSource().textOf(anchor.name)
	site.HandlerID, site.HandlerFile, site.HandlerLine = &id, &place.File, &place.Line
	site.Roots = reader.idsInTheRepo(handler.roots)
}

func (reader routeReader) idsInTheRepo(functions []*types.Func) []string {
	ids := []string{}
	for _, function := range functions {
		if _, isInTheRepo := reader.service.placeOf(function.Pos()); isInTheRepo {
			ids = append(ids, functionID(function))
		}
	}
	return ids
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

package main

import (
	"cmp"
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"go/ast"
	"go/token"
	"go/types"
	"io"
	"slices"
)

const serveMuxType = "*net/http.ServeMux"

// handlerRoot is a function of the module named in a handler, and the expression that names it.
type handlerRoot struct {
	function *types.Func
	name     ast.Expr
}

// handlerReading is what a registration's handler says. The anchor is the one function the route's
// docs belong on, and the roots are every function of the module the handler names, which the
// route's fingerprint covers. A mount registers a mux, whose own routes are listed where they are
// registered.
type handlerReading struct {
	anchor  handlerRoot
	roots   []*types.Func
	isMount bool
	// identity is what the handler holds, so that two routes sharing an anchor can tell whether
	// they share a handler.
	identity string
	// constants is a hash of the constants the handler holds, or empty when it holds none.
	constants string
}

// handlerReader follows a registration's handler through what the type checker knows.
type handlerReader struct {
	info    *types.Info
	service loadedService
	bodies  functionBodies
}

// registration is a call that registers a route, with the position of its handler and of every
// other argument that shapes the handler, such as middleware a wrapper applies: each one that is
// neither the path nor the method.
type registration struct {
	call    *ast.CallExpr
	handler int
	others  []int
}

func (registered registration) handlerExpression() ast.Expr {
	if registered.handler < 0 || registered.handler >= len(registered.call.Args) {
		return nil
	}
	return registered.call.Args[registered.handler]
}

// parts are the handler and the other arguments, which make up what serves the route.
func (registered registration) parts() []ast.Expr {
	parts := []ast.Expr{registered.handlerExpression()}
	for _, position := range registered.others {
		parts = append(parts, registered.call.Args[position])
	}
	return parts
}

func (registered registration) partPositions() []int {
	return append([]int{registered.handler}, registered.others...)
}

// read gives a handler that holds a function written in place neither anchor nor roots: that
// function has no id to hash and no declaration to carry docs. It can still mount a mux.
func (reader handlerReader) read(registered registration) handlerReading {
	handler := registered.handlerExpression()
	if handler == nil {
		return handlerReading{}
	}
	innermost := reader.innermost(handler)
	held := reader.heldBy(registered.parts())
	reading := handlerReading{isMount: reader.isMount(innermost), identity: held.identity, constants: held.constants}
	if slices.ContainsFunc(registered.parts(), holdsAFunctionLiteral) {
		return reading
	}
	reading.anchor = reader.anchorAt(innermost)
	if reading.anchor.function != nil {
		named := []*types.Func{reading.anchor.function}
		for _, part := range registered.parts() {
			named = append(named, reader.functionsNamedIn(part)...)
		}
		reading.roots = sortedByID(slices.Concat(named, reader.methodsHandedToAnInterface(registered)))
	}
	return reading
}

// heldByAHandler is what a handler holds. Its identity tells handlers apart by that and not by how
// they are written: each function by its id, each variable by where it is declared and each
// constant by its value, in order. So listItems and http.HandlerFunc(listItems) read the same, and
// logged(listItems) and logged(listOrders) do not. The constants alone go into the route's hash,
// because SSA writes them into the code of the function that holds the registration, which no
// route's hash covers, so paged(5, h) and paged(9, h) would hash the same.
type heldByAHandler struct {
	identity  string
	constants string
}

func (reader handlerReader) heldBy(parts []ast.Expr) heldByAHandler {
	identity, constants, holdsAConstant := sha256.New(), sha256.New(), false
	for _, part := range parts {
		ast.Inspect(part, func(node ast.Node) bool {
			expression, isExpression := node.(ast.Expr)
			if !isExpression {
				return true
			}
			if value := reader.info.Types[expression].Value; value != nil {
				fmt.Fprintf(identity, "constant %s\x00", value.ExactString())
				fmt.Fprintf(constants, "%s\x00", value.ExactString())
				holdsAConstant = true
				return false
			}
			if identifier, isIdentifier := expression.(*ast.Ident); isIdentifier {
				io.WriteString(identity, reader.identityOfAName(identifier))
			}
			return true
		})
	}
	held := heldByAHandler{identity: hex.EncodeToString(identity.Sum(nil))}
	if holdsAConstant {
		held.constants = hex.EncodeToString(constants.Sum(nil))
	}
	return held
}

// identityOfAName leaves out a type and a package, which hold no part of the handler.
func (reader handlerReader) identityOfAName(identifier *ast.Ident) string {
	switch named := reader.info.Uses[identifier].(type) {
	case *types.Func:
		return "function " + functionID(named) + "\x00"
	case *types.Var:
		return "variable " + reader.service.packages[0].Fset.Position(named.Pos()).String() + "\x00"
	case *types.Builtin, *types.Nil:
		return named.Name() + "\x00"
	}
	return ""
}

// alsoRootedAt adds functions to the roots of a handler that has an anchor. One with no anchor has
// no roots, because a hash of some of its code would not move for an edit to the rest.
func (reading handlerReading) alsoRootedAt(functions []*types.Func) handlerReading {
	if reading.anchor.function != nil {
		reading.roots = sortedByID(slices.Concat(reading.roots, functions))
	}
	return reading
}

func (reader handlerReader) refersTo(expression ast.Expr, variable *types.Var) bool {
	return typedSyntax{info: reader.info}.refersTo(expression, variable)
}

// mentions is true when the variable is anywhere in the expression.
func (reader handlerReader) mentions(expression ast.Expr, variable *types.Var) bool {
	found := false
	ast.Inspect(expression, func(node ast.Node) bool {
		identifier, isIdentifier := node.(*ast.Ident)
		found = found || isIdentifier && reader.info.Uses[identifier] == variable
		return !found
	})
	return found
}

func holdsAFunctionLiteral(expression ast.Expr) bool {
	found := false
	ast.Inspect(expression, func(node ast.Node) bool {
		_, isLiteral := node.(*ast.FuncLit)
		found = found || isLiteral
		return !found
	})
	return found
}

// innermost takes off every call that only wraps the handler, and stops at the first that does
// more. That one is a factory when the module declares it.
func (reader handlerReader) innermost(handler ast.Expr) ast.Expr {
	expression := ast.Unparen(handler)
	call, isCall := expression.(*ast.CallExpr)
	if !isCall {
		return expression
	}
	if wrapped, onlyWraps := reader.wrappedBy(call); onlyWraps {
		return reader.innermost(wrapped)
	}
	return expression
}

// wrappedBy says which handler a call only wraps: the argument of a conversion such as
// http.HandlerFunc(x), the one handler given to another module's function such as
// http.TimeoutHandler, or the one handler given to a function of this module that passes it on.
// A call the compiler cannot name could do anything with its handler, so it does not only wrap.
func (reader handlerReader) wrappedBy(call *ast.CallExpr) (ast.Expr, bool) {
	if reader.info.Types[call.Fun].IsType() && len(call.Args) == 1 {
		return call.Args[0], true
	}
	positions := reader.handlerPositionsOf(call)
	target := serviceCall{syntax: call, info: reader.info}.target()
	if len(positions) != 1 || target == nil {
		return nil, false
	}
	wrapped := call.Args[positions[0]]
	if !reader.service.holds(target) {
		return wrapped, true
	}
	return wrapped, reader.bodies.wraps(target, parameterAt(target, positions[0]))
}

func (reader handlerReader) handlerPositionsOf(call *ast.CallExpr) []int {
	positions := []int{}
	for position, argument := range call.Args {
		if isHandlerType(reader.info.TypeOf(argument)) {
			positions = append(positions, position)
		}
	}
	return positions
}

// isMount reads the type, so a factory that returns a mux, such as api.Mux(), mounts it too. A
// mux's ServeHTTP taken as a value serves what the mux serves, so it counts as the mux.
func (reader handlerReader) isMount(handler ast.Expr) bool {
	if selector, isSelector := ast.Unparen(handler).(*ast.SelectorExpr); isSelector && selector.Sel.Name == "ServeHTTP" {
		handler = selector.X
	}
	handlerType := reader.info.TypeOf(handler)
	return handlerType != nil && handlerType.String() == serveMuxType
}

// anchorAt names the function behind the innermost handler: a function of the module, the factory
// a call that does more than wrap reaches, or the ServeHTTP of a value of the module's own type.
// Anything else has no declaration for the docs and no body to hash, so the route has no anchor.
func (reader handlerReader) anchorAt(innermost ast.Expr) handlerRoot {
	name := nameBehind(innermost)
	if function := reader.functionNamedBy(name); function != nil {
		return handlerRoot{function: function, name: name}
	}
	return reader.serveMethodOfAValue(innermost)
}

// serveMethodOfAValue anchors a value of a module type that serves requests itself, such as
// &itemsHandler{...} or a variable of that type, at its ServeHTTP, which is the whole handler. A
// call is a factory's. A value of a function type holds code no reading can name, and a value of an
// interface can be of any type.
func (reader handlerReader) serveMethodOfAValue(innermost ast.Expr) handlerRoot {
	if _, isCall := innermost.(*ast.CallExpr); isCall {
		return handlerRoot{}
	}
	valueType := reader.info.TypeOf(innermost)
	if valueType == nil || reader.info.Types[innermost].IsType() || types.IsInterface(valueType) {
		return handlerRoot{}
	}
	if _, isFunction := valueType.Underlying().(*types.Signature); isFunction {
		return handlerRoot{}
	}
	method, _, _ := types.LookupFieldOrMethod(valueType, true, nil, "ServeHTTP")
	serve, isMethod := method.(*types.Func)
	if !isMethod || !hasTheHandlersShape(serve.Signature()) || !reader.service.holds(serve.Origin()) {
		return handlerRoot{}
	}
	return handlerRoot{function: serve, name: nameOfAValue(innermost)}
}

// nameOfAValue shows a composite literal by its type, as `itemsHandler` for &itemsHandler{...}, and
// anything else as it is written.
func nameOfAValue(value ast.Expr) ast.Expr {
	if literal := compositeBehind(value); literal != nil && literal.Type != nil {
		return literal.Type
	}
	return value
}

// compositeBehind is the composite literal an expression is, or takes the address of.
func compositeBehind(expression ast.Expr) *ast.CompositeLit {
	expression = ast.Unparen(expression)
	if address, isAddress := expression.(*ast.UnaryExpr); isAddress && address.Op == token.AND {
		expression = ast.Unparen(address.X)
	}
	literal, _ := expression.(*ast.CompositeLit)
	return literal
}

// methodsHandedToAnInterface is every method of each module type the handler hands to an
// interface, where the static graph cannot follow a call: the handler itself given to a parameter
// of interface type, an argument given to one inside it, an element a composite literal keeps as
// one, and the operand of a conversion to one.
func (reader handlerReader) methodsHandedToAnInterface(registered registration) []*types.Func {
	handed := reader.argumentTypesHandedOn(registered.call, registered.partPositions())
	for _, part := range registered.parts() {
		ast.Inspect(part, func(node ast.Node) bool {
			switch node := node.(type) {
			case *ast.CallExpr:
				handed = append(handed, reader.argumentTypesHandedOn(node, positionsOf(node.Args))...)
			case *ast.CompositeLit:
				handed = append(handed, reader.elementTypesHandedOn(node)...)
			}
			return true
		})
	}
	methods := []*types.Func{}
	for _, handedType := range handed {
		methods = append(methods, reader.methodsOf(handedType)...)
	}
	return methods
}

func positionsOf(arguments []ast.Expr) []int {
	positions := make([]int, len(arguments))
	for position := range arguments {
		positions[position] = position
	}
	return positions
}

func (reader handlerReader) argumentTypesHandedOn(call *ast.CallExpr, positions []int) []types.Type {
	if reader.info.Types[call.Fun].IsType() {
		if len(call.Args) == 1 && types.IsInterface(reader.info.TypeOf(call.Fun)) {
			return reader.concreteTypeOf(call.Args[0])
		}
		return nil
	}
	calleeType := reader.info.TypeOf(call.Fun)
	if calleeType == nil {
		return nil
	}
	signature, isSignature := calleeType.Underlying().(*types.Signature)
	if !isSignature {
		return nil
	}
	handed := []types.Type{}
	for _, position := range positions {
		parameter := parameterTypeAt(signature, position, call.Ellipsis.IsValid())
		if position < len(call.Args) && parameter != nil && types.IsInterface(parameter) {
			handed = append(handed, reader.concreteTypeOf(call.Args[position])...)
		}
	}
	return handed
}

// parameterTypeAt is the type an argument at that position is given as: the element of a variadic
// parameter for its extra arguments, unless the call spreads a slice into it.
func parameterTypeAt(signature *types.Signature, position int, spreads bool) types.Type {
	parameters := signature.Params()
	last := parameters.Len() - 1
	if signature.Variadic() && position >= last {
		variadic := parameters.At(last).Type()
		if slice, isSlice := variadic.Underlying().(*types.Slice); isSlice && !spreads {
			return slice.Elem()
		}
		return variadic
	}
	if position > last {
		return nil
	}
	return parameters.At(position).Type()
}

func (reader handlerReader) elementTypesHandedOn(literal *ast.CompositeLit) []types.Type {
	handed := []types.Type{}
	for index, element := range literal.Elts {
		var key ast.Expr
		value := element
		if pair, isPair := element.(*ast.KeyValueExpr); isPair {
			key, value = pair.Key, pair.Value
		}
		if slot := slotOf(reader.info.TypeOf(literal), index, key); slot != nil && types.IsInterface(slot) {
			handed = append(handed, reader.concreteTypeOf(value)...)
		}
	}
	return handed
}

// slotOf is the type a composite literal keeps an element's value as: a field of a struct, or the
// element of a slice, an array or a map.
func slotOf(literalType types.Type, index int, key ast.Expr) types.Type {
	if literalType == nil {
		return nil
	}
	if pointer, isPointer := literalType.Underlying().(*types.Pointer); isPointer {
		literalType = pointer.Elem()
	}
	switch kept := literalType.Underlying().(type) {
	case *types.Struct:
		return fieldOf(kept, index, key)
	case *types.Slice:
		return kept.Elem()
	case *types.Array:
		return kept.Elem()
	case *types.Map:
		return kept.Elem()
	}
	return nil
}

func fieldOf(structure *types.Struct, index int, key ast.Expr) types.Type {
	if name, isNamed := key.(*ast.Ident); isNamed {
		for field := range structure.Fields() {
			if field.Name() == name.Name {
				return field.Type()
			}
		}
		return nil
	}
	if index < structure.NumFields() {
		return structure.Field(index).Type()
	}
	return nil
}

func (reader handlerReader) concreteTypeOf(value ast.Expr) []types.Type {
	valueType := reader.info.TypeOf(value)
	if valueType == nil || types.IsInterface(valueType) {
		return nil
	}
	return []types.Type{valueType}
}

// methodsOf is every method of a named type of the module, promoted ones included, on value and
// pointer receivers alike.
func (reader handlerReader) methodsOf(handedType types.Type) []*types.Func {
	named := namedBehind(handedType)
	if named == nil || !reader.service.declares(named) {
		return nil
	}
	methods := []*types.Func{}
	for selection := range types.NewMethodSet(types.NewPointer(named)).Methods() {
		if method, isFunction := selection.Obj().(*types.Func); isFunction && reader.service.holds(method.Origin()) {
			methods = append(methods, method)
		}
	}
	return methods
}

// nameBehind takes a factory call to its callee, and an instantiated function to the function.
func nameBehind(handler ast.Expr) ast.Expr {
	for {
		switch typed := ast.Unparen(handler).(type) {
		case *ast.CallExpr:
			handler = typed.Fun
		case *ast.IndexExpr:
			handler = typed.X
		case *ast.IndexListExpr:
			handler = typed.X
		default:
			return typed
		}
	}
}

// functionsNamedIn is every function of the module the handler names, at any depth and in id order:
// the factory, the middleware, the handler and a function handed over as a value alike.
func (reader handlerReader) functionsNamedIn(handler ast.Expr) []*types.Func {
	found := []*types.Func{}
	ast.Inspect(handler, func(node ast.Node) bool {
		if identifier, isIdentifier := node.(*ast.Ident); isIdentifier {
			if function := reader.functionNamedBy(identifier); function != nil {
				found = append(found, function)
			}
		}
		return true
	})
	return sortedByID(found)
}

func sortedByID(functions []*types.Func) []*types.Func {
	slices.SortFunc(functions, func(a, b *types.Func) int { return cmp.Compare(functionID(a), functionID(b)) })
	return slices.CompactFunc(functions, func(a, b *types.Func) bool { return functionID(a) == functionID(b) })
}

func (reader handlerReader) functionNamedBy(name ast.Expr) *types.Func {
	var identifier *ast.Ident
	switch typed := name.(type) {
	case *ast.Ident:
		identifier = typed
	case *ast.SelectorExpr:
		identifier = typed.Sel
	default:
		return nil
	}
	function, isFunction := reader.info.Uses[identifier].(*types.Func)
	if !isFunction || isInterfaceMethod(function) || !reader.service.holds(function.Origin()) {
		return nil
	}
	return function
}

func isInterfaceMethod(function *types.Func) bool {
	receiver := function.Signature().Recv()
	return receiver != nil && types.IsInterface(receiver.Type())
}

// isHandlerType is true for what can serve a request: a function of the handler's shape, or a value
// with a ServeHTTP method of that shape.
func isHandlerType(candidate types.Type) bool {
	if candidate == nil {
		return false
	}
	if signature, isFunction := candidate.Underlying().(*types.Signature); isFunction {
		return hasTheHandlersShape(signature)
	}
	method, _, _ := types.LookupFieldOrMethod(candidate, true, nil, "ServeHTTP")
	function, isFunction := method.(*types.Func)
	return isFunction && hasTheHandlersShape(function.Signature())
}

// hasTheHandlersShape is true for exactly a response writer and a request, returning nothing or an
// error. A function that takes more, such as renderError(w, r, err), is a dependency and no handler.
func hasTheHandlersShape(signature *types.Signature) bool {
	parameters, results := signature.Params(), signature.Results()
	takesARequest := parameters.Len() == 2 &&
		parameters.At(0).Type().String() == "net/http.ResponseWriter" &&
		parameters.At(1).Type().String() == "*net/http.Request"
	returnsAtMostAnError := results.Len() == 0 || results.Len() == 1 && results.At(0).Type().String() == "error"
	return takesARequest && returnsAtMostAnError
}

// functionBodies holds the body of each function the service declares, to read what one does with
// a handler it is given.
type functionBodies map[*types.Func]functionBody

type functionBody struct {
	block *ast.BlockStmt
	typedSyntax
}

func (service loadedService) functionBodies() functionBodies {
	bodies := functionBodies{}
	for _, loaded := range service.packages {
		for _, file := range loaded.Syntax {
			for _, declaration := range file.Decls {
				function, isFunction := declaration.(*ast.FuncDecl)
				if !isFunction || function.Body == nil {
					continue
				}
				if declared, isDeclared := loaded.TypesInfo.Defs[function.Name].(*types.Func); isDeclared {
					bodies[declared] = functionBody{block: function.Body, typedSyntax: typedSyntax{info: loaded.TypesInfo}}
				}
			}
		}
	}
	return bodies
}

// wraps is true when the function's body serves through the handler it is given, whatever the
// spelling: it calls it, calls or takes its ServeHTTP, passes it to a call whose result serves
// requests, or keeps it in a handler of the module that it returns. Middleware written any of
// these ways reads the same, so the route's docs go on the handler inside.
func (bodies functionBodies) wraps(function *types.Func, parameter *types.Var) bool {
	body, isDeclared := bodies[function]
	if !isDeclared || parameter == nil {
		return false
	}
	found := false
	ast.Inspect(body.block, func(node ast.Node) bool {
		switch node := node.(type) {
		case *ast.SelectorExpr:
			found = found || body.isServeMethodOf(node, parameter)
		case *ast.CallExpr:
			found = found || body.refersTo(node.Fun, parameter) || body.handsOn(node, parameter)
		case *ast.ReturnStmt:
			found = found || slices.ContainsFunc(node.Results, func(result ast.Expr) bool {
				return bodies.isAHandlerHolding(body, result, parameter)
			})
		}
		return !found
	})
	return found
}

func (body functionBody) isServeMethodOf(selector *ast.SelectorExpr, parameter *types.Var) bool {
	return selector.Sel.Name == "ServeHTTP" && body.refersTo(selector.X, parameter)
}

// standsFor is true for the parameter, and for its ServeHTTP taken as a value, which serves the same
// requests.
func (body functionBody) standsFor(expression ast.Expr, parameter *types.Var) bool {
	selector, isSelector := ast.Unparen(expression).(*ast.SelectorExpr)
	return body.refersTo(expression, parameter) || isSelector && body.isServeMethodOf(selector, parameter)
}

func (body functionBody) handsOn(call *ast.CallExpr, parameter *types.Var) bool {
	return isHandlerType(body.info.TypeOf(call)) && slices.ContainsFunc(call.Args, func(argument ast.Expr) bool {
		return body.standsFor(argument, parameter)
	})
}

// isAHandlerHolding is true for a composite literal, or its address, of a module type that serves
// requests and keeps the parameter in one of its elements.
func (bodies functionBodies) isAHandlerHolding(body functionBody, result ast.Expr, parameter *types.Var) bool {
	held := compositeBehind(result)
	if held == nil || !bodies.servesRequests(body.info.TypeOf(held)) {
		return false
	}
	return slices.ContainsFunc(held.Elts, func(element ast.Expr) bool {
		if pair, isPair := element.(*ast.KeyValueExpr); isPair {
			element = pair.Value
		}
		return body.standsFor(element, parameter)
	})
}

// servesRequests is true for a type of the module whose ServeHTTP, on the value or its pointer, has
// the handler's shape.
func (bodies functionBodies) servesRequests(valueType types.Type) bool {
	if valueType == nil {
		return false
	}
	method, _, _ := types.LookupFieldOrMethod(valueType, true, nil, "ServeHTTP")
	serve, isMethod := method.(*types.Func)
	if !isMethod || !hasTheHandlersShape(serve.Signature()) {
		return false
	}
	_, isOfTheModule := bodies[serve.Origin()]
	return isOfTheModule
}

// firstWriteTo is where the function's body, its closures included, first writes one of the
// variables. An increment needs a number, and none of the variables read here can be one.
func (bodies functionBodies) firstWriteTo(function *types.Func, variables []*types.Var) (token.Pos, bool) {
	body, isDeclared := bodies[function]
	if !isDeclared {
		return token.NoPos, false
	}
	written := token.NoPos
	ast.Inspect(body.block, func(node ast.Node) bool {
		if !written.IsValid() && body.writesTo(node, variables) {
			written = node.Pos()
		}
		return !written.IsValid()
	})
	return written, written.IsValid()
}

// writesTo reads an assignment, a short declaration that declares a variable again, a range that
// assigns, an address taken, and a call of a pointer method on a variable that is no pointer,
// which takes its address too.
func (body functionBody) writesTo(node ast.Node, variables []*types.Var) bool {
	switch node := node.(type) {
	case *ast.AssignStmt:
		return body.refersToAny(node.Lhs, variables)
	case *ast.RangeStmt:
		return node.Tok == token.ASSIGN && body.refersToAny([]ast.Expr{node.Key, node.Value}, variables)
	case *ast.UnaryExpr:
		return node.Op == token.AND && body.refersToAny([]ast.Expr{node.X}, variables)
	case *ast.SelectorExpr:
		return body.takesTheAddressFor(node, variables)
	}
	return false
}

func (body functionBody) refersToAny(expressions []ast.Expr, variables []*types.Var) bool {
	return slices.ContainsFunc(expressions, func(expression ast.Expr) bool {
		return expression != nil && slices.ContainsFunc(variables, func(variable *types.Var) bool {
			return body.refersTo(expression, variable)
		})
	})
}

func (body functionBody) takesTheAddressFor(selector *ast.SelectorExpr, variables []*types.Var) bool {
	selection := body.info.Selections[selector]
	if selection == nil || selection.Kind() != types.MethodVal {
		return false
	}
	_, hasAPointerReceiver := selection.Obj().(*types.Func).Signature().Recv().Type().(*types.Pointer)
	_, isAPointer := selection.Recv().(*types.Pointer)
	return hasAPointerReceiver && !isAPointer && body.refersToAny([]ast.Expr{selector.X}, variables)
}

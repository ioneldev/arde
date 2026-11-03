package main

import (
	"cmp"
	"go/ast"
	"go/types"
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
}

// handlerReader follows a registration's handler through what the type checker knows.
type handlerReader struct {
	info    *types.Info
	service loadedService
	bodies  functionBodies
}

// read gives a handler that holds a function written in place neither anchor nor roots: that
// function has no id to hash and no declaration to carry docs. It can still mount a mux.
func (reader handlerReader) read(handler ast.Expr) handlerReading {
	if handler == nil {
		return handlerReading{}
	}
	innermost := reader.innermost(handler)
	reading := handlerReading{isMount: reader.isMount(innermost)}
	if holdsAFunctionLiteral(handler) {
		return reading
	}
	reading.anchor = reader.anchorAt(innermost)
	if reading.anchor.function != nil {
		reading.roots = reader.functionsNamedIn(handler)
	}
	return reading
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
	return wrapped, reader.bodies.passesOn(target, parameterAt(target, positions[0]))
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

// isMount reads the type, so a factory that returns a mux, such as api.Mux(), mounts it too.
func (reader handlerReader) isMount(handler ast.Expr) bool {
	handlerType := reader.info.TypeOf(handler)
	return handlerType != nil && handlerType.String() == serveMuxType
}

// anchorAt names the function behind the innermost handler: a function of the module, or the
// factory a call that does more than wrap reaches. Anything else has no declaration for the docs
// and no body to hash, so the route has no anchor.
func (reader handlerReader) anchorAt(innermost ast.Expr) handlerRoot {
	name := nameBehind(innermost)
	function := reader.functionNamedBy(name)
	if function == nil {
		return handlerRoot{}
	}
	return handlerRoot{function: function, name: name}
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
	slices.SortFunc(found, func(a, b *types.Func) int { return cmp.Compare(functionID(a), functionID(b)) })
	return slices.CompactFunc(found, func(a, b *types.Func) bool { return functionID(a) == functionID(b) })
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

// passesOn is true when the function's body calls ServeHTTP on the parameter, or passes it to a
// call whose result serves requests, as middleware does. A function that calls the parameter
// itself, as one that falls back on it does, is a factory with a dependency.
func (bodies functionBodies) passesOn(function *types.Func, parameter *types.Var) bool {
	body, isDeclared := bodies[function]
	if !isDeclared || parameter == nil {
		return false
	}
	found := false
	ast.Inspect(body.block, func(node ast.Node) bool {
		if call, isCall := node.(*ast.CallExpr); isCall {
			found = found || body.servesThrough(call, parameter) || body.handsOn(call, parameter)
		}
		return !found
	})
	return found
}

func (body functionBody) servesThrough(call *ast.CallExpr, parameter *types.Var) bool {
	selector, isSelector := ast.Unparen(call.Fun).(*ast.SelectorExpr)
	return isSelector && selector.Sel.Name == "ServeHTTP" && body.refersTo(selector.X, parameter)
}

func (body functionBody) handsOn(call *ast.CallExpr, parameter *types.Var) bool {
	return isHandlerType(body.info.TypeOf(call)) && slices.ContainsFunc(call.Args, func(argument ast.Expr) bool {
		return body.refersTo(argument, parameter)
	})
}

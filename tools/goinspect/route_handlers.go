package main

import (
	"go/ast"
	"go/types"
	"slices"
)

const serveMuxType = "*net/http.ServeMux"

// handlerRoot is the function a route's fingerprint starts from, and the expression that names it.
type handlerRoot struct {
	function *types.Func
	name     ast.Expr
}

// handlerReading is what a registration's handler says: the function behind it, and whether it is
// a mount, whose mux's own routes are listed where they are registered.
type handlerReading struct {
	root    handlerRoot
	isMount bool
}

// handlerReader follows a registration's handler through what the type checker knows.
type handlerReader struct {
	info    *types.Info
	service loadedService
}

func (reader handlerReader) read(handler ast.Expr) handlerReading {
	innermost := reader.innermost(handler)
	if innermost == nil {
		return handlerReading{}
	}
	return handlerReading{root: reader.rootOf(innermost), isMount: reader.isMount(innermost)}
}

// innermost takes off what wraps a handler: parentheses, a conversion such as http.HandlerFunc(x),
// and a call that takes exactly one handler, such as logged(x). It returns nil for nil.
func (reader handlerReader) innermost(handler ast.Expr) ast.Expr {
	if handler == nil {
		return nil
	}
	expression := ast.Unparen(handler)
	call, isCall := expression.(*ast.CallExpr)
	if !isCall {
		return expression
	}
	if reader.info.Types[call.Fun].IsType() && len(call.Args) == 1 {
		return reader.innermost(call.Args[0])
	}
	if wrapped := reader.handlerArgumentsOf(call); len(wrapped) == 1 {
		return reader.innermost(wrapped[0])
	}
	return expression
}

func (reader handlerReader) handlerArgumentsOf(call *ast.CallExpr) []ast.Expr {
	return slices.DeleteFunc(slices.Clone(call.Args), func(argument ast.Expr) bool {
		return !isHandlerType(reader.info.TypeOf(argument))
	})
}

func (reader handlerReader) isMount(handler ast.Expr) bool {
	handlerType := reader.info.TypeOf(handler)
	return handlerType != nil && handlerType.String() == serveMuxType
}

// rootOf finds the function behind the innermost handler. Only a function of this service's own
// source has a root: anything else has no body to hash, and a hash of nothing would never move. A
// call still holding handlers took several of them, and a root at one would miss edits to the rest.
func (reader handlerReader) rootOf(handler ast.Expr) handlerRoot {
	if call, isCall := handler.(*ast.CallExpr); isCall && len(reader.handlerArgumentsOf(call)) > 0 {
		return handlerRoot{}
	}
	name := nameBehind(handler)
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

// isHandlerType is true for what can serve a request: a function that takes a response writer and
// a request, whatever else it takes or returns, and a value with such a ServeHTTP method. A call
// that takes one of these is middleware, and any other call is a factory.
func isHandlerType(candidate types.Type) bool {
	if candidate == nil {
		return false
	}
	if signature, isFunction := candidate.Underlying().(*types.Signature); isFunction {
		return takesAResponseAndARequest(signature)
	}
	method, _, _ := types.LookupFieldOrMethod(candidate, true, nil, "ServeHTTP")
	function, isFunction := method.(*types.Func)
	return isFunction && takesAResponseAndARequest(function.Signature())
}

func takesAResponseAndARequest(signature *types.Signature) bool {
	parameters := []string{}
	for parameter := range signature.Params().Variables() {
		parameters = append(parameters, parameter.Type().String())
	}
	return slices.Contains(parameters, "net/http.ResponseWriter") && slices.Contains(parameters, "*net/http.Request")
}

package main

import (
	"go/ast"
	"go/types"
	"slices"
	"strings"

	"golang.org/x/tools/go/packages"
)

// The problems of a use of a registrar that no pattern reads. Each names the use in unread_sites.
const (
	// A registrar is used other than in a direct call: as a method value, a function value, an
	// argument, an assignment, a store or a method expression.
	usedAsAValue = "used as a value"
	// A call, or a method value, goes through an interface that a registrar's type satisfies.
	calledThroughAnInterface = "called through an interface"
)

// registrars are the functions whose calls register routes: each function of the module that a
// route pattern matched, and the registrations of net/http. Carriers are the concrete types whose
// methods an interface can stand for: net/http's mux, and every type the module declares.
type registrars struct {
	isRouteWrapper func(*types.Func) bool
	carriers       []types.Type
	// names are the names the registrars go by. Only an interface method of one of them can stand
	// for a registrar, so a call such as err.Error() costs no lookup at all.
	names map[string]bool
	// lookUp is types.LookupFieldOrMethod, the scan's unit of work, which a test counts.
	lookUp func(carrier types.Type, addressable bool, inPackage *types.Package, name string) (types.Object, []int, bool)
}

func (reader routeReader) registrars() registrars {
	return registrars{
		isRouteWrapper: reader.isRouteWrapper,
		carriers:       carriersOf(reader.service.packages),
		names:          reader.registrarNames(),
		lookUp:         types.LookupFieldOrMethod,
	}
}

func (reader routeReader) registrarNames() map[string]bool {
	names := map[string]bool{}
	for _, registration := range registrationFunctions {
		names[registration[strings.LastIndex(registration, ".")+1:]] = true
	}
	for wrapper := range reader.wrappers[routeKind] {
		if reader.isRouteWrapper(wrapper) {
			names[wrapper.Name()] = true
		}
	}
	return names
}

// usesOutOfSight finds every use of a registrar that is no direct call, and every use of an
// interface's method that a registrar's type satisfies. A call ARDE cannot see is a route it cannot
// list, so each of them stops the run.
func (reader routeReader) usesOutOfSight() []unreadSite {
	return reader.registrars().usesOutOfSightIn(reader.service)
}

func (known registrars) usesOutOfSightIn(service loadedService) []unreadSite {
	found := []unreadSite{}
	for _, loaded := range service.packages {
		for _, file := range loaded.Syntax {
			called := directCallees(file, loaded.TypesInfo)
			ast.Inspect(file, func(node ast.Node) bool {
				identifier, isIdentifier := node.(*ast.Ident)
				if !isIdentifier {
					return true
				}
				function, isFunction := loaded.TypesInfo.Uses[identifier].(*types.Func)
				if !isFunction {
					return true
				}
				use, isOutOfSight := known.useOutOfSight(function, called[identifier])
				if place, isInTheRepo := service.placeOf(identifier.Pos()); isOutOfSight && isInTheRepo {
					use.sourcePlace = place
					found = append(found, use)
				}
				return true
			})
		}
	}
	return found
}

// useOutOfSight is the unread site a use of the function makes, still without its place.
func (known registrars) useOutOfSight(function *types.Func, isCalledDirectly bool) (unreadSite, bool) {
	if isInterfaceMethod(function) && known.names[function.Name()] {
		if behind := known.carriersBehind(function); len(behind) > 0 {
			return unreadSite{Problem: calledThroughAnInterface, Through: throughTheInterfaceOf(function, behind)}, true
		}
	}
	if known.hold(function) && !isCalledDirectly {
		return unreadSite{Problem: usedAsAValue}, true
	}
	return unreadSite{}, false
}

func (known registrars) hold(function *types.Func) bool {
	origin := function.Origin()
	return known.isRouteWrapper(origin) || slices.Contains(registrationFunctions, origin.FullName())
}

// carriersBehind are the carriers that satisfy the interface that declares the method and answer
// its name with a registrar, their own or one they embed. A generic type is not compared whole,
// because what it satisfies depends on its type arguments, so its method's name decides alone.
func (known registrars) carriersBehind(interfaceMethod *types.Func) []types.Type {
	declaring, isInterface := interfaceMethod.Signature().Recv().Type().Underlying().(*types.Interface)
	if !isInterface {
		return nil
	}
	behind := []types.Type{}
	for _, carrier := range known.carriers {
		method, _, _ := known.lookUp(carrier, false, interfaceMethod.Pkg(), interfaceMethod.Name())
		function, isFunction := method.(*types.Func)
		if isFunction && known.hold(function) && (isGeneric(carrier) || types.Implements(carrier, declaring)) {
			behind = append(behind, carrier)
		}
	}
	return behind
}

// throughAnInterface names a call through an interface that a registrar's type satisfies: the
// method called, the interface and each type behind it, with packages named as a person reads
// them, so the stop says which registration to make direct.
type throughAnInterface struct {
	Method      string   `json:"method"`
	Interface   string   `json:"interface"`
	SatisfiedBy []string `json:"satisfied_by"`
}

func throughTheInterfaceOf(interfaceMethod *types.Func, behind []types.Type) *throughAnInterface {
	byPackageName := func(named *types.Package) string { return named.Name() }
	satisfiedBy := []string{}
	for _, carrier := range behind {
		satisfiedBy = append(satisfiedBy, types.TypeString(carrier, byPackageName))
	}
	slices.Sort(satisfiedBy)
	return &throughAnInterface{
		Method:      interfaceMethod.Name(),
		Interface:   types.TypeString(interfaceMethod.Signature().Recv().Type(), byPackageName),
		SatisfiedBy: slices.Compact(satisfiedBy),
	}
}

func isGeneric(carrier types.Type) bool {
	named := namedBehind(carrier)
	return named != nil && named.TypeParams().Len() > 0 && named.TypeArgs().Len() == 0
}

// carriersOf is net/http's mux and every named type of the module that is no interface, with a
// pointer to it, local types included. An anonymous struct that embeds a registrar is not one.
func carriersOf(loaded []*packages.Package) []types.Type {
	carriers := []types.Type{}
	packages.Visit(loaded, nil, func(visited *packages.Package) {
		if mux, isType := muxIn(visited); isType {
			carriers = append(carriers, types.NewPointer(mux))
		}
	})
	for _, service := range loaded {
		for _, defined := range service.TypesInfo.Defs {
			name, isTypeName := defined.(*types.TypeName)
			if !isTypeName || name.IsAlias() || types.IsInterface(name.Type()) {
				continue
			}
			carriers = append(carriers, name.Type(), types.NewPointer(name.Type()))
		}
	}
	return carriers
}

func muxIn(visited *packages.Package) (types.Type, bool) {
	if visited.PkgPath != "net/http" || visited.Types == nil {
		return nil, false
	}
	mux, isTypeName := visited.Types.Scope().Lookup("ServeMux").(*types.TypeName)
	if !isTypeName {
		return nil, false
	}
	return mux.Type(), true
}

// namedBehind is the named type a value has, through one pointer, or nil for any other type.
func namedBehind(valueType types.Type) *types.Named {
	if pointer, isPointer := types.Unalias(valueType).(*types.Pointer); isPointer {
		valueType = pointer.Elem()
	}
	named, _ := types.Unalias(valueType).(*types.Named)
	return named
}

// directCallees are the names the file's calls go to directly: a function, an instance of a generic
// one, a method called on a value and a function of another package. A method expression, such as
// (*app).get, takes the receiver as its first argument, so a pattern's positions would not fit it.
func directCallees(file *ast.File, info *types.Info) map[*ast.Ident]bool {
	callees := map[*ast.Ident]bool{}
	ast.Inspect(file, func(node ast.Node) bool {
		if call, isCall := node.(*ast.CallExpr); isCall {
			if callee := directCallee(call.Fun, info); callee != nil {
				callees[callee] = true
			}
		}
		return true
	})
	return callees
}

func directCallee(function ast.Expr, info *types.Info) *ast.Ident {
	switch typed := ast.Unparen(function).(type) {
	case *ast.Ident:
		return typed
	case *ast.IndexExpr:
		return directCallee(typed.X, info)
	case *ast.IndexListExpr:
		return directCallee(typed.X, info)
	case *ast.SelectorExpr:
		if selection := info.Selections[typed]; selection == nil || selection.Kind() == types.MethodVal {
			return typed.Sel
		}
	}
	return nil
}

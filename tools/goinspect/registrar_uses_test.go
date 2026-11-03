package main

import (
	"fmt"
	"go/types"
	"strings"
	"testing"
)

// aServiceWithManyTypes declares the types, each with a method, makes as many ordinary calls
// through an interface as asked, such as err.Error(), and makes one call through an interface whose
// method a mux answers, which the scan has to look at.
func aServiceWithManyTypes(typeCount, ordinaryCalls int) string {
	var source strings.Builder
	source.WriteString("package main\n\nimport (\n\t\"errors\"\n\t\"net/http\"\n)\n\n")
	source.WriteString("type adder interface {\n\tHandleFunc(pattern string, handler func(http.ResponseWriter, *http.Request))\n}\n\n")
	for number := range typeCount {
		fmt.Fprintf(&source, "type t%d struct{ v string }\n\nfunc (x *t%d) Get() string { return x.v }\n\n", number, number)
	}
	source.WriteString("func parcels(w http.ResponseWriter, r *http.Request) {}\n\nfunc main() {\n\terr := errors.New(\"x\")\n")
	for range ordinaryCalls {
		source.WriteString("\t_ = err.Error()\n")
	}
	source.WriteString("\tvar m adder = http.NewServeMux()\n\tm.HandleFunc(\"/x\", parcels)\n}\n")
	return source.String()
}

func lookupsOfTheInterfaceScan(t *testing.T, ordinaryCalls int) int {
	t.Helper()
	root := writeModule(t, "example.com/scan", map[string]string{"main.go": aServiceWithManyTypes(40, ordinaryCalls)})
	service, err := loadService(root, ".")
	assertNoError(t, err)
	known := routeReader{service: service, wrappers: wrappersByKind{}}.registrars()
	lookups := 0
	known.lookUp = func(carrier types.Type, addressable bool, inPackage *types.Package, name string) (types.Object, []int, bool) {
		lookups++
		return types.LookupFieldOrMethod(carrier, addressable, inPackage, name)
	}
	if found := known.usesOutOfSightIn(service); len(found) != 1 || found[0].Problem != calledThroughAnInterface {
		t.Fatalf("want the call through the mux's interface, got %+v", found)
	}
	return lookups
}

// The reviewer's I-4: every use of an interface method looked at every carrier, so the scan cost
// grew with err.Error(), w.Header() and ctx.Done() times the module's types.
func TestTheInterfaceScanLooksOnlyAtMethodsNamedAsARegistrarIs(t *testing.T) {
	few, many := lookupsOfTheInterfaceScan(t, 1), lookupsOfTheInterfaceScan(t, 300)

	if few == 0 || few != many {
		t.Fatalf("lookups: %d beside one ordinary call, %d beside 300", few, many)
	}
}

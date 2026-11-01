package main

import (
	"bytes"
	"crypto/sha256"
	"encoding/json"
	"fmt"
	"go/ast"
	"go/parser"
	"go/token"
	"os"
	"path/filepath"
	"reflect"
	"strings"
)

var posType = reflect.TypeOf(token.Pos(0))

func hash(n any) string {
	var b bytes.Buffer
	ast.Fprint(&b, nil, n, func(_ string, v reflect.Value) bool { return v.Type() != posType })
	return fmt.Sprintf("%x", sha256.Sum256(b.Bytes()))[:12]
}

func exprName(e ast.Expr) string {
	switch t := e.(type) {
	case *ast.StarExpr:
		return exprName(t.X)
	case *ast.Ident:
		return t.Name
	}
	return "?"
}

func decls(root string) map[string]string {
	out := map[string]string{}
	filepath.Walk(root, func(p string, i os.FileInfo, _ error) error {
		if i == nil || i.IsDir() || !strings.HasSuffix(p, ".go") {
			return nil
		}
		fset := token.NewFileSet()
		f, err := parser.ParseFile(fset, p, nil, parser.SkipObjectResolution)
		if err != nil {
			return nil
		}
		rel, _ := filepath.Rel(root, p)
		for _, d := range f.Decls {
			switch v := d.(type) {
			case *ast.FuncDecl:
				name := v.Name.Name
				if v.Recv != nil {
					name = exprName(v.Recv.List[0].Type) + "." + name
				}
				out[rel+"::func "+name] = hash(v)
			case *ast.GenDecl:
				for _, s := range v.Specs {
					switch sp := s.(type) {
					case *ast.TypeSpec:
						out[rel+"::type "+sp.Name.Name] = hash(sp)
					case *ast.ValueSpec:
						for _, n := range sp.Names {
							out[rel+"::"+v.Tok.String()+" "+n.Name] = hash(sp)
						}
					}
				}
			}
		}
		return nil
	})
	return out
}

func main() {
	a, b := decls(os.Args[1]), decls(os.Args[2])
	changed := []string{}
	for k, v := range b {
		if old, ok := a[k]; !ok {
			changed = append(changed, "+"+k)
		} else if old != v {
			changed = append(changed, "~"+k)
		}
	}
	for k := range a {
		if _, ok := b[k]; !ok {
			changed = append(changed, "-"+k)
		}
	}
	json.NewEncoder(os.Stdout).Encode(changed)
}

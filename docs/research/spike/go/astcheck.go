package main

import (
	"bytes"
	"crypto/sha256"
	"fmt"
	"go/ast"
	"go/parser"
	"go/token"
	"os"
	"path/filepath"
	"reflect"
	"sort"
	"strings"
)

var posType = reflect.TypeOf(token.Pos(0))

func dump(path string) string {
	fset := token.NewFileSet()
	f, err := parser.ParseFile(fset, path, nil, parser.SkipObjectResolution)
	if err != nil {
		panic(err)
	}
	f.Doc, f.Comments = nil, nil
	ast.Inspect(f, func(n ast.Node) bool {
		switch v := n.(type) {
		case *ast.FuncDecl:
			v.Doc = nil
		case *ast.GenDecl:
			v.Doc = nil
		case *ast.TypeSpec:
			v.Doc, v.Comment = nil, nil
		case *ast.ValueSpec:
			v.Doc, v.Comment = nil, nil
		case *ast.ImportSpec:
			v.Doc, v.Comment = nil, nil
		case *ast.Field:
			v.Doc, v.Comment = nil, nil
		}
		return true
	})
	var b bytes.Buffer
	ast.Fprint(&b, nil, f, func(_ string, v reflect.Value) bool { return v.Type() != posType })
	return b.String()
}

func hashes(root string) map[string]string {
	out := map[string]string{}
	filepath.Walk(root, func(p string, i os.FileInfo, _ error) error {
		if i != nil && !i.IsDir() && strings.HasSuffix(p, ".go") {
			r, _ := filepath.Rel(root, p)
			out[r] = fmt.Sprintf("%x", sha256.Sum256([]byte(dump(p))))
		}
		return nil
	})
	return out
}

func main() {
	a, b := hashes(os.Args[1]), hashes(os.Args[2])
	keys := []string{}
	for k := range a {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	same := true
	for _, k := range keys {
		if a[k] != b[k] {
			same = false
			fmt.Println("  DIFF:", k)
		}
	}
	for k := range b {
		if _, ok := a[k]; !ok {
			same = false
			fmt.Println("  NEW :", k)
		}
	}
	fmt.Println("  identical:", same)
}

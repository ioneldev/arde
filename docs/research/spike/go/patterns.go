package main

import (
	"bytes"
	"encoding/json"
	"fmt"
	"go/ast"
	"go/parser"
	"go/printer"
	"go/token"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
)

type pattern struct {
	Kind string         `json:"kind"`
	Call string         `json:"call"`
	Args map[string]int `json:"args"`
}

func text(fset *token.FileSet, e ast.Expr) string {
	var b bytes.Buffer
	printer.Fprint(&b, fset, e)
	return b.String()
}

func main() {
	raw, _ := os.ReadFile(os.Args[2])
	var patterns []pattern
	if err := json.Unmarshal(raw, &patterns); err != nil {
		panic(err)
	}
	counts := map[string]int{}
	fset := token.NewFileSet()
	filepath.Walk(os.Args[1], func(p string, i os.FileInfo, _ error) error {
		if i == nil || i.IsDir() || !strings.HasSuffix(p, ".go") || strings.HasSuffix(p, "_test.go") {
			return nil
		}
		f, err := parser.ParseFile(fset, p, nil, 0)
		if err != nil {
			return nil
		}
		ast.Inspect(f, func(n ast.Node) bool {
			c, ok := n.(*ast.CallExpr)
			if !ok {
				return true
			}
			name := text(fset, c.Fun)
			for _, pt := range patterns {
				if !regexp.MustCompile(pt.Call).MatchString(name) {
					continue
				}
				keys := make([]string, 0, len(pt.Args))
				for k := range pt.Args {
					keys = append(keys, k)
				}
				sort.Strings(keys)
				parts := []string{}
				for _, k := range keys {
					if idx := pt.Args[k]; idx < len(c.Args) {
						parts = append(parts, k+"="+text(fset, c.Args[idx]))
					}
				}
				counts[pt.Kind]++
				fmt.Printf("  %-6s %-22s %s\n", pt.Kind, name, strings.Join(parts, " "))
			}
			return true
		})
		return nil
	})
	if counts["route"] == 0 {
		fmt.Println("  STOP: zero routes matched; refusing to treat an empty inventory as a pass")
		os.Exit(1)
	}
	fmt.Println("  counts:", counts)
}

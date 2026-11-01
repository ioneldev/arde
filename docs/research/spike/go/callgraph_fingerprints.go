package main

import (
	"bytes"
	"crypto/sha256"
	"fmt"
	"os"
	"sort"
	"strings"

	"golang.org/x/tools/go/callgraph"
	"golang.org/x/tools/go/callgraph/static"
	"golang.org/x/tools/go/packages"
	"golang.org/x/tools/go/ssa"
	"golang.org/x/tools/go/ssa/ssautil"
)

const modulePrefix = "catalog/"

func inModule(f *ssa.Function) bool {
	return f != nil && f.Pkg != nil && strings.HasPrefix(f.Pkg.Pkg.Path(), modulePrefix) && !strings.HasPrefix(f.Name(), "init")
}

func body(f *ssa.Function) string {
	var b bytes.Buffer
	f.WriteTo(&b)
	var keep []string
	for _, l := range strings.Split(b.String(), "\n") {
		if !strings.HasPrefix(l, "# ") {
			keep = append(keep, l)
		}
	}
	return strings.Join(keep, "\n")
}

func closure(g *callgraph.Graph, root *ssa.Function) []*ssa.Function {
	seen := map[*ssa.Function]bool{}
	var walk func(*callgraph.Node)
	walk = func(n *callgraph.Node) {
		if n == nil || seen[n.Func] || !inModule(n.Func) {
			return
		}
		seen[n.Func] = true
		for _, e := range n.Out {
			walk(e.Callee)
		}
	}
	walk(g.Nodes[root])
	out := make([]*ssa.Function, 0, len(seen))
	for f := range seen {
		out = append(out, f)
	}
	sort.Slice(out, func(i, j int) bool { return out[i].String() < out[j].String() })
	return out
}

func main() {
	cfg := &packages.Config{Mode: packages.LoadAllSyntax, Dir: os.Args[1]}
	pkgs, err := packages.Load(cfg, "./...")
	if err != nil {
		panic(err)
	}
	prog, _ := ssautil.AllPackages(pkgs, ssa.BuilderMode(0))
	prog.Build()
	g := static.CallGraph(prog)

	funcs, edges := 0, 0
	for f, n := range g.Nodes {
		if inModule(f) {
			funcs++
			for _, e := range n.Out {
				if inModule(e.Callee.Func) {
					edges++
				}
			}
		}
	}
	if len(os.Args) > 2 {
		fmt.Printf("module functions: %d, internal call edges: %d\n", funcs, edges)
	}
	roots := []string{}
	byName := map[string]*ssa.Function{}
	for f := range g.Nodes {
		if inModule(f) && strings.Contains(f.String(), "handlers).") {
			roots = append(roots, f.String())
			byName[f.String()] = f
		}
	}
	sort.Strings(roots)
	for _, r := range roots {
		c := closure(g, byName[r])
		h := sha256.New()
		names := []string{}
		for _, f := range c {
			h.Write([]byte(body(f)))
			names = append(names, strings.TrimPrefix(strings.TrimPrefix(f.String(), "catalog/internal/"), "(catalog/internal/"))
		}
		short := r[strings.LastIndex(r, ".")+1:]
		fmt.Printf("%-8s %x  (%d funcs)", short, h.Sum(nil)[:5], len(c))
		if len(os.Args) > 2 {
			fmt.Printf("  %s", strings.Join(names, ", "))
		}
		fmt.Println()
	}
}

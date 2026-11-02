package main

import (
	"os"
	"path/filepath"
	"testing"
)

// writeTree creates the files under a fresh temporary directory and returns its path.
func writeTree(t *testing.T, files map[string]string) string {
	t.Helper()
	root := t.TempDir()
	writeFiles(t, root, files)
	return root
}

func writeFiles(t *testing.T, root string, files map[string]string) {
	t.Helper()
	for relativePath, content := range files {
		absolutePath := filepath.Join(root, filepath.FromSlash(relativePath))
		if err := os.MkdirAll(filepath.Dir(absolutePath), 0o755); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(absolutePath, []byte(content), 0o644); err != nil {
			t.Fatal(err)
		}
	}
}

func assertNoError(t *testing.T, err error) {
	t.Helper()
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
}

// syntaxCase is an edit that changes what a program means while the parser records the
// difference only as a token position: the "=" of an alias and the "..." of a spread.
type syntaxCase struct {
	before    string
	after     string
	changedID string
}

var syntaxHeldInPositions = map[string]syntaxCase{
	"alias becomes a new type": {
		before:    "package shop\n\ntype E struct{}\n\ntype F = E\n",
		after:     "package shop\n\ntype E struct{}\n\ntype F E\n",
		changedID: "shop.go::type F",
	},
	"spread argument becomes a plain one": {
		before:    "package shop\n\nfunc show(items []any) {\n\tfmt.Println(items...)\n}\n",
		after:     "package shop\n\nfunc show(items []any) {\n\tfmt.Println(items)\n}\n",
		changedID: "shop.go::func show",
	},
	"append spread becomes a plain argument": {
		before:    "package shop\n\nfunc join(a, b []int) []int {\n\treturn append(a, b...)\n}\n",
		after:     "package shop\n\nfunc join(a, b []int) []int {\n\treturn append(a, b)\n}\n",
		changedID: "shop.go::func join",
	},
	"generic alias becomes a new type": {
		before:    "package shop\n\ntype L[T any] = []T\n",
		after:     "package shop\n\ntype L[T any] []T\n",
		changedID: "shop.go::type L",
	},
}

// sameSyntaxMoved holds each of those constructs, plus the "..." forms that carry no meaning of
// their own, and the same code laid out differently.
const sameSyntaxMovedBefore = `package shop

type E struct{}

type F = E

type L[T any] = []T

var table = [...]int{1, 2, 3}

func variadic(xs ...int) {}

func show(items []any) {
	fmt.Println(items...)
}
`

const sameSyntaxMovedAfter = `package shop

// Comments and blank lines move every position below them.

type E struct{}


type   F   =   E

type L[T any]   =   []T


var table = [...]int{
	1, 2, 3,
}

func variadic(
	xs ...int,
) {}

func show(items []any) {
	fmt.Println(
		items...,
	)
}
`

package main

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"go/ast"
	"go/parser"
	"go/token"
	"reflect"
	"strconv"
	"strings"
)

// parsedSource keeps the file set, because token positions mean nothing without it.
type parsedSource struct {
	syntax  *ast.File
	fileSet *token.FileSet
}

// parseSource leaves comments out of the tree by not asking for them: parser.ParseComments is
// absent from the mode. SkipObjectResolution keeps Ident.Obj empty; with it filled in, a dump of
// a function would contain the body of every same-file declaration it calls, so editing a helper
// would change its callers' hashes. path is only used to name the file in errors.
func parseSource(path string, source []byte) (*parsedSource, error) {
	fileSet := token.NewFileSet()
	syntax, err := parser.ParseFile(fileSet, path, source, parser.SkipObjectResolution)
	if err != nil {
		return nil, err
	}
	return &parsedSource{syntax: syntax, fileSet: fileSet}, nil
}

// lineOf reports the line in the file as written. PositionFor(pos, false) ignores //line
// directives, which generated code uses to point at another file.
func (source *parsedSource) lineOf(pos token.Pos) int {
	return source.fileSet.PositionFor(pos, false).Line
}

// normalisedFingerprint says whether two sources mean the same thing to ARDE: it ignores
// comments, formatting, line endings and the tags ARDE adds. It is the one normaliser that both
// compare and the declaration hashes rely on.
func normalisedFingerprint(path string, source []byte) (string, error) {
	parsed, err := parseSource(path, source)
	if err != nil {
		return "", err
	}
	normaliseTree(parsed.syntax)
	return fingerprint(parsed.syntax), nil
}

// normaliseTree is the step every command runs on syntax before it hashes anything in it. It
// changes the tree in place and is safe to run twice.
func normaliseTree(tree ast.Node) {
	dropTagsAddedByArde(tree)
	markSyntaxHeldInPositions(tree)
}

var positionType = reflect.TypeOf(token.Pos(0))

// omitPositions drops every token.Pos, so moving code does not change a hash, except the three
// fields (under two names, Assign and Ellipsis) that markSyntaxHeldInPositions has reduced to
// present or absent.
func omitPositions(fieldName string, value reflect.Value) bool {
	return value.Type() != positionType || fieldName == "Assign" || fieldName == "Ellipsis"
}

// markSyntaxHeldInPositions sets the positions of "=" in an alias and "..." in a spread argument,
// a variadic parameter and a [...] array to a fixed value, or to none when the syntax is absent.
// The parser records `type A = B` and `f(xs...)` only as a valid position in those fields, so
// dropping them would make an alias equal a new type and a spread call equal a plain one.
func markSyntaxHeldInPositions(tree ast.Node) {
	ast.Inspect(tree, func(node ast.Node) bool {
		switch node := node.(type) {
		case *ast.TypeSpec:
			node.Assign = presence(node.Assign)
		case *ast.CallExpr:
			node.Ellipsis = presence(node.Ellipsis)
		case *ast.Ellipsis:
			node.Ellipsis = presence(node.Ellipsis)
		}
		return true
	})
}

func presence(pos token.Pos) token.Pos {
	if pos.IsValid() {
		return 1
	}
	return token.NoPos
}

// fingerprint hashes a dump of the nodes. Each dump closes its own braces, so joining several
// cannot make two different lists read the same.
func fingerprint(nodes ...any) string {
	var dump bytes.Buffer
	for _, node := range nodes {
		if err := ast.Fprint(&dump, nil, node, omitPositions); err != nil {
			panic(fmt.Sprintf("dumping a syntax tree into memory cannot fail: %v", err))
		}
	}
	sum := sha256.Sum256(dump.Bytes())
	return hex.EncodeToString(sum[:])
}

// dropTagsAddedByArde rewrites every struct tag in the tree. An unannotated field and an
// annotated one must come out the same, so a tag with nothing left becomes no tag at all.
func dropTagsAddedByArde(tree ast.Node) {
	ast.Inspect(tree, func(node ast.Node) bool {
		if field, isField := node.(*ast.Field); isField && field.Tag != nil {
			dropTagsFromField(field)
		}
		return true
	})
}

func dropTagsFromField(field *ast.Field) {
	tag, err := strconv.Unquote(field.Tag.Value)
	if err != nil {
		// The scanner has already accepted this literal, so this is not expected. If it happens
		// the tag stays as written, and any edit to it counts as a change.
		return
	}
	pairs, wellFormed := splitStructTag(tag)
	if !wellFormed {
		// An unreadable tag stays as written, so any edit to it counts as a change.
		return
	}
	kept := []string{}
	for _, pair := range pairs {
		if !isAddedByArde(pair) {
			kept = append(kept, pair)
		}
	}
	if len(kept) == 0 {
		field.Tag = nil
		return
	}
	field.Tag.Value = strconv.Quote(strings.Join(kept, " "))
}

// isAddedByArde matches whole keys, spelled the way ARDE writes them: any oneOf, and binding
// "optional" and nothing else, because binding "required" changes what the service accepts. A
// value spelled another way, such as with an escape, counts as a change.
func isAddedByArde(pair string) bool {
	return strings.HasPrefix(pair, `oneOf:"`) || pair == `binding:"optional"`
}

// splitStructTag cuts a tag into its `key:"value"` pairs the way reflect.StructTag reads them. It
// reports false for a tag reflect would stop reading part way, and for a repeated key: reflect
// reads only the first of those, so dropping one could change what the tag means.
func splitStructTag(tag string) ([]string, bool) {
	pairs := []string{}
	keys := map[string]bool{}
	for rest := strings.TrimLeft(tag, " "); rest != ""; rest = strings.TrimLeft(rest, " ") {
		keyLength := tagKeyLength(rest)
		key := rest[:keyLength]
		if keyLength == 0 || !strings.HasPrefix(rest[keyLength:], `:"`) || keys[key] {
			return nil, false
		}
		quoted, err := strconv.QuotedPrefix(rest[keyLength+1:])
		if err != nil {
			return nil, false
		}
		keys[key] = true
		pairLength := keyLength + 1 + len(quoted)
		pairs = append(pairs, rest[:pairLength])
		rest = rest[pairLength:]
	}
	return pairs, true
}

func tagKeyLength(text string) int {
	length := 0
	for length < len(text) && text[length] > ' ' && text[length] != ':' && text[length] != '"' && text[length] != 0x7f {
		length++
	}
	return length
}

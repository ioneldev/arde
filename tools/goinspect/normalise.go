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
	dropTagsAddedByArde(parsed.syntax)
	return fingerprint(parsed.syntax), nil
}

var positionType = reflect.TypeOf(token.Pos(0))

func omitPositions(_ string, value reflect.Value) bool {
	return value.Type() != positionType
}

// fingerprint hashes a dump of the nodes with every token.Pos left out, so moving code does not
// change it. Each dump closes its own braces, so joining several cannot make two different
// lists read the same.
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
func dropTagsAddedByArde(file *ast.File) {
	ast.Inspect(file, func(node ast.Node) bool {
		if field, isField := node.(*ast.Field); isField && field.Tag != nil {
			dropTagsFromField(field)
		}
		return true
	})
}

func dropTagsFromField(field *ast.Field) {
	tag, err := unquoteTag(field.Tag)
	if err != nil {
		return
	}
	pairs, wellFormed := parseStructTag(tag)
	if !wellFormed {
		// An unreadable tag stays as written, so any edit to it counts as a change.
		return
	}
	kept := []string{}
	for _, pair := range pairs {
		if !pair.isAddedByArde() {
			kept = append(kept, pair.text())
		}
	}
	if len(kept) == 0 {
		field.Tag = nil
		return
	}
	field.Tag.Value = strconv.Quote(strings.Join(kept, " "))
}

func unquoteTag(literal *ast.BasicLit) (string, error) {
	return strconv.Unquote(literal.Value)
}

type tagPair struct {
	key    string
	quoted string
	value  string
}

func (pair tagPair) text() string {
	return pair.key + ":" + pair.quoted
}

// isAddedByArde matches whole keys only: a key that merely ends in oneOf is someone else's.
// Of the binding values, only optional is ours: required changes what the service accepts.
func (pair tagPair) isAddedByArde() bool {
	switch pair.key {
	case "oneOf":
		return true
	case "binding":
		return pair.value == "optional"
	default:
		return false
	}
}

// parseStructTag reads `key:"value" key:"value"` the way reflect.StructTag does. It reports
// false for a tag reflect would stop reading part way, so the caller can leave it alone.
func parseStructTag(tag string) ([]tagPair, bool) {
	pairs := []tagPair{}
	rest := tag
	for {
		rest = strings.TrimLeft(rest, " ")
		if rest == "" {
			return pairs, true
		}
		keyLength := tagKeyLength(rest)
		if keyLength == 0 || len(rest) < keyLength+2 || rest[keyLength] != ':' || rest[keyLength+1] != '"' {
			return nil, false
		}
		key := rest[:keyLength]
		quoted, remainder, closed := cutQuotedString(rest[keyLength+1:])
		if !closed {
			return nil, false
		}
		value, err := strconv.Unquote(quoted)
		if err != nil {
			return nil, false
		}
		pairs = append(pairs, tagPair{key: key, quoted: quoted, value: value})
		rest = remainder
	}
}

func tagKeyLength(text string) int {
	length := 0
	for length < len(text) && text[length] > ' ' && text[length] != ':' && text[length] != '"' && text[length] != 0x7f {
		length++
	}
	return length
}

// cutQuotedString splits the double-quoted string at the start of text from what follows it.
func cutQuotedString(text string) (quoted, rest string, closed bool) {
	for index := 1; index < len(text); index++ {
		switch text[index] {
		case '\\':
			index++
		case '"':
			return text[:index+1], text[index+1:], true
		}
	}
	return "", "", false
}

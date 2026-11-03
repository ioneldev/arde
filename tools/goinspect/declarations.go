package main

import (
	"fmt"
	"go/ast"
	"go/token"
	"go/types"
	"reflect"
	"strconv"
	"unicode/utf8"
)

type declaration struct {
	File string `json:"file"`
	Kind string `json:"kind"`
	Name string `json:"name"`
	Hash string `json:"hash"`
	Line int    `json:"line"`
}

type structField struct {
	File    string `json:"file"`
	Package string `json:"package"`
	Struct  string `json:"struct"`
	Name    string `json:"name"`
	Line    int    `json:"line"`
	Tag     string `json:"tag"`
	// JSONTag is the value of the tag's json key, and null when the tag has none.
	JSONTag  *string `json:"json_tag"`
	GoType   string  `json:"go_type"`
	Embedded bool    `json:"embedded"`
}

type declarationReport struct {
	Declarations []declaration `json:"declarations"`
	Fields       []structField `json:"fields"`
}

// inspectDeclarations lists files in path order and each file's entries in source order, which
// is the sorted order the report promises.
func inspectDeclarations(root string) (declarationReport, error) {
	files, err := listGoFiles(root, skipWhatTheGoToolIgnores)
	if err != nil {
		return declarationReport{}, err
	}
	report := declarationReport{Declarations: []declaration{}, Fields: []structField{}}
	for _, path := range files {
		fileReport, err := inspectFile(root, path)
		if err != nil {
			return declarationReport{}, err
		}
		report.Declarations = append(report.Declarations, fileReport.Declarations...)
		report.Fields = append(report.Fields, fileReport.Fields...)
	}
	return report, nil
}

func inspectFile(root, path string) (declarationReport, error) {
	content, err := readGoFile(root, path)
	if err != nil {
		return declarationReport{}, err
	}
	parsed, err := parseSource(path, content)
	if err != nil {
		return declarationReport{}, fmt.Errorf(
			"cannot parse %s, which would leave its declarations out of the inventory; fix the syntax error and run again: %w",
			path, err)
	}
	// Fields report tags as written, so they are read before the tree is normalised for hashing.
	fields := structFieldsIn(path, parsed)
	normaliseTree(parsed.syntax)
	return declarationReport{Declarations: declarationsIn(path, parsed), Fields: fields}, nil
}

type foundDeclaration struct {
	kind string
	name string
	hash string
	pos  token.Pos
}

func declarationsIn(path string, source *parsedSource) []declaration {
	timesSeen := map[string]int{}
	declarations := []declaration{}
	for _, found := range foundDeclarationsIn(source.syntax) {
		declarations = append(declarations, declaration{
			File: path,
			Kind: found.kind,
			Name: found.uniqueName(timesSeen),
			Hash: found.hash,
			Line: source.lineOf(found.pos),
		})
	}
	return declarations
}

// uniqueName keeps ids unique within a file. Go allows several init functions and several
// blank identifiers in one file, and each must stay separately addressable.
func (found foundDeclaration) uniqueName(timesSeen map[string]int) string {
	key := found.kind + " " + found.name
	timesSeen[key]++
	if timesSeen[key] == 1 {
		return found.name
	}
	return fmt.Sprintf("%s#%d", found.name, timesSeen[key])
}

func foundDeclarationsIn(syntax *ast.File) []foundDeclaration {
	found := []foundDeclaration{}
	for _, topLevel := range syntax.Decls {
		switch topLevel := topLevel.(type) {
		case *ast.FuncDecl:
			found = append(found, foundFunc(topLevel))
		case *ast.GenDecl:
			found = append(found, foundInGroup(topLevel)...)
		}
	}
	return found
}

func foundFunc(function *ast.FuncDecl) foundDeclaration {
	return foundDeclaration{
		kind: "func",
		name: functionName(function),
		hash: hashOfDump(function),
		pos:  function.Name.Pos(),
	}
}

func foundInGroup(group *ast.GenDecl) []foundDeclaration {
	switch group.Tok {
	case token.TYPE:
		return foundTypes(group)
	case token.VAR:
		return foundVars(group)
	case token.CONST:
		return foundConsts(group)
	}
	return nil
}

func foundTypes(group *ast.GenDecl) []foundDeclaration {
	found := []foundDeclaration{}
	for _, spec := range group.Specs {
		typeSpec := spec.(*ast.TypeSpec)
		found = append(found, foundDeclaration{
			kind: "type",
			name: typeSpec.Name.Name,
			hash: hashOfDump(typeSpec),
			pos:  typeSpec.Name.Pos(),
		})
	}
	return found
}

func foundVars(group *ast.GenDecl) []foundDeclaration {
	found := []foundDeclaration{}
	for _, spec := range group.Specs {
		valueSpec := spec.(*ast.ValueSpec)
		hash := hashOfDump(valueSpec)
		for _, name := range valueSpec.Names {
			found = append(found, foundDeclaration{kind: "var", name: name.Name, hash: hash, pos: name.Pos()})
		}
	}
	return found
}

func foundConsts(group *ast.GenDecl) []foundDeclaration {
	found := []foundDeclaration{}
	var lastValueSource *ast.ValueSpec
	for index, spec := range group.Specs {
		valueSpec := spec.(*ast.ValueSpec)
		if valueSpec.Type != nil || len(valueSpec.Values) > 0 {
			lastValueSource = valueSpec
		}
		meaning := constMeaning{spec: valueSpec, valueSource: lastValueSource, iotaValue: index}
		hash := meaning.hash()
		for _, name := range valueSpec.Names {
			found = append(found, foundDeclaration{kind: "const", name: name.Name, hash: hash, pos: name.Pos()})
		}
	}
	return found
}

func functionName(function *ast.FuncDecl) string {
	if function.Recv == nil || len(function.Recv.List) == 0 {
		return function.Name.Name
	}
	return typeBaseName(function.Recv.List[0].Type) + "." + function.Name.Name
}

// constMeaning is what a const spec means once Go's implicit repetition is applied: a spec with
// no type and no value repeats the type and values of the last spec that stated them, with its
// own iota. Hashing the spec as written would miss an edit to the spec it repeats. valueSource is
// the spec whose type and values apply: the spec itself, or the last earlier spec that stated them.
type constMeaning struct {
	spec        *ast.ValueSpec
	valueSource *ast.ValueSpec
	iotaValue   int
}

func (meaning constMeaning) hash() string {
	parts := []any{meaning.spec}
	valueSource := meaning.valueSource
	if valueSource == nil {
		valueSource = meaning.spec
	}
	if valueSource != meaning.spec {
		parts = append(parts, valueSource.Type, valueSource.Values)
	}
	if mentionsIota(valueSource.Values) {
		parts = append(parts, meaning.iotaValue)
	}
	return hashOfDump(parts...)
}

func mentionsIota(values []ast.Expr) bool {
	found := false
	for _, value := range values {
		ast.Inspect(value, func(node ast.Node) bool {
			if ident, isIdent := node.(*ast.Ident); isIdent && ident.Name == "iota" {
				found = true
			}
			return !found
		})
	}
	return found
}

func structFieldsIn(path string, source *parsedSource) []structField {
	fields := []structField{}
	for _, topLevel := range source.syntax.Decls {
		group, isGenDecl := topLevel.(*ast.GenDecl)
		if !isGenDecl || group.Tok != token.TYPE {
			continue
		}
		for _, spec := range group.Specs {
			typeSpec := spec.(*ast.TypeSpec)
			structType, isStruct := ast.Unparen(typeSpec.Type).(*ast.StructType)
			if isStruct {
				owner := structField{File: path, Package: source.syntax.Name.Name, Struct: typeSpec.Name.Name}
				fields = append(fields, source.fieldsOf(owner, structType)...)
			}
		}
	}
	return fields
}

// fieldsOf reads a struct's fields. owner holds what they all share.
func (source *parsedSource) fieldsOf(owner structField, structType *ast.StructType) []structField {
	fields := []structField{}
	for _, field := range structType.Fields.List {
		template := owner
		template.Tag = tagText(field)
		template.JSONTag = jsonKeyOf(template.Tag)
		template.GoType = boundedExprString(field.Type)
		fields = append(fields, source.namedAs(template, field)...)
	}
	return fields
}

// namedAs makes one record per declared name, or a single embedded record named after the type.
func (source *parsedSource) namedAs(template structField, field *ast.Field) []structField {
	if len(field.Names) == 0 {
		template.Name = typeBaseName(field.Type)
		template.Line = source.lineOf(field.Type.Pos())
		template.Embedded = true
		return []structField{template}
	}
	records := []structField{}
	for _, name := range field.Names {
		record := template
		record.Name = name.Name
		record.Line = source.lineOf(name.Pos())
		records = append(records, record)
	}
	return records
}

// tagText is the tag without the quotes of its Go literal. The scanner has already accepted the
// literal, so Unquote is not expected to fail; if it does, the literal is reported as written. A
// tag is repeated for each name in its field, so a long one costs its length many times over.
func tagText(field *ast.Field) string {
	if field.Tag == nil {
		return ""
	}
	if len(field.Tag.Value) > maxPrintedBytes {
		return tooLongToPrint
	}
	text, err := strconv.Unquote(field.Tag.Value)
	if err != nil {
		return field.Tag.Value
	}
	return text
}

// jsonKeyOf reads the tag the way encoding/json does, through reflect, so an escape in it decodes
// and text that only looks like a json key is passed over. A value that is not UTF-8 counts as no
// key: JSON cannot carry it, and encoding/json refuses such a name and uses the field's own.
func jsonKeyOf(tag string) *string {
	value, found := reflect.StructTag(tag).Lookup("json")
	if !found || !utf8.ValidString(value) {
		return nil
	}
	return &value
}

// typeBaseName is the name Go gives a method's receiver type or an embedded field: the type's
// own name, without pointer, parentheses, type arguments or package qualifier.
func typeBaseName(expression ast.Expr) string {
	switch typed := expression.(type) {
	case *ast.Ident:
		return typed.Name
	case *ast.StarExpr:
		return typeBaseName(typed.X)
	case *ast.ParenExpr:
		return typeBaseName(typed.X)
	case *ast.IndexExpr:
		return typeBaseName(typed.X)
	case *ast.IndexListExpr:
		return typeBaseName(typed.X)
	case *ast.SelectorExpr:
		return typed.Sel.Name
	default:
		return types.ExprString(expression)
	}
}

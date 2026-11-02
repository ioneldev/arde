package main

import (
	"fmt"
	"go/ast"
	"go/token"
	"go/types"
	"strconv"
)

type declaration struct {
	File string `json:"file"`
	Kind string `json:"kind"`
	Name string `json:"name"`
	Hash string `json:"hash"`
	Line int    `json:"line"`
}

type structField struct {
	File     string `json:"file"`
	Struct   string `json:"struct"`
	Name     string `json:"name"`
	Line     int    `json:"line"`
	Tag      string `json:"tag"`
	GoType   string `json:"go_type"`
	Embedded bool   `json:"embedded"`
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

type declarationCollector struct {
	path         string
	source       *parsedSource
	timesSeen    map[string]int
	declarations []declaration
}

func declarationsIn(path string, source *parsedSource) []declaration {
	collector := &declarationCollector{
		path:         path,
		source:       source,
		timesSeen:    map[string]int{},
		declarations: []declaration{},
	}
	for _, topLevel := range source.syntax.Decls {
		switch topLevel := topLevel.(type) {
		case *ast.FuncDecl:
			collector.addFunc(topLevel)
		case *ast.GenDecl:
			collector.addGenDecl(topLevel)
		}
	}
	return collector.declarations
}

func (collector *declarationCollector) addFunc(function *ast.FuncDecl) {
	collector.add(foundDeclaration{
		kind: "func",
		name: functionName(function),
		hash: fingerprint(function),
		pos:  function.Name.Pos(),
	})
}

func (collector *declarationCollector) addGenDecl(group *ast.GenDecl) {
	switch group.Tok {
	case token.TYPE:
		collector.addTypes(group)
	case token.VAR:
		collector.addVars(group)
	case token.CONST:
		collector.addConsts(group)
	}
}

func (collector *declarationCollector) addTypes(group *ast.GenDecl) {
	for _, spec := range group.Specs {
		typeSpec := spec.(*ast.TypeSpec)
		collector.add(foundDeclaration{
			kind: "type",
			name: typeSpec.Name.Name,
			hash: fingerprint(typeSpec),
			pos:  typeSpec.Name.Pos(),
		})
	}
}

func (collector *declarationCollector) addVars(group *ast.GenDecl) {
	for _, spec := range group.Specs {
		valueSpec := spec.(*ast.ValueSpec)
		hash := fingerprint(valueSpec)
		for _, name := range valueSpec.Names {
			collector.add(foundDeclaration{kind: "var", name: name.Name, hash: hash, pos: name.Pos()})
		}
	}
}

func (collector *declarationCollector) addConsts(group *ast.GenDecl) {
	var lastValueSource *ast.ValueSpec
	for index, spec := range group.Specs {
		valueSpec := spec.(*ast.ValueSpec)
		if valueSpec.Type != nil || len(valueSpec.Values) > 0 {
			lastValueSource = valueSpec
		}
		meaning := constMeaning{spec: valueSpec, valueSource: lastValueSource, iotaValue: index}
		hash := meaning.fingerprint()
		for _, name := range valueSpec.Names {
			collector.add(foundDeclaration{kind: "const", name: name.Name, hash: hash, pos: name.Pos()})
		}
	}
}

func (collector *declarationCollector) add(found foundDeclaration) {
	collector.declarations = append(collector.declarations, declaration{
		File: collector.path,
		Kind: found.kind,
		Name: collector.uniqueName(found.kind, found.name),
		Hash: found.hash,
		Line: collector.source.lineOf(found.pos),
	})
}

// uniqueName keeps ids unique within a file. Go allows several init functions and several
// blank identifiers in one file, and each must stay separately addressable.
func (collector *declarationCollector) uniqueName(kind, name string) string {
	key := kind + " " + name
	collector.timesSeen[key]++
	if collector.timesSeen[key] == 1 {
		return name
	}
	return fmt.Sprintf("%s#%d", name, collector.timesSeen[key])
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

func (meaning constMeaning) fingerprint() string {
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
	return fingerprint(parts...)
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
	reader := fieldReader{path: path, source: source}
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
				fields = append(fields, reader.fieldsOf(typeSpec.Name.Name, structType)...)
			}
		}
	}
	return fields
}

type fieldReader struct {
	path   string
	source *parsedSource
}

func (reader fieldReader) fieldsOf(structName string, structType *ast.StructType) []structField {
	fields := []structField{}
	for _, field := range structType.Fields.List {
		template := structField{
			File:   reader.path,
			Struct: structName,
			Tag:    tagText(field),
			GoType: types.ExprString(field.Type),
		}
		fields = append(fields, reader.namedAs(template, field)...)
	}
	return fields
}

// namedAs makes one record per declared name, or a single embedded record named after the type.
func (reader fieldReader) namedAs(template structField, field *ast.Field) []structField {
	if len(field.Names) == 0 {
		template.Name = typeBaseName(field.Type)
		template.Line = reader.source.lineOf(field.Type.Pos())
		template.Embedded = true
		return []structField{template}
	}
	records := []structField{}
	for _, name := range field.Names {
		record := template
		record.Name = name.Name
		record.Line = reader.source.lineOf(name.Pos())
		records = append(records, record)
	}
	return records
}

// tagText is the tag without the quotes of its Go literal. The scanner has already accepted the
// literal, so Unquote is not expected to fail; if it does, the literal is reported as written.
func tagText(field *ast.Field) string {
	if field.Tag == nil {
		return ""
	}
	text, err := strconv.Unquote(field.Tag.Value)
	if err != nil {
		return field.Tag.Value
	}
	return text
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

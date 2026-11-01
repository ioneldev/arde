import sys
root = sys.argv[1]
FACT = {
    "anchor": "func (h *handlers) product(",
    "summary": "Get one product priced for a currency and quantity",
    "params": [
        {"name": "id", "in": "path", "type": "int", "required": True, "description": "Product id", "minimum": 1},
        {"name": "currency", "in": "query", "type": "string", "required": False, "description": "Currency code, case-insensitive", "enum": ["RON", "EUR", "HUF"], "default": "RON"},
        {"name": "qty", "in": "query", "type": "int", "required": False, "description": "Units to price", "minimum": 1, "maximum": 10, "default": 1},
    ],
    "responses": [
        {"kind": "Success", "status": 200, "type": "ProductResponse"},
        {"kind": "Failure", "status": 400, "type": "ErrorResponse"},
        {"kind": "Failure", "status": 404, "type": "ErrorResponse"},
    ],
    "route": "/products/{id} [get]",
}
FIELD = {"anchor": "\tAvailable       bool", "comment": "\t// Available is true when stock covers the requested quantity."}

def attributes(p):
    out = []
    if "enum" in p: out.append("Enums(" + ", ".join(p["enum"]) + ")")
    if "minimum" in p: out.append(f"minimum({p['minimum']})")
    if "maximum" in p: out.append(f"maximum({p['maximum']})")
    if "default" in p: out.append(f"default({p['default']})")
    return " ".join(out)

def param_line(p):
    req = "true" if p["required"] else "false"
    return f'// @Param {p["name"]} {p["in"]} {p["type"]} {req} "{p["description"]}" {attributes(p)}'.rstrip()

def render(f):
    lines = [f'// @Summary {f["summary"]}', "// @Produce json"]
    lines += [param_line(p) for p in f["params"]]
    lines += [f'// @{r["kind"]} {r["status"]} {{object}} {r["type"]}' for r in f["responses"]]
    lines += [f'// @Router {f["route"]}']
    return lines

def upsert_above(path, anchor, block):
    raw = open(path, newline="").read()
    eol = "\r\n" if "\r\n" in raw else "\n"
    lines = raw.split(eol)
    i = next(n for n, l in enumerate(lines) if l.startswith(anchor))
    start = i
    while start > 0 and (lines[start - 1].lstrip().startswith("// @") or lines[start - 1] in block):
        start -= 1
    lines[start:i] = block
    open(path, "w", newline="").write(eol.join(lines))

for _ in range(int(sys.argv[2])):
    upsert_above(f"{root}/internal/httpapi/handlers.go", FACT["anchor"], render(FACT))
    upsert_above(f"{root}/internal/httpapi/responses.go", FIELD["anchor"], [FIELD["comment"]])
if sys.argv[2] == "1":
    print("\n".join(render(FACT)))

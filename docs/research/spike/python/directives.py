import json, os, re, shutil, signal, subprocess, sys, time, urllib.request, urllib.error
SP = sys.argv[1]
ENV = {**os.environ, 'GOCACHE': f'{SP}/gocache', 'GOPATH': f'{SP}/gopath', 'GOFLAGS': ''}
AST = f'{SP}/astcheck/astcheck'
OLD_RULE = re.compile(r'^\s*//(go:|line |\s*\+build)')
NEW_RULE = re.compile(r'^\s*(//[a-z0-9]+:[a-z0-9]|//(line|export)\s|//\s*\+build|/\*line\s|//\s*#cgo|//\s*(unordered\s+)?output:)', re.I)

def run(cmd, cwd, env=ENV):
    return subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True, shell=isinstance(cmd, str))

def fresh(name, src):
    d = f'{SP}/y_{name}'
    shutil.rmtree(d, ignore_errors=True)
    shutil.copytree(src, d) if isinstance(src, str) else os.makedirs(d)
    return d

def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    open(path, 'w').write(text)

def edit(path, old, new):
    s = open(path, newline='').read()
    assert old in s, old
    open(path, 'w', newline='').write(s.replace(old, new, 1))

def same_ast(a, b):
    return 'identical: true' in run([AST, a, b], SP).stdout

def go_lines(root):
    out = []
    for d, sub, names in os.walk(root):
        sub[:] = [s for s in sub if s != '.git']
        for n in names:
            if n.endswith('.go'):
                out += [(n, l) for l in open(os.path.join(d, n), newline='').read().replace('\r', '').splitlines()]
    return out

def caught(a, b, rule):
    pick = lambda r: sorted(x for x in go_lines(r) if rule.match(x[1]))
    return pick(a) != pick(b)

def imports_c(root):
    return any(l.strip() == 'import "C"' for _, l in go_lines(root))

def list_view(root):
    out = run(['go', 'list', '-json', './...'], root).stdout
    keys = ('ImportPath', 'GoFiles', 'IgnoredGoFiles', 'TestGoFiles', 'EmbedFiles', 'EmbedPatterns', 'CgoFiles', 'DefaultGODEBUG', 'CgoCFLAGS', 'CgoLDFLAGS')
    dec, i, pkgs = json.JSONDecoder(), 0, []
    while i < len(out):
        while i < len(out) and out[i].isspace(): i += 1
        if i >= len(out): break
        o, i = dec.raw_decode(out, i)
        pkgs.append({k: o.get(k) for k in keys})
    return pkgs

rows = []
def row(label, a, b, effect):
    rows.append((label, same_ast(a, b), caught(a, b, OLD_RULE), caught(a, b, NEW_RULE) or imports_c(b), list_view(a) != list_view(b), effect))

# 1. //go:debug in package main
BASE = f'{SP}/ann'
a = fresh('debug_a', BASE); b = fresh('debug_b', BASE)
edit(f'{b}/cmd/server/main.go', 'package main', '//go:debug httpmuxgo121=1\n\npackage main')
build = run(['go', 'build', '-o', f'{SP}/y_debug_bin', './cmd/server'], b)
effect = 'build failed: ' + build.stderr.strip()[:90]
if build.returncode == 0:
    p = subprocess.Popen([f'{SP}/y_debug_bin'], env={**ENV, 'PORT': '18095'}, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    def get(u):
        try:
            return urllib.request.urlopen(f'http://localhost:18095{u}').status
        except urllib.error.HTTPError as e:
            return e.code
        except Exception:
            return None
    for _ in range(300):
        if get('/x') is not None: break
        time.sleep(0.01)
    effect = f"go test exit {run(['go','test','./...'], b).returncode}; live: / {get('/')}, /healthz {get('/healthz')}, /products/1 {get('/products/1')}"
    p.send_signal(signal.SIGTERM); p.wait()
row('//go:debug httpmuxgo121=1', a, b, effect)

# 2. //go:embed pattern
a = fresh('embed_a', None)
write(f'{a}/go.mod', 'module e\n\ngo 1.22\n')
write(f'{a}/main.go', 'package main\n\nimport (\n\t_ "embed"\n\t"fmt"\n)\n\n//go:embed a.txt\nvar data string\n\nfunc main() { fmt.Print(data) }\n')
write(f'{a}/a.txt', 'rates-v1\n'); write(f'{a}/b.txt', 'rates-v2\n')
b = fresh('embed_b', a)
edit(f'{b}/main.go', '//go:embed a.txt', '//go:embed b.txt')
row('//go:embed a.txt -> b.txt', a, b, f"output {run('go run .', a).stdout.strip()} -> {run('go run .', b).stdout.strip()}")

# 3. // Output: in an Example test
a = fresh('output_a', f'{SP}/lf')
write(f'{a}/internal/catalog/example_test.go', 'package catalog\n\nimport "fmt"\n\nfunc ExampleConvert() {\n\tfmt.Println(Convert(8990, RON))\n\t// Output: 89.9\n}\n')
b = fresh('output_b', a)
edit(f'{b}/internal/catalog/example_test.go', '// Output: 89.9', '// Output: 12345')
c = fresh('output_c', a)
edit(f'{c}/internal/catalog/example_test.go', '\t// Output: 89.9\n', '')
row('// Output: value changed', a, b, f"go test exit {run(['go','test','./...'], a).returncode} -> {run(['go','test','./...'], b).returncode}")
ran = lambda d: 'ExampleConvert' in run(['go', 'test', '-v', './internal/catalog/'], d).stdout
row('// Output: deleted', a, c, f"example ran: {ran(a)} -> {ran(c)}; go test exit {run(['go','test','./...'], c).returncode}")

# 4. cgo preamble
a = fresh('cgo_a', None)
write(f'{a}/go.mod', 'module c\n\ngo 1.22\n')
write(f'{a}/main.go', 'package main\n\n// #define LIMIT 10\nimport "C"\n\nimport "fmt"\n\nfunc main() { fmt.Println(int(C.LIMIT)) }\n')
b = fresh('cgo_b', a)
edit(f'{b}/main.go', '#define LIMIT 10', '#define LIMIT 20')
env = {**ENV, 'CGO_ENABLED': '1'}
ra, rb = run('go run .', a, env), run('go run .', b, env)
row('cgo comment: #define LIMIT', a, b, f"output {ra.stdout.strip() or ra.stderr.strip()[:50]} -> {rb.stdout.strip() or rb.stderr.strip()[:50]}")

# 5. harmless controls
a = fresh('nolint_a', BASE); b = fresh('nolint_b', BASE)
edit(f'{b}/internal/httpapi/handlers.go', 'func money(', '//nolint:unused\nfunc money(')
row('//nolint:unused (control)', a, b, 'no effect on build or tests')

print(f"{'comment change':<30}{'AST same':<10}{'old rule':<10}{'new rule':<10}{'go list':<9}effect")
for label, s, o, n, g, e in rows:
    print(f"{label:<30}{str(s):<10}{str(o):<10}{str(n):<10}{str(g):<9}{e}")

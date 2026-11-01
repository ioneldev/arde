import os, re, shutil, subprocess, sys
SP = sys.argv[1]
ENV = {**os.environ, 'GOCACHE': f'{SP}/gocache', 'GOPATH': f'{SP}/gopath', 'GOFLAGS': ''}
BASE = f'{SP}/ann'
DIRECTIVE = re.compile(r'^\s*//(go:|line |\s*\+build)')

def files(root):
    out = {}
    for d, sub, names in os.walk(root):
        sub[:] = [s for s in sub if s != '.git']
        for n in names:
            p = os.path.join(d, n)
            out[os.path.relpath(p, root)] = open(p, 'rb').read().replace(b'\r\n', b'\n')
    return out

def is_docs(f): return f.endswith('.md') or f.startswith('docs/')

def directives(raw):
    return [l for l in raw.decode().splitlines() if DIRECTIVE.match(l)]

def ast_changed(old, new):
    out = subprocess.run([f'{SP}/astcheck/astcheck', old, new], capture_output=True, text=True).stdout
    return set(re.findall(r'(?:DIFF|NEW ?): *(\S+)', out))

def decide(old, new, check_directives=True):
    a, b = files(old), files(new)
    changed = [f for f in set(a) | set(b) if a.get(f) != b.get(f)]
    if not changed:
        return 'skip: no changes'
    if all(is_docs(f) for f in changed):
        return 'skip: docs only'
    real_go = ast_changed(old, new)
    for f in changed:
        if is_docs(f):
            continue
        if not f.endswith('.go'):
            return 'RUN: non-Go file changed'
        if f in real_go or f not in a or f not in b:
            return 'RUN: Go code changed'
        if check_directives and directives(a[f]) != directives(b[f]):
            return 'RUN: Go directive changed'
    return 'skip: comments only'

def variant(name, fn):
    d = f'{SP}/x_{name}'
    shutil.rmtree(d, ignore_errors=True)
    shutil.copytree(BASE, d)
    if fn: fn(d)
    return d

def edit(path, old, new):
    s = open(path, newline='').read()
    assert old in s, old
    open(path, 'w', newline='').write(s.replace(old, new, 1))

CASES = [
 ('nothing changed', None),
 ('blank lines added in Go', lambda d: edit(f'{d}/internal/catalog/store.go', 'return slices.Clone(s.products)', '\n\treturn slices.Clone(s.products)')),
 ('swag comment edited', lambda d: edit(f'{d}/internal/httpapi/handlers.go', '// @Summary Get product', '// @Summary Fetch product')),
 ('README edited', lambda d: open(f'{d}/README.md', 'a').write('\nA note.\n')),
 ('Makefile comment added', lambda d: open(f'{d}/Makefile', 'a').write('\n# a note\n')),
 ('blank import of pprof added', lambda d: edit(f'{d}/cmd/server/main.go', '"net/http"', '"net/http"\n\t_ "net/http/pprof"')),
 ('//go:build ignore on a test file', lambda d: edit(f'{d}/internal/catalog/pricing_test.go', 'package catalog', '//go:build ignore\n\npackage catalog')),
]
print(f"{'case':<36}{'without directive check':<28}with directive check")
for label, fn in CASES:
    d = variant(re.sub(r'\W+', '_', label), fn)
    print(f"{label:<36}{decide(BASE, d, False):<28}{decide(BASE, d)}")
print(f"{'line endings only (CRLF vs LF)':<36}{decide(SP + '/baseline', SP + '/lf', False):<28}{decide(SP + '/baseline', SP + '/lf')}")

d = f'{SP}/x__go_build_ignore_on_a_test_file'
count = lambda p: subprocess.run("go test -list '.*' ./... 2>/dev/null | grep -c '^Test'", shell=True, cwd=p, env=ENV, capture_output=True, text=True).stdout.strip()
rc = subprocess.run(['go', 'test', './...'], cwd=d, env=ENV, capture_output=True).returncode
print(f"\n//go:build trap: go test exit {rc}, tests {count(BASE)} -> {count(d)}, AST identical: {'identical: true' in subprocess.run([SP + '/astcheck/astcheck', BASE, d], capture_output=True, text=True).stdout}")

tally = {}
for name in sorted(n[2:] for n in os.listdir(SP) if n.startswith('c_')):
    r = decide(BASE, f'{SP}/c_{name}').split(':')[0]
    tally[r] = tally.get(r, 0) + 1
    if r == 'skip':
        print('22-set skip:', name)
print('22-set decisions:', tally)

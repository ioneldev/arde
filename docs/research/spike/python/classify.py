import json, os, re, shutil, signal, subprocess, sys, time, urllib.request, urllib.error
S = sys.argv[1]
SP = f'{S}/spike'
ENV = {**os.environ, 'GOCACHE': f'{SP}/gocache', 'GOPATH': f'{SP}/gopath'}
BASE = f'{SP}/ann'
URLS = ['/', '/healthz', '/products/1', '/products/2', '/products/3', '/products/2?qty=3', '/products/2?qty=4',
        '/products/1?currency=EUR&qty=10', '/products/1?qty=99', '/products/99', '/products/abc',
        '/products/1?currency=USD', '/does-not-exist']
PIPELINE = {'Makefile', 'Dockerfile', '.dockerignore', 'go.mod', 'go.sum'}

def mutate(name, fn):
    d = f'{SP}/c_{name}'
    shutil.rmtree(d, ignore_errors=True)
    shutil.copytree(BASE, d)
    fn(d)
    return d

def sub(path, old, new, regex=False):
    s = open(path, newline='').read()
    n = re.sub(old, new, s) if regex else s.replace(old, new, 1)
    assert n != s, path
    open(path, 'w', newline='').write(n)

def inventory(d):
    out = subprocess.run([f'{SP}/patterns/patterns', d, f'{SP}/patterns/stdlib.json'], capture_output=True, text=True).stdout
    items = {' '.join(l.split()) for l in out.splitlines() if l.strip() and 'counts:' not in l}
    for root, _, files in os.walk(d):
        for f in files:
            if f.endswith('.go') and not f.endswith('_test.go'):
                src = open(os.path.join(root, f), newline='').read()
                for name, body in re.findall(r'type (\w+) struct \{(.*?)\n\}', src, re.S):
                    items |= {f'field {name}.{t}' for t in re.findall(r'json:"([a-z_]+)', body)}
    return items

def changed_files(d):
    out = subprocess.run(['diff', '-rq', '-x', '.git', '-x', 'docs', BASE, d], capture_output=True, text=True).stdout
    names = []
    for l in out.splitlines():
        m = re.match(r'Files \S+ and (\S+) differ', l)
        if m:
            names.append(os.path.relpath(m.group(1), d))
        m = re.match(r'Only in (\S+): (\S+)', l)
        if m:
            names.append(os.path.relpath(os.path.join(m.group(1), m.group(2)), d))
    return names

def fetch(port, url):
    try:
        with urllib.request.urlopen(f'http://localhost:{port}{url}') as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()

def start(d, name, port):
    b = f'{SP}/cbin_{name}'
    subprocess.run(['go', 'build', '-o', b, './cmd/server'], cwd=d, env=ENV, check=True)
    p = subprocess.Popen([b], env={**ENV, 'PORT': str(port)}, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(300):
        try:
            fetch(port, '/'); break
        except Exception:
            time.sleep(0.01)
    return p

def classify(files, code_same, removed, added, responses_differ):
    labels = []
    if any(f in PIPELINE for f in files): labels.append('pipeline')
    go = [f for f in files if f.endswith('.go')]
    if go and all(f.endswith('_test.go') for f in go): labels.append('testing')
    if removed: labels.append('contract-breaking')
    if added: labels.append('contract-additive')
    if responses_differ and not removed and not added: labels.append('behaviour')
    prod = [f for f in go if not f.endswith('_test.go')]
    if prod and not code_same and not labels: labels.append('internal')
    return labels or ['comments-only']

CASES = [
 ('comments', 'comment reworded', 'comments-only', lambda d: sub(f'{d}/internal/httpapi/handlers.go', '// product returns one product.', '// product returns a single product.')),
 ('route', 'route removed', 'contract-breaking', lambda d: sub(f'{d}/internal/httpapi/router.go', r'\s*mux\.HandleFunc\("GET /healthz", h\.healthz\)', '', True)),
 ('qty', 'qty param removed', 'contract-breaking', lambda d: sub(f'{d}/internal/httpapi/handlers.go', r'(?s)qty := defaultQty\s+if raw := query\.Get\("qty"\).*?\n\t}\n', 'qty := defaultQty\n', True)),
 ('lang', 'new param read', 'contract-additive', lambda d: sub(f'{d}/internal/httpapi/handlers.go', 'query := r.URL.Query()', 'query := r.URL.Query()\n\t_ = query.Get("lang")')),
 ('field', 'new response field', 'contract-additive', lambda d: sub(f'{d}/internal/httpapi/responses.go', 'Rating          float64 `json:"rating"`', 'Rating          float64 `json:"rating"`\n\tBrand           string  `json:"brand"`')),
 ('discount', 'discount dropped', 'behaviour', lambda d: sub(f'{d}/internal/httpapi/handlers.go', 'unit := p.UnitPrice()', 'unit := p.PriceMinor')),
 ('round', 'rounding changed in Convert', 'behaviour', lambda d: sub(f'{d}/internal/catalog/pricing.go', 'math.Round(', 'math.Floor(')),
 ('avail', 'available uses >', 'behaviour', lambda d: sub(f'{d}/internal/httpapi/handlers.go', 'p.Stock >= qty', 'p.Stock > qty')),
 ('rename', 'local variable renamed', 'internal', lambda d: sub(f'{d}/internal/httpapi/handlers.go', r'\bunit\b', 'unitPrice', True)),
 ('log', 'access log records query', 'security-compliance', lambda d: sub(f'{d}/internal/httpapi/middleware.go', '"path", r.URL.Path,', '"path", r.URL.Path,\n\t\t\t"query", r.URL.RawQuery,')),
 ('make', 'Makefile target added', 'pipeline', lambda d: open(f'{d}/Makefile', 'a', newline='').write('\nlint:\n\tgo vet ./...\n')),
 ('test', 'test added', 'testing', lambda d: open(f'{d}/internal/catalog/extra_test.go', 'w').write('package catalog\n\nimport "testing"\n\nfunc TestExtra(t *testing.T) {}\n')),
 ('max5', 'qty limit lowered to 5', 'contract-breaking', lambda d: sub(f'{d}/internal/httpapi/handlers.go', 'maxQty     = 10', 'maxQty     = 5')),
 ('default2', 'default qty changed to 2', 'contract-breaking', lambda d: sub(f'{d}/internal/httpapi/handlers.go', 'defaultQty = 1', 'defaultQty = 2')),
 ('timeout', 'read timeout lowered to 1s', 'operational', lambda d: sub(f'{d}/cmd/server/main.go', 'ReadTimeout:       10 * time.Second', 'ReadTimeout:       1 * time.Second')),
 ('recover', 'panic recovery removed', 'operational|contract-breaking', lambda d: sub(f'{d}/internal/httpapi/router.go', 'logRequests(logger, recoverPanics(logger, mux))', 'logRequests(logger, mux)')),
 ('errcode', 'error code renamed', 'contract-breaking', lambda d: sub(f'{d}/internal/httpapi/handlers.go', '"invalid_qty"', '"bad_qty"')),
 ('rate', 'EUR rate changed', 'data', lambda d: sub(f'{d}/internal/catalog/pricing.go', 'EUR: 0.2012,', 'EUR: 0.2100,')),
 ('seed', 'fourth product added', 'data', lambda d: sub(f'{d}/internal/catalog/store.go', 'return []Product{', 'return []Product{\n\t\t{ID: 4, Slug: "gopher-mug", Name: "Gopher Mug", Description: "Ceramic mug.", Category: CategoryFashion, PriceMinor: 3500, Stock: 7, Rating: 4.3, Specs: ApparelSpecs{Color: "white", Material: "ceramic"}},')),
 ('extract', 'helper extracted, same logic', 'internal', lambda d: (sub(f'{d}/internal/httpapi/handlers.go', 'Available:   p.Stock >= qty,', 'Available:   isAvailable(p, qty),'), open(f'{d}/internal/httpapi/handlers.go', 'a', newline='').write('\nfunc isAvailable(p catalog.Product, qty int) bool {\n\treturn p.Stock >= qty\n}\n'))),
 ('docker', 'Dockerfile base image bumped', 'pipeline', lambda d: sub(f'{d}/Dockerfile', 'golang:1.26-alpine', 'golang:1.27-alpine')),
 ('max20', 'qty limit raised to 20', 'contract-additive', lambda d: sub(f'{d}/internal/httpapi/handlers.go', 'maxQty     = 10', 'maxQty     = 20')),
]

HARD = {'max5','default2','timeout','recover','errcode','rate','seed','extract','docker','max20'}

import difflib, random
def udiff(d, files):
    out = []
    for rel in files:
        a = os.path.join(BASE, rel); b = os.path.join(d, rel)
        al = open(a, newline='').read().replace('\r', '').splitlines(True) if os.path.exists(a) else []
        bl = open(b, newline='').read().replace('\r', '').splitlines(True) if os.path.exists(b) else []
        out += difflib.unified_diff(al, bl, f'a/{rel}', f'b/{rel}', n=3)
    return ''.join(out)

base_srv = start(BASE, 'base', 18090)
base_inv = inventory(BASE)
rows = []
for name, label, expected, fn in CASES:
    d = mutate(name, fn)
    ast = subprocess.run([f'{SP}/astcheck/astcheck', BASE, d], capture_output=True, text=True).stdout
    same = 'identical: true' in ast
    inv = inventory(d)
    removed, added = base_inv - inv, inv - base_inv
    srv = start(d, name, 18091)
    differ = sum(fetch(18090, u) != fetch(18091, u) for u in URLS)
    srv.send_signal(signal.SIGTERM); srv.wait()
    files = changed_files(d)
    rows.append(dict(name=name, label=label, expected=expected.split('|'), code=classify(files, same, removed, added, differ > 0), diff=udiff(d, files), hard=name in HARD))
base_srv.send_signal(signal.SIGTERM); base_srv.wait()
random.Random(42).shuffle(rows)
with open(f'{SP}/classifier_input.txt', 'w') as f:
    for i, r in enumerate(rows, 1):
        r['id'] = f'change-{i:02d}'
        f.write(f"=== {r['id']} ===\n{r['diff']}\n")
json.dump([{k: r[k] for k in ('id', 'label', 'expected', 'code', 'hard')} for r in rows], open(f'{SP}/classifier_key.json', 'w'), indent=1)
ok = [r for r in rows if set(r['expected']) & set(r['code'])]
print('cases:', len(rows), '| code correct:', len(ok), '| of the hard 10:', sum(1 for r in ok if r['hard']))
for r in sorted(rows, key=lambda r: (not r['hard'], r['label'])):
    if r['hard']:
        print(f"  {r['label']:<32}{'/'.join(r['expected']):<36}{'+'.join(r['code'])}")
sizes = [len(r['diff']) for r in rows]
print('diff chars per change: median', sorted(sizes)[len(sizes)//2], 'max', max(sizes))
print('input file chars:', os.path.getsize(f'{SP}/classifier_input.txt'))

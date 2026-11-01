import json, sys, urllib.request, urllib.error
from jsonschema import Draft202012Validator
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012
from openapi_spec_validator import validate as validate_spec

spec = json.load(open(sys.argv[1]))
try:
    validate_spec(spec); print('spec is valid OpenAPI', spec['openapi'])
except Exception as e:
    print('SPEC INVALID:', type(e).__name__, str(e)[:300])

root = Resource(contents=spec, specification=DRAFT202012)
reg = Registry().with_resource('urn:spec', root)

def schema_for(path, status):
    r = spec['paths'][path]['get']['responses'][str(status)]
    return {'$ref': 'urn:spec' + '#/components/schemas/' + r['content']['application/json']['schema']['$ref'].split('/')[-1]}

def fetch(url):
    try:
        with urllib.request.urlopen('http://localhost:18080' + url) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        return e.code, json.load(e)

def check(label, schema, body):
    errs = list(Draft202012Validator(schema, registry=reg).iter_errors(body))
    print(('PASS ' if not errs else 'FAIL ') + label, '' if not errs else '-> ' + errs[0].message[:140])

for url, tpl in [('/', '/'), ('/healthz', '/healthz'), ('/products/1', '/products/{id}'),
                 ('/products/2?currency=EUR&qty=2', '/products/{id}'), ('/products/3', '/products/{id}'),
                 ('/products/abc', '/products/{id}'), ('/products/99', '/products/{id}')]:
    st, body = fetch(url)
    check(f'{st} {url}', schema_for(tpl, st), body)

print('--- negative controls: these must FAIL or the check proves nothing ---')
st, good = fetch('/products/1')
s = schema_for('/products/{id}', 200)
bad1 = dict(good); del bad1['total']
bad2 = json.loads(json.dumps(good)); bad2['specs'] = {'nonsense': True}
bad3 = json.loads(json.dumps(good)); del bad3['specs']['cpu']
bad4 = dict(good); bad4['invented_field'] = 1
for lbl, b in [('missing total', bad1), ('specs matches no variant', bad2), ('specs missing a key', bad3), ('undocumented extra field', bad4)]:
    check(lbl, s, b)

import sys, urllib.request, urllib.error
URLS = ['/', '/healthz', '/products/1', '/products/2', '/products/3', '/products/2?qty=3', '/products/2?qty=4',
        '/products/1?currency=EUR&qty=10', '/products/1?qty=99', '/products/99', '/products/abc',
        '/products/1?currency=USD', '/does-not-exist']
def fetch(port, url):
    try:
        with urllib.request.urlopen(f'http://localhost:{port}{url}') as r:
            return r.status, r.headers.get('Content-Type'), r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.headers.get('Content-Type'), e.read()
diffs = [u for u in URLS if fetch(sys.argv[1], u) != fetch(sys.argv[2], u)]
print(f'differs on {len(diffs)}/{len(URLS)}' + (': ' + ', '.join(diffs[:3]) if diffs else ''))

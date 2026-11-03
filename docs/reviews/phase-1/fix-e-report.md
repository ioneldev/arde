**Status: Group E is done.** It is 11 commits on `phase-1`, on top of `e775f77`. Nothing is pushed. All three gates passed before each commit, and the final five-run loop passed. `.env` and `catalog-poc` were not touched; I used a `cp -R` copy with its `.git` removed. Every log is in `/private/tmp/claude-501/-Users-ionel-work-personal-emag/0f37fc47-1c0a-42ed-bd77-f0a6f96a2ead/scratchpad/fixE`.

## Commits
| Commit | Item |
|---|---|
| `e959497` | E1: the links gate walks `.git` |
| `c6f95de` | E2: a wrapper's site is read in one of three forms, or the run stops |
| `96fd1c7` | E3: roots and anchor |
| `711ec00` | N2: `GOPACKAGESDRIVER=off` |
| `6b606df` | N5: separators and direction controls are escaped |
| `99d34e0` | M1: a failing test keeps its text, tracked per run |
| `417788e` | Docs: M2–M5 and the glossary's `dropped` entry |
| `8b8d790` | Closure 1: the service scope tests use `local_service_runner()` |
| `22a971e` | Closure 2: `TestEachConstNameKeepsItsOwnLine` |
| `8437de5` | A regression I introduced in E3, found and fixed (below) |
| `722902e` | Refactor: E3's `refersTo` took three positional arguments, and a test helper did too |

## Test counts
| After | `make check` | integration | Go top-level |
|---|---|---|---|
| base | 1930 | 241 | 209 |
| E1 | 1939 (+9: 2 `.git` listing tests, 7 adapter methods; the flipped test replaces the old one) | 242 (+1: the p11 shape) | 209 |
| E2 | 1948 (+9: profile +3, builder +5 (4 problems, method hint, safe file name, minus the old prefix test), client +1) | 250 (+8: `test_route_wrappers.py`) | 214 (+8 new, −3 replaced) |
| E3 | 1949 (+1) | 261 (+11: `test_handler_identity.py` 8, adapter 3) | 216 (+2) |
| N2 | 1950 (+1) | 262 (+1: stub driver) | 216 |
| N5 | 1961 (+11 code points) | 262 | 216 |
| M1 | 1962 (+1) | 263 (+1) | 216 |
| docs, closure 1 | 1962 | 263 | 216 |
| closure 2 to HEAD | 1962 | 263 | 217 (+1) |

The sums check out: 1930+9+9+1+1+11+1 = 1962, 241+1+8+11+1+1 = 263, and 209+5+2+1 = 217. Collected is 2233 = 1962 + 271 deselected = 263 + 1970.

**Final loop:** `make check` ran 5 times, each 1962 passed with exit 0. `make integration` looped alongside 3 times, each 263 passed with exit 0. Logs are in `fixE/final/`.

## Each RED, on the code before the fix
- **E1:**
  - 9 unit tests failed: "DID NOT RAISE", or the listing came back `[]`.
  - The p11 shape (fake 25-line key at `.git/dep/go.mod`, `go.work use ./.git/dep`) leaked **25/25** key lines through `extract_inventory`, `run_checks` and `built_service`.
- **E2:**
  - 10 of the 12 new or changed Go tests failed.
  - Python: w1 with a `method` key was refused by the profile, and w1 without it gave no stop.
  - w2 (both forms) and w7 gave no stop. w4 gave `ANY /items`; t1 gave `POST /items`.
  - The builder and client test modules failed on import, because the new types are missing on base.
- **E3:**
  - Go: anchor `renderError`, no roots, `GET /debug/routes` missing.
  - Python: 7 of the 8 identity tests failed.
  - The w3 hash stayed `632fb693…` across the factory edit, and both w3c routes had id `renderError`.
- **N2:** the stub driver ran (`assert not True`), and 3 environment tests failed.
- **N5:** 11 failures.
- **M1:** the text was `''`; `run_checks` output held only `FAIL`.
- **Closure 2:** the mutant `pos: valueSpec.Pos()` gave `got map[first:4 second:4]`.
- **Fix `8437de5`:** `got [/stock/ /stock/items]`.
- The docs commit and closure 1 change no behaviour, so they have no RED.

**Tests that pinned old behaviour and changed:**
- `test_a_link_inside_the_git_folder_is_not_looked_at`, flipped. The `.git` comments in `base.py` and `test_models.py` changed with it.
- Go wrapper tests:
  - `TestRoutesListsAWrapperThatAddsToThePathItIsGiven` became `...StopsAtAWrapperThatBuildsItsPath...`.
  - `TestRoutesDoesNotCallAMethodOrAPlainPathAPrefix` became `...ReadsThePathAWrapperRegistersAtEachCall`, with the `"GET "+path` case flipped to GET.
  - `TestRoutesDoesNotCallAPathOutsideAWrapperAPrefix` was renamed.
  - Two tests now expect `POST /orders` in the literal.
- The `method` refusal case in `test_models.py`.
- `wrapper_prefixes` in the builder and client tests.
- E3:
  - Toy `return next` middleware in `routes_test.go` became real middleware.
  - `adapted(d.failing)` now anchors at `adapted`.
  - `either(parcels, depots)` now anchors at `either`, with 3 roots.
  - `TestFingerprintsAgreeWithTheIdsRoutesPrints` now reads `roots`.
  - `test_inventory_on_shop` and the `a_route` helper carry `roots`.
- N2: two tests that pin the exact environment.

## Base comparison
I compared `5298e7b`-equivalent `e775f77` against HEAD `722902e`. The repos were `shop`, the `catalog-poc` copy (with `stdlib.json`), `parcels` tracker and console, and `library`. The comparison covered:
- the inventory
- route hashes and reasons
- declaration hashes
- `changed_declarations` and the routes moved after a one-token edit
- `run_checks`

**Result: 12 differences, all `roots` added on a route.** Each equals `[handler_id]`, or `[]` for the inline `POST /purge`. Every hash, reason, declaration hash, moved route and check result is identical.

The control injected 2 changes and the comparison found exactly 2 (14 against base). One finding: `go test` prints packages' `[no test files]` lines in a varying order. The same tree differed from itself in 1 of 8 runs, so the comparison sorts the output's lines; booleans and counts are compared exactly.

## What the brief or the reports left open or got wrong
- **Nested wrappers.** Under the literal E2 rules, a wrapper that hands its path parameter to another wrapper is trusted, and the inner wrapper's `"GET "` is lost silently. I added a fourth stop for it, `hands on its path`.
- **A constant-path site inside a wrapper** is still read as its own site. Read literally, "trusted only when…" would stop it.
- **"Registers nothing"** stops both a mount at the wrapper call and a wrapper with no site that registers its path.
- **`method` key at another module's call.** The brief scopes it to form 3, but the profile's own rule says an ignored key must not exist. So at a call to another module's function, such as a chi-style `r.Method(m, p, h)`, it prefixes the path, and it must be a constant there too.
- **Form 1 wrappers** (path parameter alone) still take the method from the wrapper's name, as R114 implies, although `net/http` does not enforce it. This is documented.
- **E3 rule gaps:**
  - A function written in place anywhere in the handler, even as a factory's argument, now means no anchor and no hash. The hash could not cover it, and base under-reported here.
  - A call the compiler cannot name, such as `wrap(h)`, no longer peels. Base anchored those at the handler.
- **E3 consequence of R115.** Middleware that calls `next(w, r)` directly, or keeps `next` in a struct, is a factory under the rule. The route's docs then go on that middleware. This is documented.
- **Regression I introduced in E3 and fixed in `8437de5`.** A mux behind middleware that also takes a function written in place stopped being a mount. It now is a mount again.
- **Closure note 4:** the `group` repo, with the prefix in a field, now stops at `router/router.go:16` with "builds its path". The old limitations line saying such paths "read short" was wrong after E2, and I rewrote it.
- **Security N1:** the report measured 19/25 lines from two methods with its PEM. My one-token-per-line fake key gave 25/25 from all three.
- **Re-measured:**
  - M2: both repos give goinspect exit 2 and `go build` exit 1.
  - M3: `parcels` with no route pattern has no route line in `dropped`.
  - M4: `--main ./internal/api` exits 0 and drops cmd's routes.
  - M5: k2 gives tests_run 5 and tests_listed 6, and 5 against 5 once its benchmark is removed.

**Plan lines now stale (not edited):**
- line 469: the routes reply has `unread_sites` instead of `wrapper_prefixes`, and sites carry `roots`.
- line 214: `Route` fields.
- line 110: the list of shapes with `handler_id` None should add "a function written in place anywhere in the handler" and "a call the compiler cannot name".
- line 123: the test name says "callee", and the anchor is now the outermost factory.

## Not verified
- I didn't run the bug hunter's services with curl, re-run the security probes p1–p10, run mutation totals, or test on Linux.
- I tested nested wrappers only for the stop, not for any composition.
- I didn't measure the effect of the `method`-key prefix on any real third-party router.
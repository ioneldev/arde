## Status: Group G is done, with 12 commits on `phase-1`, base `9a82826`

Nothing is pushed. All three gates passed on the exact tree before every commit. The 5-run load test and the base comparison passed on the final commit `4cb7a4c`. I didn't read or touch `.env`. I used catalog-poc only as a `cp -R` copy with `.git` removed, and spawned no subagents. Logs are in `/private/tmp/claude-501/-Users-ionel-work-personal-emag/0f37fc47-1c0a-42ed-bd77-f0a6f96a2ead/scratchpad/fixG`: `<item>-{check,integration,gotest,tree}.log/txt`, `*-red-*.log`, `*-green-*.log`, `compare/`, `load/`.

**One thing in the tree isn't mine.** `docs/superpowers/plans/2026-11-01-arde.md` is modified in the working tree; it was edited while my base gates ran, presumably by you. I never staged or edited it, and every gate ran with it present.

| Commit | Item |
|---|---|
| `6b26ba5` | G1: a wrapper's site that holds none of the call's handler reads its own (x1) |
| `6881ff9` | G2: curried middleware is peeled; a handler called through a middleware variable has roots (c1, c2) |
| `af2fac4` | G3: the guard keeps a handler that is its anchor alone; a method value's receiver counts by type; limitations:78 |
| `812dcfe` | G4: the interface scan looks only at methods named like a registrar |
| `063e7ed` | G5: only methods an interface can call join, both as roots and in the walk (o1, o2) |
| `641f091` | G6: an embedded interface field gives no anchor (e1) |
| `0dbbcb7` | G6: the stop for a call through an interface names the method, the interface and the types behind it (q3a) |
| `e15c5b2` | G6: limitations:79, local or package middleware variable (l1) |
| `bce6226` | G7 R124: two character sets; a refusal names the code point and its offset |
| `8d8e5e4` | G7 R125: the port says whoever prints these values escapes them |
| `afbe611` | G7 R126: the links gate's three messages |
| `4cb7a4c` | Follow-up to G1 (explained under "what the brief got wrong") |

I fixed the `routeSite` comment from G6 inside `6881ff9`, because G2 made it wrong a second time.

## Test counts (all passed)

| After | make check | integration | Go top-level |
|---|---|---|---|
| base | 2130 | 289 | 233 |
| G1 | 2130 | 291 (+2) | 234 (+1) |
| G2 | 2130 | 294 (+3) | 235 (+1) |
| G3 | 2132 (+2) | 296 (+2) | 238 (+3) |
| G4 | 2132 | 296 | 239 (+1) |
| G5 | 2132 | 298 (+2) | 241 (+2) |
| G6 e1 | 2132 | 298 | 242 (+1) |
| G6 q3a | 2134 (+2) | 298 | 244 (+2) |
| G6 l1 | 2134 | 298 | 244 |
| R124 | 2193 (+59) | 298 | 244 |
| R125 | 2193 | 298 | 244 |
| R126 | 2196 (+3) | 298 | 244 |
| follow-up | 2196 | 298 | 245 (+1) |

- **Sums:**
  - make check: 2130+2+2+59+3 = 2196.
  - integration: 289+2+3+2+2 = 298.
  - Go: 233+1+1+3+1+2+1+2+1 = 245.
- **Collected:** 2196 + 306 deselected = 2502 = 298 + 2204.
- **R124's +59:**
  - Removed: the two "hides from a reader" tests, 54 + 81 = 135.
  - Added (194):
    - 24 free-text fields refusing the 4 direction controls, and 36 names refusing them.
    - 48 free-text fields and 16 plain names keeping the 8 other format characters.
    - 56 identifiers refusing those 8.
    - 4 offset tests, 6 inventory tests, 3 more `one_line` cases and 1 `test_base` test.
  - Net: 194 − 135 = +59.
- **Load test** (on `4cb7a4c`):
  - `make check` ran 5 times: 2196 passed and exit 0 each time.
  - `make integration` looped 3 times alongside: 298 passed and exit 0 each time.
  - The tree was unchanged. A first load run was stopped before the follow-up; its logs are in `load/stopped-early`.

## Each RED, on the code before its fix

- **G1:**
  - Go: `OPTIONS /items` anchored at `items` with `items` among its roots.
  - Python: both OPTIONS routes anchored at items or orders, and an edit to `items` moved `OPTIONS /items` too.
- **G2:**
  - Go: `GET /items` anchored at `requireRole`, and `admin(...)` had no roots.
  - Python: c1/c2 routes were None after the guard; the middleware edit moved `/items` and `/orders` but not the variable route; the initialiser edit moved nothing.
- **G3:**
  - Go: `s.items` had different identities from `publicRoutes` and `v2Routes`, and the "anchor alone" flag was false everywhere.
  - Unit: `/items` got None with `shares=[/admin/items, /v1/items]`, and a non-alone route named a route with its own identity.
  - Integration: notFound at `/help` and `/missing` was None, and so were g1's `/list` and `/v1/list` and all of g3.
- **G4:** 82 lookups beside one `err.Error()` call, 24,301 beside 300.
- **G5:**
  - Go: roots included the unexported `login`, and the walk reached `audit`.
  - Python: the `audit` edit moved `/items`, `/orders`, `/report` and `/login`.
- **e1:** anchored at `(e1.Handler).ServeHTTP`, the interface's method.
- **q3a:** Go `through` was nil in both tests; the Python message named neither the interface nor the type.
- **R124:** 98 failures:
  - accepted Persian, emoji, RLM and soft-hyphen paths: 4
  - the free-text and name "keep" tests: 48 + 16
  - the code point and offset tests: 24 + 4
  - the U+202E route stop, and the json name with ZWNJ that was dropped: 1 + 1
- **R126:** 4 failures: the race message, the long path (which showed its start), the next action for a long path, and the 0444 case naming `d/go.mod`.
- **Follow-up:** `OPTIONS /items` lacked `requireAdmin`.

## Base comparison

I reused Group F's `dump.py` and compared the base (`git archive 9a82826` with its own goinspect) against the final commit (`git archive 4cb7a4c` with its own goinspect). The repos were shop, a fresh catalog-poc copy, parcels tracker and console, and library.

- **What it covers:** the inventory (routes, anchors, roots, `constants_hash`, `shares_anchor_with`, dropped), route hashes and reasons, declaration hashes, `changed_declarations` and the routes moved after a one-token edit, and `run_checks` with its output lines sorted.
- **Result: 0 differences.** None of these repos has a fixed wrapper site, curried middleware, a shared anchor, or a module type with unexported methods reached through an interface. So no difference was asked for and none appeared.
- **Control:** changing `"ok"` to `"up"` in catalog's `healthz` gave 4 differences against the final dump: the `handlers.healthz` declaration hash, the `GET /healthz` hash, the test output, and `test_ok` going from true to false.

## big/main.go times

This is `goinspect routes` alone. The reviewer measured through `GoAdapter`. I made 20,000 `g.Get("k")` calls in `use()`, because the copy in `p1f` had none left.

| Commit | Time |
|---|---|
| `9a82826` | 26.8 s |
| `af2fac4` (just before G4) | 27.4 s and 26.9 s |
| `812dcfe` (G4) | 0.97 s and 0.38 s |

- The reports from before and after G4 are byte-identical.
- The file as left, with 0 calls, took 0.31 s at the base and 0.28 s after G4.
- Other agents' gate runs were on the machine at the same time, so treat these as rough.

## What the brief or the reports got wrong

- **R127, "reads the call's handler only when its handler expression is the parameter":**
  - Taken literally, a site such as `logged(h)`, or `LOADS_JSON`'s closure that holds `load`, would read its own expression and lose its anchor and hash. That second case is the one R120 roots and an existing test pins.
  - I used "holds the handler parameter" instead.
  - My first cut also dropped R120's roots for the call's other arguments on fixed sites, which `9a82826` had. `4cb7a4c` restores them.
- **x1's preflight edit still moves `GET /items`.** The wrapper `get` is a root of every route it registers (R120), and its SSA names `preflight`.
- **R130's "if wrong" can't happen.** An interface method can only be satisfied by a method of the same name, so filtering by registrar names loses nothing.
- **R131 says "the type's own package".** For a promoted method, the right package is the method's own, because only that package's interfaces can name it. I used the method's package; it is the type's package for every method that isn't promoted.
- **Tests pinned behaviour the rulings change:**
  - The "a call the compiler cannot name" case was exactly R128's shape (`wrap := logged; wrap(parcels)`). I now write `wrap` twice in it.
  - `TestRoutesAnchorsAValueThatServesItselfAtItsServeHTTP` pinned the unexported `render` as a root (R131); `ServeHTTP`'s walk still reaches it.
  - `TWO_APPS` now keeps `notFound` at `/help` and `/missing` (R129).
- **R129's receiver rule:** I applied it only to receivers of a concrete type. A receiver of an interface type still counts by its variable, since two such variables can hold any two values.

## Shapes no rule covered (none of them stops the run)

- **Curried middleware from another module,** such as chi's `middleware.Timeout(d)(h)`: R128 covers module functions only, so the route has no anchor and no hash.
- **A curried factory that returns one wrapping function and one non-wrapping one:** it is peeled if any returned function wraps. That is `wraps`' existing any-semantics, and it is the risk R128 accepts.
- **A fixed wrapper site that holds a parameter other than the handler** (`chain(preflight, wrap...)`): its identity is its own, so two calls handing different middleware share the anchor. The roots do cover that middleware.
- **A handler called through a middleware variable** still shows as `(inline)`, and when unhashed its reason is still `inline`. The brief didn't ask to change that.
- **A repo path that doesn't exist** now reads "Cannot read the folder …, Make the folder … readable, or remove it". The old text was equally odd, and R126 doesn't cover it.

## What I didn't verify

- Linux, Unicode versions other than 15.0, and curl against any probe.
- Mutation testing.
- The interface scan on a real large repo.
- Promoted unexported methods from another package of the module under R131. I reasoned about these, but no test covers them.
- The reviewer's exact `GoAdapter` timing harness for big; I timed `goinspect routes` alone.
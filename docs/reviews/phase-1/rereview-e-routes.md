## Scoped re-review of Group E (wrapper sites and handler identity), branch `phase-1` at `722902e`

I built goinspect from HEAD and ran probes through `GoAdapter` (HEAD `src`), using `/private/tmp/claude-501/-Users-ionel-work-personal-emag/0f37fc47-1c0a-42ed-bd77-f0a6f96a2ead/scratchpad/p1e/probe.py` and the edit-and-compare script `/private/tmp/claude-501/-Users-ionel-work-personal-emag/0f37fc47-1c0a-42ed-bd77-f0a6f96a2ead/scratchpad/p1e/edit.sh`. Each probe service has its own folder under `/private/tmp/claude-501/-Users-ionel-work-personal-emag/0f37fc47-1c0a-42ed-bd77-f0a6f96a2ead/scratchpad/p1e/routes/`, named in each finding. I checked what each service really serves with curl. The arde repo is unchanged, and the untracked `fix-e-report.md` was already there.

**Verdict: not ready.** Three Criticals: two let a wrong or missing route through with no stop, and one leaves a fingerprint that doesn't move.

### C-1 (Critical, R114): a wrapper's path or method parameter that is reassigned before use is still trusted
`formOf` only checks that the identifier resolves to the parameter object (`refersTo`, `info.Uses`). That stays true after `path = ...`.
- **r10** (router-group style), pattern `\.get$` `{path:0,handler:1}`:
  ```go
  func (g *group) get(path string, h http.HandlerFunc) { path = g.prefix + path; g.mux.HandleFunc("GET "+path, h) }
  ```
  `v1 := &group{prefix: "/api/v1"}; v1.get("/items", items)`
  - ARDE prints `GET /items handler=items`, with `dropped` empty.
  - curl: `/items` 404, `/api/v1/items` 200.
  - It should stop with `builds its path`. `limitations.md:42` says a prefix held in a field stops the run, and in this form it doesn't.
- **r1**: `path = "/v1" + path` gives `GET /items`. Live: `/v1/items` 200, `/items` 404.
- **r1b**: `if !strings.HasSuffix(path, "/") { path += "/" }` gives `GET /items`. Live, `GET /items/` is a subtree route: `/items/` 200, `/items` 307.
- **r2**: `method = "PUT"` before `method+" "+path`, called with `"GET"`, gives `GET /items`. Live: GET 405, PUT 200.

Fix: refuse any form whose path or method parameter is assigned, incremented or has its address taken anywhere in the wrapper.

### C-2 (Critical, R114): a wrapper called through a function value loses its routes, with no line anywhere
Once one direct call matches the wrapper, its inner site is skipped on trust. A call through a value has no static target, so it is never read.
- **r3**: `a.get` is `"GET "+path`.
  ```go
  a.get("/healthz", health); registerShop(a.get)   // registerShop calls get("/items", items); get("/orders", orders)
  g := a.get; g("/direct", items)
  ```
  - ARDE lists only `GET /healthz`. Raw goinspect output: `unread_sites []`, `wrapper_calls []`, and `dropped` is empty.
  - curl: `/items`, `/orders` and `/direct` all 200.
- It should stop whenever a matched wrapper's `*types.Func` is used anywhere other than as a call's `Fun`. The same applies to a call through an interface.

### C-3 (Critical, R115): a handler's `ServeHTTP` method on a struct type is outside every hash
The SSA walk follows function operands only. When a module type is converted to `http.Handler` (a `MakeInterface`), its methods are never named, so the walk never reaches them.
- **h2**, `mux.Handle("GET /items", newItemsHandler(...))`, where `newItemsHandler` returns `&itemsHandler{}`:
  - Roots are `[newItemsHandler]`.
  - Editing `(*itemsHandler).ServeHTTP`, which is the whole handler, leaves the hash at `e5eb2afe7cbb`.
- **h2**, `secured(http.HandlerFunc(listOrders))`, where `secured` returns `&headerMiddleware{next}`:
  - Changing `"DENY"` to `"SAMEORIGIN"` in `(*headerMiddleware).ServeHTTP` leaves `79b27a87bc35`.
  - `limitations.md:78` says middleware that keeps its handler in a struct is inside the fingerprint, and that an edit to any root moves the hash. Measured false.

Fix: when the walk reaches a `MakeInterface` of a module type, also walk that type's methods. That over-reports, which is safe. It would also cover the `apiFunc` gap in line 79.

### I-1 (Important, R115 anchor): the "wraps" test reads syntax, so the anchor flips with how the handler is called
- **h1**: `func logged(next http.HandlerFunc) http.HandlerFunc`, which calls `next(w, r)`, used as `logged(listItems)` and `logged(listOrders)`.
  - Both routes print `handler=logged id=example.com/h1.logged`: two routes, one anchor.
  - Change only `next(w, r)` to `next.ServeHTTP(w, r)`, and the anchors become `listItems` and `listOrders`.
- **h3**: `spa(pages, fallback http.Handler)` calls `fallback.ServeHTTP` for unknown assets. `GET /` and `GET /admin/` both get `id=h3.notFound` and the same hash `a67557b26d99`, so the docs land on the 404 helper. Written as `fallback(w, r)`, both anchor at `spa`.

Each of the two common shapes lands on the wrong function. h1 recreates C2's w3c, where two routes share one identity and Task 14 would put both swag blocks on one function.
- Line 78 documents the `next(w, r)` case. It doesn't say that routes then share an anchor, and it doesn't cover the reverse case where the fallback is called through `ServeHTTP`.
- This needs a ruling, not a code tweak. One option: when routes with different roots resolve to the same anchor and that anchor is not their innermost handler, set `handler_id` to None with a reason.

### I-2 (Important, R115 fingerprint): what a route wrapper does to the handler is outside the hash
Roots come only from the handler argument at the wrapper's call. Neither the wrapper itself nor the handler expression at its inner site is ever added.
- **h4** (w4's `getJSON` shape): `a.get(path, load func(*http.Request) any)` registers a closure that calls `writeJSON`. Changing `writeJSON`'s Content-Type leaves `GET /items` at `f8473ef7c2a7`.
- **r8**: `a.get("/admin/items", items, requireAdmin)`, where the wrapper applies its variadic middleware. Changing the role check from `"admin"` to `"root"` leaves `bfe9b6311de2`.

Base had the same gap. Fix: add the wrapper, and the functions named at its inner site and in its call's other arguments, to the roots.

### Minor
- **Phantom mount rows (mt).** Each of these mounts gives an extra `ANY /x/` row beside the real sub-routes:
  - `secured(a)` (struct middleware) and `passed(d)` (`return http.HandlerFunc(next.ServeHTTP)`) are anchored at the middleware.
  - `logged(b.ServeHTTP)` gives a row anchored at `logged`.
  - `root.HandleFunc("/c/", c.ServeHTTP)` gives a row with no function.

  curl `/a/`, `/b/`, `/c/` and `/d/` all give 404. Line 47 only covers a function that doesn't pass the mux on, and these do.
- **Honest shapes that stop:**
  - **r7**: a typed method, `string(m)+" "+path`, stops with `builds its path`.
  - **r5**: nested wrappers stop, which is documented.
  - **mw1**: `a.handle("/admin/", adminMux)` through a form-1 wrapper stops with `registers nothing`. Its next action, "check the route pattern's positions", is misleading because the positions are right.
- **m_empty:** an empty method gives "the path `/items` must start with /". The leading space can't be seen, so the message makes no sense to a reader.
- **Constant arguments in a handler expression are not hashed.** Changing `paged(5, …)` to `paged(9, …)` leaves `70b8a78d6387`. That is outside R115's letter.
- **A middleware slice held in a package variable is not hashed.** In `chain(h, common...)`, editing `audited` moves `/c` (named inline) but not `/d` (`08ef0aeaed5f`). Lines 48 and 49 cover this broadly.

### Checked and found right
- **`method` key**, all matching curl:
  - A non-constant method stops (`method is not a constant`). `""` and `"get"` stop.
  - `"FOO"`, `"GET /x"`, `"GET\t"` and `http.MethodPatch` give `FOO /items`, `GET /x /items`, `GET /items` and `PATCH /items`, which is what net/http serves.
  - A call to another module's `r.Method(m, p, h)` puts the method before the path. This is tested at `routes_test.go:1289-1310`, and I agree it's right.
- **Wrapper forms:**
  - A closure inside the wrapper that registers (r4) reads right.
  - A generic wrapper, including an explicit instantiation (r6), reads right.
  - A range variable shadowing the parameter (r9) stops.
  - `http.MethodGet` as the method reads right.
- **Middleware:**
  - `defer next.ServeHTTP`, `http.HandlerFunc(next).ServeHTTP` and an inline `chain(h, audited)` anchor at the handler, with the middleware in the roots, and editing `audited` moves the hash.
  - A factory capturing its receiver (h6) moves when `render` changes.
- **8437de5's neighbours:** a mux behind `guarded(TimeoutHandler(e,…), func…)` and behind `TimeoutHandler(guarded(f, func…))` are both mounts.
- **Base comparison** (`git archive e775f77`, run with `PYTHONPATH` on its `src`):
  - shop has 2 differences, the catalog-poc copy 3 and library 4. Every one is `roots` added, equal to `[handler_id]`.
  - Hashes and unhashed reasons are identical.
  - Control: changing `id < 1` to `id < 2` in `product` moved `GET /products/{id}` on both sides.
  - I agree, for 9 of the 12 differences; I didn't rerun parcels.

### Not checked
- parcels, and Linux.
- The Go and Python test suites.
- `go next.ServeHTTP` (by reasoning it behaves like the defer case).
- Handlers on interface methods beyond the documented line.
- What Task 14 does with a shared anchor, which I reasoned about but didn't run.
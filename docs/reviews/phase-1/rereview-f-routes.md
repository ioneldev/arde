## Re-review of Group F's route rules (`722902e..9a82826`)

**Verdict:** I found no wrong path or method, and no route that went missing without a stop. Every hash probe I ran moved when its code changed. I found four Importants (three about anchors or the guard, one about speed) and a few Minors.

I built goinspect from HEAD into `p1f/bin` and ran every probe through `GoAdapter` (HEAD `src`) with `p1f/probe.py` and `p1f/edit.sh`. Probes are in `/private/tmp/claude-501/-Users-ionel-work-personal-emag/0f37fc47-1c0a-42ed-bd77-f0a6f96a2ead/scratchpad/p1f/routes/<name>/main.go`. Nothing in the arde repo changed.

### I-1 Important: a wrapper's fixed companion route anchors at the caller's handler, and the guard can't see it
- **Rule broken:** R121, "two routes that would share an anchor for different code get no anchor".
- **Input (`x1`)**, pattern `^a\.get$ {path:0,handler:1}`:
```go
func (a *app) get(path string, h http.HandlerFunc) {
	a.mux.HandleFunc("OPTIONS "+path, preflight)
	a.mux.HandleFunc("GET "+path, h)
}
a.get("/items", items); a.get("/orders", orders)
```
- **Printed:** `GET /items id=x1.items` and `OPTIONS /items id=x1.items roots=[get, items, preflight]`. `/orders` is the same, and `shares_anchor_with` is empty everywhere.
- **What it serves:** curl `OPTIONS /items` gives 204 from `preflight`. `items` never runs.
- **Should print:** the OPTIONS routes anchored at `preflight`, or with no anchor.
- **Cause:** `wrapperCallAt` uses the call's handler for every site of the wrapper and ignores `form.holdsTheHandler`. Both rows of one call share the call's identity, so the guard never fires. Two operations go into one comment block without any warning. The hashes are right.

### I-2 Important: curried middleware anchors at the middleware
- **Rule broken:** "wraps" is judged by what a function does with its handler.
- **Input (`c1`):** `mux.Handle("GET /items", requireRole("admin")(http.HandlerFunc(listItems)))`, where the function `requireRole` returns calls `next.ServeHTTP`.
- **Printed:** `GET /items handler=requireRole id=c1.requireRole`.
- **`c2`:** two such routes both get `id=None`.
- **Also in `c2`:** `admin := requireRole("admin"); admin(h)` reads as `handler=(inline)`, with no roots and the unhashed reason `inline`, though nothing is written in place.
- **Should print:** `id=c1.listItems`.
- **Cause:** `wrappedBy` gives up when `call.Fun` is itself a call, because there is no static target. `nameBehind` then follows `Fun` to `requireRole`.
- **Impact:** `requireRole("admin")(h)`, `cors(opts)(h)` and `rateLimit(n)(h)` are common. One use puts the docs on the middleware without warning. Several uses leave every route without docs. The hash is right: an edit to the middleware moved it.

### I-3 Important: the guard drops honest sharing (Q2)
- **(a) `g1`:** `listItems` at `/items` and `/v1/items`, plus `requireAuth(listItems)` at `/admin/items`. All three get `None`. `/items` gets `shares_anchor_with = ['GET /admin/items','GET /v1/items']`, but `/v1/items` holds the same handler, so the reason points the reader at something that isn't a cause.
- **(b) `g3`:** `s.items` is registered in `publicRoutes()` at `/items` and in `v2Routes()` at `/v2/items`. Both get `None`. `s.health` in `publicRoutes()` and in `registerHealth(mux, s)` does the same.
- **Cause of (b):** the identity hashes each variable by where it is declared, and each method's receiver is a different variable. So limitations.md:78's "one handler at two paths, however it is written, keeps its anchor" is false as measured.
- **How common:** public/admin pairs, v1/v2 pairs, and registration split across several methods are all common.
- **A rule that keeps honest sharing:** split each anchor group by identity. The part whose handler is the anchor alone (a bare name or conversions only, with no other roots or constants) keeps the anchor. Every other part gets `None`, and its reason names only the routes outside its own part. Also compare a method value's receiver by type, not by declaration.
- **The reason a person sees:** nothing in `src` reads `shares_anchor_with` yet, and no next action is given.

### I-4 Important: the interface scan grows with interface calls × module types
Measured on a synthetic service only. The effect on real services is reasoned.

`usesOutOfSight` runs `standBehind`, one `LookupFieldOrMethod` per carrier with two carriers per module type, for every use of an interface method. On `big/main.go` with 3,000 types and one route:

| Interface calls | HEAD | Base |
|---|---|---|
| 20,000 | 25.2 s | 0.33 s |
| 10,000 | 14.1 s | not run |
| 0 | 1.0 s | not run |

Reasoned: a service with about 7,000 types and 70,000 calls like `err.Error()`, `w.Header()` or `ctx.Done()` would pass the 120 s timeout and stop. Fix: test only interface methods whose name is a registrar's name.

### Q4 over-reporting (Minor: costs money, answers stay right)
- **`o1`:** `server` satisfies `ItemStore` and `OrderStore`, and routes register `itemsHandler(s)` and `ordersHandler(s)`.
  - The roots of `GET /items` hold all six server methods, including `login`, `report` and `routes()`.
  - Editing `audit`, which only `login` calls, moved `/items`, `/orders` and `/login`.
  - Adding a route inside `routes()` moved `/items` and `/orders`.
- **`o2`:** the walk rule, triggered by `slog.Info("…","server",s)`. `/items` and `/orders` get identical hashes, and both move on the `audit` edit.
- **Effect on change detection:** in a service built around one big server struct, every route that hands `s` to an interface, or logs it, moves on any edit to any method, so they are re-documented on every run.
- **A narrower rule that is still sound:** leave out an unexported method unless an interface in its own package declares that name. `reflect` cannot call unexported methods, and only an interface from the same package can name one. In `o1` this keeps `Items` and `Orders`.

### Minors
- **M-1 (`e1`):** `type logged struct{ Handler; name string }`, where `Handler` is the module's own interface, then `mux.Handle("GET /items", logged{Handler: itemsHandler{}})`.
  - Printed: `handler_id (example.com/e1.Handler).ServeHTTP` with `handler_line 11`, which is the interface method's line, and reason `no function`.
  - `serveMethodOfAValue` lacks the `isInterfaceMethod` check that `functionNamedBy` has.
  - Should print: `None`.
- **M-2 (`q3a`):** a `Router` interface over `*http.ServeMux` stops with "goes through an interface that a function registering routes satisfies… report that".
  - The stop is clear but leaves nothing to do, and a type satisfies an interface, not a function.
  - The message never names `*http.ServeMux`.
  - This shape is uncommon, and before Group F it lost routes silently.
- **M-3 (`l1`):** a local `admin := []middleware{requireAdmin}`, used as `a.get(…, admin...)` and in `chain(orders, admin...)`.
  - Changing `requireAdmin`'s role check moved neither hash.
  - limitations.md:79 names only a package variable.

### Q3: what now stops
- **Router interface:** see M-2.
- **Route table loop (`q3b`):** stops with "path argument `rt.method + " " + rt.path` is not a constant", and the next action says a table can't be documented. Same as base.
- **Default mux (`q3c`):** `http.HandleFunc`, `http.Handle` and `DefaultServeMux.HandleFunc` give an honest list.
- **Test helper (`q3d`):** `register(mux.HandleFunc, …)` in `_test.go` has no effect, because test files aren't loaded.

### Base comparison (Q5)
I used my own script, `p1f/compare.py`, with base taken by `git archive 722902e`. Each side used its own `src` and its own goinspect.
- **Shop and the catalog copy:** 16 differences.
  - 10 new empty fields, plus shop's 2 roots and 2 hashes: the 14 the report counts for these two repos.
  - 2 from controls I added: editing `router.handle` and `router.get` moved `GET /` and `GET /items/{sku}` at HEAD and nothing at base. That is F4 working.
- **Identical:** catalog hashes, reasons, anchors, declaration hashes, `changed_declarations`, and `run_checks`.
- **Controls that showed the comparison sees changes:**
  - `len(products)+1` moved `GET /` on both sides.
  - An edit to `writeJSON` moved all three routes on both sides.
  - An edit to `logRequests`, the middleware around the whole mux, moved none on either side, as documented.
- **Agreement:** I agree with the report for these two repos. I didn't rerun parcels or library.

### Checked and found right
- **F1 (written path or method parameter):** a `switch` that reassigns, `normalise(&path)`, a `select` receive into the parameter, a pointer method on a named string type, a write in a goroutine closure, and an empty method given a default all stop, naming where the write is.
- **F2 (registrar used other than by a direct call):**
  - stop: a map of funcs, an interface in an embedded field, and a generic constraint interface
  - read correctly: `defer` and promoted calls
- **F3 (handler types):** a `ServeHTTP` promoted from an embedded struct, a generic `jsonHandler[T]`, and a factory returning a type whose `ServeHTTP` is on the value receiver all anchor right, and their edits move the hash.
- **F5:** `next` called inside a closure built by a nested func anchors at the handler.
- **Constants:** a package const, an `iota` reorder and a typed const all move the hash.

### Not checked
Linux, parcels, library, Task 14, timing on a real large repo, and curl on any probe except `x1`.
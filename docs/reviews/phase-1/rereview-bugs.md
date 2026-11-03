# Phase 1 re-review: adversarial bug hunter

Tree: `5298e7b`, exported with `git archive` to `/private/tmp/claude-501/-Users-ionel-work-personal-emag/0f37fc47-1c0a-42ed-bd77-f0a6f96a2ead/scratchpad/p1r/bugs/tree`. I ran `GoAdapter` through `/private/tmp/claude-501/-Users-ionel-work-personal-emag/0f37fc47-1c0a-42ed-bd77-f0a6f96a2ead/scratchpad/p1r/bugs/probe.py`, on services I wrote in `/private/tmp/claude-501/-Users-ionel-work-personal-emag/0f37fc47-1c0a-42ed-bd77-f0a6f96a2ead/scratchpad/p1r/bugs/svc/`, with Go 1.27.1 on darwin/arm64. "Measured" means I ran the adapter and also ran the built service with curl where this says so.

## New findings

### C1 (Critical). A registration inside a wrapper is skipped on trust, so routes vanish and methods come out wrong with nothing in `dropped`

`siteAt` skips any site inside a function a route pattern matched when the site's path is not a constant (`routes.go:216-219`). It then stops only if the path holds a constant containing a slash (`routes.go:230-244`). The code comment says why: "A method such as "GET "+path has no slash, and the wrapper's name gives the method." The method then comes from the last part of the callee (`inventory_builder.py:312-321`). Nothing checks that the wrapper's call produced the routes it registers. Every case below is what you get after following ARDE's own stop message (`READ_A_CONSTANT_PATH`: "add a route pattern for the calls to that function").

| Service | What it serves (measured, curl) | ARDE, with the pattern the message asks for | `dropped` |
|---|---|---|---|
| `w1`: `func (a *app) handle(method, path string, h)` calls `HandleFunc(method+" "+path, h)` | `POST /orders` 200 (GET 405), `DELETE /orders/{id}` 200 (GET 405) | `ANY /orders`, `ANY /orders/{id}` | empty |
| `w2`: `resource(base, crud)` registers `"GET "+base`, `"POST "+base`, `"GET "+path.Join(base, "{id}")` (a local `item := base+"/{id}"` does the same) | `GET /books`, `POST /books`, `GET /books/3` 200, PUT 405 | one row, `ANY /books (inline)`. **`GET /books/{id}` is gone** | empty |
| `w4`: `getJSON(path, h)` registers `"GET "+path` | GET 200, POST 405 | `ANY /items` | empty |
| `w7`: `mountAPI(mux, prefix)` registers `"GET "+prefix+"items"` and `"…orders"`, pattern `{path:1, handler:0}` | `/api/items`, `/api/orders` | **both gone**; the inventory holds only `GET /healthz` | empty |
| `t1`: the repo's own fixture `routes_test.go:940`, where `post` registers `"GET "+path` | GET | `POST /items` | empty |

That last test asserts the shape is "not a prefix", so the suite records the wrong method as correct behaviour. In `w7` the pattern has no right answer: `mountAPI` has no handler parameter, and pointing `handler` at the mux turns the call into a mount (`routes.go:213`). ARDE accepts that silently.

None of this is in `limitations.md`. Line 45 covers only "a prefix held in a variable or a field … paths read short". The planned backstops are partial. R83 (Explore probes `ANY`) could recover the verbs for `w1` and `w4`. Task 12's live check only requests routes it has listed, so it cannot see a route that is missing. That leaves Explore's `suspected_gaps` as the only backstop (R94).

**Fix:** skip a site inside a wrapper only when its path expression is exactly the wrapper's own `path` parameter, either alone or after one constant `"METHOD "`. In the second form that constant gives the method and overrides the name. Any other form stops, as `wrapper_prefixes` does. A wrapper call that yields no site, such as a mount, also stops. This keeps `shop` (`pattern` alone, and `"GET "+path` inside `get`), and it turns all five rows above into stops.

### C2 (Critical). A call that takes exactly one handler-shaped argument is treated as middleware, so the route is rooted at a dependency, or lost

`innermost` peels any call that has exactly one argument of handler type (`route_handlers.go:37-39`). "Handler type" means any function taking a `ResponseWriter` and a `*Request`, "whatever else it takes" (`:110-128`). Fix-B item 5 widened it that way on purpose. So a factory with one handler-shaped dependency is read as middleware.

- **`w3`, a wrong root and a stale hash (measured).** `mux.Handle("GET /items", handleListItems(s, renderError))`, where `renderError(w, r, err)` writes the error reply, gives `handler=renderError`, `handler_id=example.com/w3.renderError`. I edited `handleListItems` so its reply drops the first item. The hash for `GET /items` stayed `4ba0d1e46e` before and after. `changed_declarations` names `main.go::func handleListItems`, but nothing ties that change to the route.
- **`w3c`, two handlers with one identity (measured).** I added `DELETE /items` as `handleClearItems(s, renderError)`. It got the same `handler_id` and the same hash, `4ba0d1e46e`. Under R84, Task 14 would put both routes' swag blocks on `renderError`.
- **`w4`, the wrong handler.** `spa(pages, notFound)` gives `GET /` with `handler=notFound`.
- **`w4`, a lost route (measured).** `routesPage(a.mux)` peels to the mux, which counts as a mount. `GET /debug/routes` answers 200 and is missing from the inventory, with `dropped` empty.

`limitations.md:77-78` say middleware is outside the fingerprint. They don't say that a factory can be taken for middleware.

**Fix:**
1. A plain function type counts as a handler only if its parameters are exactly `(ResponseWriter, *Request)`, optionally returning an error.
2. When the peeled call is a function of the service, hash its callee together with the argument (this over-reports, which is safe) and anchor at the callee. That is R84's "a factory is annotated at its declaration".
3. A mux reached only by peeling a service function stays a route row, as an `http.Handler`-typed mount already does (line 46). Task 12's live check can then see it, where today the route just disappears.

### M1. A failing test whose name also passed loses its failure text

`go_test_events.py:47,53` keeps the text of events only for names that never passed. In `k2d`, package `calc` has `TestSkipsOne` passing and `calc_test` has a `TestSkipsOne` that fails. The counts are right (`tests_run` 6, `test_ok` False). The test section reads only `FAIL` / `FAIL example.com/k2/calc`, with no failing test and no message, so the repair loop has nothing to work from. Fix: key the kept text by event order or by `Output` per run, not by name.

### M2. Two broken repos come back as profile problems

`namesNothing` (`service.go:77-81`) treats any error on the entry package that has no position as the profile's fault. Measured:

- `pp/c`: `package main` and `package other` in one folder. Exit 2, `bad main_package`.
- `pp/g`: `//go:build linux &&` in `main.go`. Exit 2.

`go build` fails on both, so with R98's order the baseline is red first. Either way, `limitations.md:70` ("A package whose only problem is in one of its own files is the repo's") is not true for these two.

### M3. `limitations.md:40` overstates `dropped` for a second main

The page says a route from another folder "is left out and listed in `dropped`". That only happens when a route pattern matched it. On `parcels` with no route pattern, which is what `discovery_guidance.py` tells Discovery to write for `net/http`, `dropped` holds only the env and response-field lines. `console/console.go:20: route outside…` appears only because `test_service_scope.py:31` passes `^switchboard\.HandleFunc$`. The plan's Task 12 (line 105) says "A second `main` always leaves `dropped` lines", which is false for a second main that reads no env, params, errors or JSON structs.

### M4. A library passes as `main_package`

`goinspect routes --main ./calc` (a library) exits 0. `extract_inventory` on `k1` with `./internal/api` returns 6 routes. It drops `cmd/server`'s `ANY /` and `GET /version` with no line, and the only line in `dropped` is `cmd/server/main.go:14: env outside main_package`. `built_service` refuses a library (`toolchain.py:94-104`), so Task 12 catches it if it builds before it uses the inventory. Line 70 doesn't list this case.

### M5. The `syscall.Exit` signal is noise

`limitations.md:81` says a test that calls `syscall.Exit(0)` "shows only as `tests_run` below `tests_listed`". Any benchmark already causes that: a clean run of `k2` gives 5 against 6. Say so.

## Shapes ARDE read right

| Shape | Service | Result |
|---|---|---|
| Constructor returning `http.Handler`, nested muxes, mount behind middleware `root.Handle("/api/", logged(api))` | k1 | sub-routes exact. `mux.Handle("/", api.New())` reads as `ANY /`, which line 46 documents |
| Method value promoted from an embedded struct, `s.ping` | k1 | id `(api.base).ping`. Editing it moves only `GET /api/ping` |
| Generic method value `s.books.list` on `*store[Book]`, generic factory `handleCount(s.books)` | k1 | ids `(*store[T]).list` and `handleCount`. An edit moves only its own route |
| Middleware chain `chain(http.HandlerFunc(s.show), logged)` | k1 | root `show` |
| Route registered in `init` on a package-level mux | k1 | listed. Shares a key with the mount function, so the reason is `shared key` (loud) |
| `//go:build prod` file registering `GET /prod-only` | k1 | not listed. The service built here doesn't serve it (curl 404 for unknown paths) |
| `StripPrefix` around a `FileServer` | k1 | a route with no hash (R96). Serves 200 |
| Named handler type `apiFunc(s.orders)`; `http.TimeoutHandler(s.slow(), …)` | w5 | roots `orders` and `slow` |
| Routes registered in a loop over a slice | w6 | exit 3 with `READ_A_CONSTANT_PATH` (line 41 documents it) |
| Wrapper with a whole pattern, or `"GET "+path` inside `get` (the `shop` shape) | shop, t1 | right |
| Tests: `x_test` package, parallel subtests, a skipped subtest, a fuzz seed, examples with and without output, a benchmark, an `integration`-tagged test, a nested module, also named in `go.work` | k2, k2w | `tests_run` 5, `tests_skipped` names the one subtest, `tests_listed` 6, `ignored_files` names the tagged file, `unchecked_modules` is `tools`. `go.work` doesn't extend `./...`, so the page is true |
| Exit 1 for a broken repo: an import nothing provides, a missing embed, an import cycle, a dependency excluded by constraints, a mistyped import, a syntax error | pp a,b,d,e,f,h | right |
| Exit 2 for a bad profile: a missing folder, a nested module's folder | k2 | right |
| Two mains | parcels | the other main's routes are left out |

## Verdict

Not ready. The new route code has two silent defects, C1 and C2, and the easiest way into C1 is ARDE's own stop message. Fix them before Phase 2 builds facts and anchors on `Route`.

**Plan changes:**
- **Phase 1 fix round (`routes.go`, `route_handlers.go`):** make the C1 and C2 changes, test-first. Use `w1`, both `w2` suffix forms, `w4`, `w7` and `w3`/`w3c` as tests, and flip `TestRoutesDoesNotCallAMethodOrAPlainPathAPrefix`'s `"GET "+path` case so it expects GET or a stop. Also add the M1 fix and its test.
- **`docs/limitations.md`:** line 40 (the route line in `dropped` needs a route pattern), line 70 (a library passes; two package clauses and a bad `//go:build` line read as profile problems), line 81 (benchmarks). After the fix, add a line on wrappers saying exactly which forms are read.
- **Task 12 (plan line 105):** change "always leaves `dropped` lines" to "leaves them when the other main reads env, params, errors or JSON structs". State that `validate_profile` enters `built_service` before it uses the inventory, so a library `main_package` fails first.
- **Task 14:** add `test_a_route_rooted_by_peeling_a_service_call_is_anchored_at_the_callee`, once C2's rule is in place.

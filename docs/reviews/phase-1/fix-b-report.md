# Phase 1 fix report: Group B, routes by type information

**Status: DONE_WITH_CONCERNS.** Items 1 to 10 are done, on branch `phase-1`, base `471281f`, not pushed. Nothing blocks Groups C and D. The concerns are in [Concerns](#concerns), and three of them need a ruling.

## Commits

| Commit | What | `make check` | `make integration` | Go tests |
|---|---|---|---|---|
| base `471281f` | | 1671 | 170 | 145 |
| `bc5f94f` | `goinspect routes` (items 2, 3, 4, 5, Go side of 1) | 1671 | 170 | 179 |
| `78aa554` | The inventory reads sites (items 1, 2, 3, 5, 6, 8) | 1750 | 177 | 179 |
| `ad7f93c` | Exact ids, `--main`, `RouteHashes` (items 1, 7) | 1750 | 183 | 185 |
| `a7e7872` | Patterns (item 9) | 1773 | 186 | 188 |
| `ff950ed` | Fixtures, wrapper prefix stop, docs (item 10) | 1774 | 201 | 190 |
| `aeb0fcc` | Fix: a service with a cgo file keeps its inventory | 1774 | 202 | 191 |
| `afb74c4` | Test: a function of another module inside the repo is no root | 1774 | 202 | 192 |

Both gates ran, exit 0, on the tree of every commit. The Go count at `78aa554` is read from the diff, which touches no Go file. The others are measured.

**Figures against their breakdown:**

- `make check`: 0 + 79 + 0 + 23 + 1 + 0 = 103 new tests, and 1671 + 103 = 1774.
- `make integration`: 0 + 7 + 6 + 3 + 15 + 1 = 32 new tests, and 170 + 32 = 202.
- Eval tests stay at 8: 1774 + 202 + 8 = 1984, and pytest deselects 210 = 202 + 8 from `make check`.
- Go: +34, +0, +6, +3, +2, +1, +1 = 47 new, and 145 + 47 = 192. `ad7f93c` is 7 added and 1 removed. `ff950ed` is 3 added and 1 removed.
- Diff: 44 files, +4032, -408. The commits sum to +4135, -511. The 103 lines of difference on each side were added in one commit and rewritten in a later one.
- Mutants: see [Mutation checks](#mutation-checks).

## Required at the end

### Item 10's fixtures

- `tests/fixtures/parcels`: two `main` packages (`cmd/tracker`, `cmd/console`) sharing `ledger`, and a route that only a blank import adds, in a folder no main names (`rollcall`).
- `tests/fixtures/library`: a front mux mounting two others, one behind middleware. Paths are constants and consts.

Both differ from `catalog-poc` in names, routes and shape. `gofmt` in `make integration` now covers all of `tests/fixtures`. `tests/adapters/go/test_service_scope.py` holds 16 test cases, and two of them run the built binary.

**`shop`'s route patterns: no change at all.** `^app\.handle$` and `^app\.get$` stay. Its wrappers `router.handle` and `router.get` hold the real sites, with paths the compiler doesn't know, and those sites are passed over because those patterns match. With no route pattern, or with only one of the two wrappers named, `shop` now stops at `web/router.go`, where it used to lose routes silently.

### Comparison with the base

Inventory, fingerprints and `changed_declarations` through the real `GoAdapter`, base source against final source, each with a one-token edit for `changed_declarations`. `fingerprints` no longer takes bare names, so hashes compare by route key. `capture.py` and `compare.py` in the scratchpad's `fixB`.

| Repo and profile | Differences, ignoring added fields | Routes | Hashes |
|---|---|---|---|
| `shop` | 1 | 2 | 2, all equal to base |
| `catalog-poc` copy, spec's `stdlib.json` | 0 | 3 | 3, all equal to base |
| `catalog-poc` copy, plus an env pattern for its helper | 1 | 3 | 3, all equal to base |

**Every other difference, and why it's right.** There is one kind, in two of the runs:

```
shop     dropped  base: ['settings.go:7: env argument `name` is `name`, not a string literal']   new: []
poc-env  dropped  base: ['cmd/server/main.go:59: env argument `name` is `key`, not a string literal']   new: []
```

That is bugs M2: a helper's own body. With no pattern on the helper (`poc`, profile as in the spec) the line stays, which is right, because then the value really is lost. A test pins each side.

**Added fields only:** `Route.file`, `line`, `handler_id`, `handler_file`, `handler_line`; `Inventory.declarations` (25 and 58 ids); `Inventory.unmatched_patterns` (empty on both).

`catalog-poc` original: `diff -rq` without `.git` matches the copy, and the `.git` mtime is still Nov 2 20:01:59. No git ran inside it.

### Probes

**The original `p1` to `p9` sources were not kept**: the bugs report's scratch folder doesn't exist. I rebuilt them from its text, so they are the same shapes and not the same files, and `go vet` passes on each. Every probe ran through the real `GoAdapter`, the same script on base source and final source. Output in the scratchpad's `fixB/probes`.

| Probe | Before | After | Verdict |
|---|---|---|---|
| `p1` two muxes behind `StripPrefix` | 5 rows, 3 wrong, stray `ANY` | Stop, exit 3, `main.go:12` | Ruled (R82) |
| `p2` mux under three names | 1 of 3 | 3 of 3, all hashed | Fixed |
| `p3` loop over a table | Stop, exit 3 | Stop, exit 3, says "not a constant" | Right: a true variable |
| `p4` const in a sum | Stop, exit 3 | 3 of 3, all hashed | Fixed |
| `p4` with the pattern narrowed | **1 of 3, silent** | 3 of 3 | Fixed. The review's headline |
| `p5` `.Methods("GET")` chained | `ANY /items` | `ANY /items` | Unchanged, by R83 |
| `p6` param shapes | `X-Trace-Id` read, `page` missing | Same | Unchanged, out of this group |
| `p7` `http.HandlerFunc(a)` | `(inline)`, 1 of 2 hashed | `h`, 2 of 2 hashed | Fixed |
| `p7` with `handler: 9` | `(inline)`, silent | Stop, exit 3 | Fixed |
| `p8` three mains | 3 routes, 0 dropped | 1 route, 2 dropped | Fixed |
| `p9` wrapper adds `/v1` | `ANY /items`, `POST /items`, **silent and wrong** | Stop, exit 3, `main.go:7` | Fixed, by an addition |

## What changed

### `goinspect routes` (items 1 to 5)

`service.go` loads the service in two steps. A graph-only `go list` from `main_package` gives the folders of that module, and the type checker then loads exactly those. So a blank import counts, and another `main` doesn't. Modules are compared, not import paths, because a nested module's path starts with this one's.

`routes.go` lists sites. A site is any static call resolving to the four `net/http` functions, or a call a route pattern matches. `route_handlers.go` follows a handler to its function.

**RED** (`a-red.txt`), 32 failed. `TestRoutesRejectsBadArguments` passed for the wrong reason, because an unknown command also exits 1:

```
--- FAIL: TestRoutesFindsEveryRegistrationWhateverItsReceiverIsCalled (0.00s)
    routes_test.go:123: exit 1, stderr "goinspect: unknown command \"routes\"; usage: ...
FAIL	goinspect	8.753s
```

**GREEN:** `ok goinspect 20.165s`.

### The inventory reads sites (items 1, 2, 3, 5, 6, 8)

`build_inventory` takes routes from sites and everything else from `match`. `_Scope` keeps what belongs to the service, by folder. Out of scope goes to `dropped`, in the form the list gives:

```
cmd/console/main.go:13: env outside main_package ./cmd/tracker
console/console.go:13: response field outside main_package ./cmd/tracker
console/console.go:20: route outside main_package ./cmd/tracker
console/console.go:21: param outside main_package ./cmd/tracker
console/console.go:22: error outside main_package ./cmd/tracker
```

**RED:** a first run was only a collection error, which proves little, so I added the reply types and constants alone and ran again: `163 failed, 200 passed in 7.65s`. **GREEN:** `363 passed in 6.13s`, then the gates.

`USE_A_LITERAL` is now `READ_A_CONSTANT_PATH`, because the old name stopped being true. It names the real way past a wrapper and says not to narrow.

### Exact ids (items 1, 7)

`fingerprints --roots FILE --main PKG DIR`. An id can hold a comma, `(*shop.pair[K, V]).serve`, so roots travel in a JSON file. `routes` and `fingerprints` share `functionID`, and a test feeds one's output to the other.

**RED, Go** (`c-red-go.txt`), 34 failed:

```
fingerprints_hash_test.go:92: exit 1, stderr "goinspect: flag provided but not defined: -main; ...
```

**RED, Python:** 2 failed in the client, 16 in the adapter. **GREEN:** `ok goinspect 23.614s`, and `92 passed` in the adapter and link tests.

The review's inputs are now tests: a variable sharing a function's name (bugs I5, silent C2 `wrongroot`) and a field sharing a method's name (`fieldroot`). Each gives `unhashed == {"GET /": "no function"}`, where base returned a confident hash of the wrong function.

### Patterns (item 9)

All as listed. **RED:** `160 failed, 647 passed`, most of them because `SourceReading` had no new field yet, plus Go's panic on the missing reply key. **GREEN:** `807 passed in 7.00s`, and `95 passed` in the adapter tests.

### Regression found by probing (`aeb0fcc`)

**`78aa554` to `ff950ed` could not read a service with any cgo file**, and I introduced that. `calls()` stopped on any call with no place in the repo, and cgo adds a generated file with no place in it. No test held a cgo file, so both gates passed.

I found it only because I went looking for code I had written and never run. RED:

```
routes_test.go:525: exit 1, stderr "goinspect: /Users/ionel/Library/Caches/go-build/ab/ab1e...-d:58:76 is outside ...
1 failed, 15 deselected in 2.16s
```

GREEN: `ok goinspect 25.191s`, and `1 passed`. The site reads `main.go:11`, and a handler in another cgo file reads `c.go:8`. The fix removed code: a call with no place is generated, so it's passed over, and four functions lost an error return. Both new tests skip when there's no `cc`.

## Mutation checks

I broke the code by hand on purpose, because several tests went green first time.

- **Route code:** 21 mutants. 17 died on the first run. The 4 survivors were real test gaps, closed: a conversion to a type that serves, a plain function passed to a factory, a known path inside a wrapper, and a `//line` directive pointing inside the repo.
- **Python and later Go:** 20 mutants. 18 died. The 2 survivors showed **redundant code, now removed**: a kind check in `patternsWithNoMatch`, which can't change the answer, and a `Synthetic` check that `Object()` already covers.
- **Final code:** the route batch was re-run after the cgo refactor, because its results described code that had changed. See below.

## Additions beyond the list

Each is small and tested. Please confirm or reject them.

1. **A function a pattern matches at one call counts at every static call to it.** Without this, `^a\.post$` loses `b.post("/two", h)`, because the site inside `post` is passed over. This is bug C2 one level up, and the list's wording alone allowed it.
2. **A wrapper that adds to the path stops the run**, as `wrapper_prefixes`: a site inside a wrapper whose path holds a constant with a slash. `p9` was silent and wrong, and the list gave it no item. It resolves nothing, so it keeps R82. A false match is a loud stop.
3. **`http.StripPrefix` is found by type, at every call in the service,** not by handler text. An import name, a variable and a helper can't hide it. It also stops on a call that isn't at a site.
4. **A mount behind middleware is a mount.** `withAudit(loans.Mux())`, which the list's "handler of type `*http.ServeMux`" doesn't cover.
5. **Handler resolving is stricter than the list in four ways.**
   - A wrapper around a value with no root gives no root, not a root at the middleware. `logged(local)` rooted at `logged` would hash the wrong thing, which is the defect silent C2 names.
   - A call with several handlers has no root, because a root at one misses edits to the rest.
   - A function from another module has no root, because a hash of nothing never moves.
   - "Handler-shaped" takes any function with a response writer and a request, so `func(w, r) error` counts. That is a common idiom.
6. **A route match in a file this build leaves out goes to `dropped`.** This **reverses a stated limit**, "a route in `only_windows.go` is listed", and a test said so. The type checker reads this machine's build, and the service built here has no such route. A line says so.
7. **`fingerprints` and `routes` load only the service's packages.** So a broken package outside the service no longer stops them, which reverses a Go test, and a `testdata` or `_` folder the service imports now has a body to hash.
8. **`routes` guards its own input.** `//line` isn't believed, because `handler_file` decides where the renderer writes. A constant path past 1,024 bytes reads as the stand-in, because `go/types` allows nearly 2 GB. A path that isn't UTF-8 has no value, which is Group C item 5's rule, done here because this is new code.
9. **`USE_A_PATH_PATTERN` is reworded too.** It also told the reader to change the pattern.
10. **`DeclarationReply.id`** replaces the format string the adapter held, because the builder needs it too.

## Decisions to check

- **`Inventory.declarations` holds every id in a service folder, test files included,** because `changed_declarations` reports those and a fact could cite one. An id outside the service is left out with no `dropped` line: every function in another `main` would otherwise add one, and nothing was lost.
- **`unmatched_patterns` is text, `kind regex`,** cleaned by `one_line`, because Tasks 12 and 17 list it and a profile regex can hold a line break. The default env pattern is never listed.
- **`goinspect` doesn't refuse a package that isn't `main`.** `build_service` is the one place that checks. So `--main ./pricing` gives an inventory from that package, and the build stops it later.
- **`./main.go` passes `MainPackage` and `build_service` but stops in `routes`**, exit 1, with a message saying to name the folder. `go list` gives such a file no module.

## Concerns

**Three need a ruling:**

1. **Silent gaps remain, and I measured each.** Reading source can't close them. Each is in `limitations.md`.
   - A route on a mux nothing serves is listed. My own fixture did this: the inventory said `GET /rollcall`, and the running binary answered 404. No report named this. Task 12's live check is the backstop, and it isn't built.
   - A prefix held in a variable or a field reads short: `a.root+path` gives `GET /items` for `/v1/items`.
   - A registration through an interface, or a method value in a variable, is lost silently. A pattern recovers it, and nothing tells you it's needed.
   - A route another module adds isn't listed, and `_ "net/http/pprof"` is the usual case.
   - A table behind one handler stays invisible, as the review said.

   **`[NEEDS CLARIFICATION: does "works on another service with no config" hold with these open, or does Task 12's live check have to land first?]`**

2. **The re-measure Group D item 5 asks for can't run under the current rule.** "3 of 10 real samples stopped; re-measure" needs `routes`, and `routes` compiles, through `go list -export`. The bugs review refused to compile third-party copies, and this brief says not to run them. **`[NEEDS CLARIFICATION: may Group D compile those module-cache samples? Compiling runs no init, but cgo and generators are a boundary someone should choose to cross.]`**

3. **`http.StripPrefix` stopping means the usual static-file line stops a repo.** `mux.Handle("/static/", http.StripPrefix("/static/", http.FileServer(...)))` is common Go, and R82 rules it stops. With addition 3 the stop is wider still. The route it would lose is only a file server, so a narrower rule may be worth asking for. I kept R82.

**The rest:**

4. **`extract_inventory` now compiles.** Measured on `shop`: 4.3 s and 296 MB with an empty `GOCACHE`, 0.18 s warm. It shares the 120 s `GOINSPECT_TIMEOUT_S`. A large repo with a cold cache may hit that. Baseline checks warm the cache first, so this should reach only a run that skipped them (inferred).

5. **Tests I got wrong, all caught.** My expected line numbers were wrong three times, and each time I checked the file and the code was right. The `/rollcall` failure was a fixture error, and it's what exposed concern 1.

6. **For Group C.** `routes` adds a surface for items 8 and 5: a bad patterns file exits 1 today, and `main_package` problems are `GoinspectError`. `TOO_LONG_TO_PRINT` and `tooLongToPrint` are unchanged and still match.

## Plan lines now stale

I didn't edit them. Line numbers are from the plan as it stands.

| Line | Says | Now |
|---|---|---|
| 87 | A root matching several functions unions their reach; none goes in `unresolved` | Exact id, no union. `without_body` is a separate list |
| 91 | A route argument that isn't a string literal raises; `ANY` from the last part of the call | A constant the compiler knows is read. A site inside a matched wrapper is passed over |
| 97 | A route missing from `route_fingerprints` counts as changed | It returns `RouteHashes`. Line 115 already says so |
| 109, 110 | Only a function literal lacks a block | **A variable, a field and a table entry have no `handler_file` either. Task 14 must key on `handler_id is None`, not on `(inline)`** |
| 447 | `goinspect fingerprints --roots a,b DIR` | `--roots FILE --main PKG DIR` |
| 491 | `route_fingerprints(self, repo, routes) -> dict[str, str]` | Takes `main_package`, returns `RouteHashes` |

Design spec line 431 has the same stale signature.

Task 12 (line 105) will reject the first submit of any repo with a second `main`, because `dropped` is then never empty. It accepts the same set on resubmit, so that costs one turn. It's worth knowing before it reads as a failure.

## Not verified

- **Linux.** All of this ran on macOS arm64, go1.27.1.
- **Real third-party trees.** See concern 2. Everything here is fixtures and probes.
- **The original `p1` to `p9` files.** They are rebuilt from the report's text.
- **`go.work` for `routes`.** I probed it once and it behaved as the limits say, but it isn't in the test suite. A vendored repo is covered for roots only, by `afb74c4`.
- **Windows-side claims.** The other-platform test uses `plan9`, which is simply not this machine.
- **A constant near 2 GB.** `go/types` refuses past that, as I read in its source. A doubling constant past it was refused by `go build`, measured. I didn't run one just under the limit, so that memory cost is unmeasured. The cap keeps it out of the reply.
- **The cold-cache timing on a large repo.** Measured on `shop` only.
- **Mutant coverage of `service.go`'s error messages,** beyond the cases in `TestRoutesFailsLoudly`.

`.env` was never read, printed, copied or bound. Nothing called the API. Only explicit paths were staged. Nothing was pushed. No subagent was spawned. The plan, the design and `docs/superpowers/` are untouched. `.superpowers/` is git-ignored, so this report is not committed.

## Files changed

**New:** `tools/goinspect/service.go`, `routes.go`, `route_handlers.go`, `routes_test.go`, `tests/adapters/go/test_patterns.py`, `test_service_scope.py`, `tests/fixtures/parcels/` (8 files), `tests/fixtures/library/` (4 files).

**Product:** `tools/goinspect/fingerprints.go`, `main.go`, `match.go`, `src/arde/adapters/language/go/adapter.py`, `goinspect_client.py`, `inventory_builder.py`, `src/arde/application/ports.py`, `src/arde/domain/inventory.py`, `profile.py`.

**Tests:** `tools/goinspect/fingerprints_test.go`, `fingerprints_hash_test.go`, `fingerprints_reach_test.go`, `match_test.go`, `tests/adapters/go/test_adapter.py`, `test_goinspect_client.py`, `test_inventory_builder.py`, `tests/adapters/test_repo_links.py`, `tests/domain/test_inventory.py`, `test_models.py`.

**Docs and tooling:** `docs/architecture.md`, `deviations.md`, `glossary.md`, `limitations.md`, `docs/specs/phase-1-reading-go.md`, `AGENTS.md`, `Makefile`.

Full RED and GREEN output, mutants, probes and captures are in `/private/tmp/claude-501/-Users-ionel-work-personal-emag/0f37fc47-1c0a-42ed-bd77-f0a6f96a2ead/scratchpad/fixB`.

## Re-run on the final tree

The 21 route mutants, re-run on `aeb0fcc`: 20 died and 1 survived, `outside-module function is a root`. It had died the first time.

The cgo fix made a place in the repo a second condition for a root, which hid `holds` for anything outside the repo. It looked redundant, like the two removed earlier, **and it isn't.** A nested module and `vendor/` sit inside the repo. With the check removed, the new test prints:

```
routes_test.go:407: a nested module: got root example.com/dep.Serve in dep/dep.go
routes_test.go:407: a vendored module: got root example.com/dep.Serve in vendor/example.com/dep/d.go
```

That would have sent the renderer's comment into vendored code, so the code stays and `afb74c4` pins it. That mutant now dies, for both layouts. I re-ran only that one after `afb74c4`, which changes a test file and no product code.

Totals: 41 mutants across the two batches, 39 killed on the code as it stands, and 2 that led to removing redundant code, so they no longer exist.

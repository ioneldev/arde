# Phase 1 review: silent failures

Reviewed `2fbd052..f145fe7` on Go 1.27.1, darwin/arm64. Every experiment ran the real `GoAdapter`, `goinspect` or `LocalServiceRunner` in `/private/tmp/claude-501/-Users-ionel-work-personal-emag/0f37fc47-1c0a-42ed-bd77-f0a6f96a2ead/scratchpad/p1/silent`, where `repos/` holds the inputs and `*.out` the outputs. Everything said about a later phase comes from the plan, not from running it, because those phases don't exist yet. Other inferences are marked.

## Critical

### C1. `test_ok` and `test_count` say the tests passed when no test ran

**Where:** `src/arde/adapters/language/go/toolchain.py:112`, `:114`, `:122`, `:141`. `src/arde/application/ports.py:143`.

`run_checks` reads the exit code of plain `go test ./...` and takes the count from `go test -list`. Neither says what ran. ARDE's environment allow-list makes this likely: a test guarded by `DATABASE_URL` skips under ARDE and runs in the user's shell.

**Input:** `repos/skips`, three tests that call `t.Skip` without `DATABASE_URL` and `t.Fatal` with it. The variable was exported in ARDE's own process.

```text
ARDE:     test_ok: true, test_count: 3, output: "ok  example.com/skips 0.321s"
By hand:  DATABASE_URL=x go test -count=1 ./...  ->  --- FAIL: TestOne, TestTwo, TestThree
go test -json in ARDE's environment: {'skip': 3}
```

Two more shapes give the same false pass:

| Repo | Result | What ran |
|---|---|---|
| `benches`: two benchmarks, no tests | `test_ok: true, test_count: 2` | Nothing: `[no tests to run]` |
| `testmain`: `TestMain` exits 0 before `m.Run` | `test_ok: true, test_count: 0` | Nothing, and one test exists |

**Who is misled:** `test_zero_tests_gives_warning` (plan:898) is the only planned reader of `test_count`, and it fires on zero. `skips` and `benches` are non-zero, so design section 13's "Zero tests give a warning" stays quiet, and ARDE's "proves it changed no behaviour" rests on tests that never ran. No later check measures whether the tests ran. `task-8-review.md` item 7 noted in passing that a skip counts as one test, but no ruling followed.

**Fix:** run `go test -json -count=1 ./...` and count events for top-level tests. `CheckResult` gains `tests_run: int` and `tests_skipped: tuple[str, ...]`. Keep the human text in `output`. A cut `-json` stream must stop the run through `_require_whole`, so that stream needs a larger limit than 1 MB. Warn on `tests_run == 0` and on any skip.

### C2. A route's fingerprint can hash unrelated code, so a real edit moves nothing

**Where:** `src/arde/adapters/language/go/adapter.py:94-95` (`_root_of`), `tools/goinspect/fingerprints.go:64-70`, `:177-189`.

A handler is cut to its last name, and goinspect looks that name up across every declared function. The registration may actually name a local variable or a struct field. If nothing shares the name, the route goes to `unresolved`, which is safe (`last.out`: `local` and `orders`). If anything shares it, ARDE returns a confident hash of the wrong function.

**This is not the settled "call graph is static" limit.** The graph is fine. The walk starts in the wrong place, and the wrong start can be fixed exactly with type information.

**Inputs and outputs (`roots.out`, `live.out`):**

| Repo | Registration | Unrelated function hashed | Edit | Fingerprint moved |
|---|---|---|---|---|
| `wrongroot` | `health := func(...)`, `mux.HandleFunc("GET /health", health)` | `(database).health` | closure now returns 500 and doubles the price | **False** |
| `fieldroot` | `mux.HandleFunc("GET /orders", s.orders)`, field set to `realOrders` | `(audit).orders` | `realOrders` returns 500 | **False** |
| `struct1` | `h := NewHandlers(store)`, `mux.HandleFunc("GET /items", h.List)` | `(Store).List` | `discount`, called by the closure, `n / 2` becomes `n * 2` | **False** |

`struct1` is an ordinary layout: a struct of handlers built by a constructor, over a data layer with a method of the same name. `reach` printed `{'List': ('(example.com/structh.Store).List',)}`.

**Who is misled:** Task 24 treats a missing route as changed and a present, equal hash as unchanged, so this is the one path that silently says "unchanged". `changed_declarations` does list `main.go::func discount`, but a fact linked only to `GET /items` does not go stale. That leaves stored requests as the only backstop, and only for a request that exercises the changed value.

**Fix:** resolve the handler at its registration. Add `file` and `line` to `Route`, which `_route_from` has and drops at `inventory_builder.py:121`. Pass them to `fingerprints` in place of bare names, find the call in the loaded syntax, and read the argument's object from `TypesInfo.Uses`. A `*types.Func` is the exact root, through `program.FuncValue`. Anything else goes to `unresolved`, which Task 24 already handles.

### C3. A repo with two `main` packages gets one merged inventory, and most of the phantom facts have no check

**Where:** `tools/goinspect/match.go:60`, `tools/goinspect/declarations.go:37`, `src/arde/adapters/language/go/adapter.py:57-64`, `src/arde/domain/inventory.py:10-13`.

`extract_inventory` receives `profile.main_package` and never uses it to scope anything. Every non-test file in the module is read, and `Route` holds no file, so a caller can't filter afterwards.

**Input:** `repos/live` and `repos/phantom39280`, with `main_package="./cmd/api"` and a second `cmd/admin`.

```text
inventory: ['POST /admin/wipe', 'GET /items']   env: ('ADMIN_PORT', 'ADMIN_TOKEN', 'PORT')
built service: POST /admin/wipe -> 404, GET /items -> 200

phantom repo:
  params:          [{"name": "force"}]
  error_codes:     [{"code": "admin_only", "status": "http.StatusForbidden"}]
  response_fields: [{"struct": "WipeReply", "file": "cmd/admin/main.go", ...}]
  dropped:         []
```

Every value above belongs to the binary ARDE did not build, and nothing marks it. In `two_mains`, `GET /health` exists in both binaries, so the key is left out of `route_fingerprints` and counts as changed on every run, with no reason given.

**Who is misled:** R65 accepts a phantom route because it is "a visible finding". That holds for routes only if the live check stops the run, which `docs/limitations.md:34` calls untested, and R58 calls the same thing "a false blocking finding", so such a repo may never finish. The other kinds have no live check at all (inferred from the plan): design line 359 says live checks can't catch an invented param, design line 363 resolves docs against this same inventory, and `limitations.md:25` says a rule with no HTTP effect goes unchecked. So docs for `cmd/api` can state that it reads `ADMIN_PORT`, takes `force` and returns `admin_only`, and every check passes. `catalog-poc` has one `main`, so the acceptance run cannot show this.

**Fix:** in `extract_inventory`, run `go list -deps -f '{{.Dir}}' -- <main_package>`, keep matches and fields from those folders, and put the rest in `dropped`, for example `cmd/admin/main.go:13: route outside main_package ./cmd/api`. This keeps R65's point, because nothing vanishes silently. Add the two-main case to `tests/fixtures`.

## Important

### I1. An inventory with holes looks the same as a repo that has none of those things

**Where:** `src/arde/adapters/language/go/adapter.py:18`, `:57-64`, `src/arde/adapters/language/go/inventory_builder.py:153-163`, `docs/limitations.md:53`.

**Input:** `repos/inv`, with `req.URL.Query().Get("limit")`, `q.Get("page")`, `os.LookupEnv("API_TOKEN")`, a second mux `api.HandleFunc("GET /receipts", ...)`, and patterns `^mux\.Handle(Func)?$`, `^r\.URL\.Query\(\)\.Get$` and `^writeError$`.

```text
routes: only /api/ and /ping      GET /receipts is gone
params: []                        two real params
error_codes: []                   pattern matched no call
env_vars: ["PORT"]                API_TOKEN is gone
dropped: []
```

`dropped` covers a call that matched and gave nothing. A pattern that matched nothing, or matched only some calls, leaves no trace. So `limitations.md:53`, "so an empty list is never silent", is false. Task 12 rejects zero *routes* only (plan:680). Design line 359 compares spec and inventory both ways, which cannot find a route missing from both. Design line 137 relies on Explore noticing, and only in changed code.

**Fix:**
1. The `match` reply lists each pattern with zero matches, and `Inventory` carries them.
2. The adapter adds a Go-only candidate pattern, `(^|\.)Handle(Func)?$`, and reports each candidate call that no route pattern matched as a `dropped` line.
3. Widen the default to `^os\.(Getenv|LookupEnv)$`.

### I2. A symlinked `.go` file is compiled and served, and invisible to the inventory

**Where:** `tools/goinspect/walk.go:248-252` (`entry.Type().IsRegular()`).

**Input:** `repos/linked`, where `routes.go -> ../linked_shared/routes.go`.

```text
go list -f '{{.GoFiles}}' .   ->  [main.go routes.go]
routes:        ['GET /visible']     POST /hidden is gone
declarations:  ['main', 'visible']  register, hidden and Reply are gone
build_ok:      True
```

Go follows the link, and `match` and `declarations` do not. `fingerprints` loads through `go list`, so it should see the code (inferred). Plan line 83 says non-regular files are skipped, and Task 24 covers only `compare`'s retargeted link. The inventory case is not in `limitations.md`, and the phase rule calls it a defect.

**Fix:** in `isListedFile`, follow a symlink whose target is a regular file, as `go/build` does. `compare` then sees a retargeted link too, which closes the Task 24 note.

### I3. A wrong `handler` position turns every route into `(inline)` with no message

**Where:** `src/arde/adapters/language/go/inventory_builder.py:138-140`.

```python
handler = match.args.get("handler", "")
```

`path` raises when missing (`:105-113`). `handler` defaults to a value that reads as a normal inline handler.

**Input:** `args={"path": 0, "handler": 5}`, past the last argument, in `repos/inv`.

```text
routes: [{"method": "ANY", "path": "/api/", "handler": "(inline)"},
         {"method": "GET", "path": "/ping", "handler": "(inline)"}]
route_fingerprints: {}      dropped: []
```

Every route then counts as changed on every run, the run costs more, and nothing says why. A registration always has this argument, or it would not compile, so a missing key can only mean a wrong profile.

**Fix:** raise `RouteNotUnderstoodError` when the key is absent, and keep `(inline)` for an argument that is present but not a plain name.

### I4. The fields that report skips have no reader

**Where:** `src/arde/adapters/language/go/adapter.py:69-73`, `src/arde/domain/inventory.py:44-46`, the plan.

- `reply.unresolved` is discarded. `repos/partial39280` (`last.out`): goinspect said `('Index', 'local', 'orders')`, and the adapter returned `['GET /good']`. An inline handler, a shared key, a name with no function and code with no body all arrive as the same absence, so a user can't learn why a route re-runs every time.
- R60 says Task 12 shows `dropped` to the model and Task 17 traces it. Neither is in the plan: `dropped` as a field appears nowhere in it. The promise lives only in `progress.md`.

**Fix:** return `RouteHashes(hashes: dict[str, str], unhashed: dict[str, str])`, where `unhashed` maps each key to its reason. Carry the plan changes listed below.

### I5. A repo that doesn't load raises from `run_checks` and loses the real reason

**Where:** `src/arde/adapters/language/go/toolchain.py:123`, `:163-172`, against the docstring at `ports.py:137`, "A failed check is data, not an error".

**Input:** `repos/broken_mod`, and `repos/no_mod_with_go`.

```text
GoCommandError: Cannot list the files the build leaves out: go: errors parsing go.mod: ...
NEXT: Run the same list command by hand in the repo to see why, then run again.
```

The build, vet and test sections are thrown away, and the message blames a side step. `test_baseline_red_stops_with_clear_message` (plan:895) will not get a `CheckResult` to report. The message does contain Go's text, so this is misleading, not silent.

**Fix:** `_ignored_files(repo, build_passed=build.succeeded)` raises only when the build passed, the same shape as `_count_listed_tests`. Otherwise it returns `()`, and the red result carries the reason.

### I6. A nested module's failing test reads as green

**Where:** `src/arde/adapters/language/go/toolchain.py:108-112`, `:188-193`.

**Input:** `repos/nested`, with `billing/go.mod`, a test that calls `t.Fatal`, and a tagged test.

```text
build_ok True, vet_ok True, test_ok True, test_count 0, ignored_files ()
```

`./...` stops at the module boundary, and `_is_passed_over` removes the folder from `ignored_files` too. Here the zero-tests warning happens to fire. With one test in the root module it would not. `limitations.md` covers the skip for `declarations` and `match`, not for the checks.

**Fix:** `CheckResult.unchecked_modules: tuple[str, ...]`, filled from the third branch of `_is_passed_over`, plus a Task 17 warning and a limitations line.

## Minor

1. **`build_ok` is true when nothing was built.** `toolchain.py:118`. `empty_with_mod` and `all_tagged` give `build_ok: true` with `go: warning: "./..." matched no packages`. In both, vet and test exit 1, so the baseline still goes red on Go 1.27.1. Treat that warning as not ok.
2. **`go test` can answer from cache.** `toolchain.py:112`. At a fixed path, with the outside dependency broken: `ok example.com/wc (cached)`, `test_ok: True`, even after re-copying the tree. A new path missed the cache and failed. Task 10's `input_copy(run_dir)` is per run, so this should reach only repeats inside one run (inferred). `-count=1` closes it, and C1 adds it anyway.
3. **A timeout throws away the output.** `child_process.py:136-141` raises before `_read_back`. With the limit set to 8 s for the experiment, `repos/hang` printed `CLUE: waiting for the database at db:5432`, and the error held only "ran longer than 8 s". `GO_TEST_TIMEOUT_S` (600) also equals Go's default 10m, and ARDE's clock starts first, so Go's goroutine dump naming the hung test should never arrive (inferred). Put the tail in the error, and pass `-timeout 540s`.
4. **`test_count` is partial when tests fail.** `halfcompile` has 8 tests and gives `test_count: 3`, with `test_ok: false`. `toolchain.py:131` says "Zero must mean zero tests", which holds only after a pass. Harmless while a red baseline stops the run. Make it `int | None`.
5. **Raw exceptions cross the `ArdeError` boundary.** Plan line 59 says the pipeline catches `ArdeError` alone. `makefile.py:26` raised a raw `PermissionError` on a mode 000 Makefile, and `goinspect_client.py:90` raised a raw `NotADirectoryError`. Both reach the user as tracebacks with no next action.
6. **Next actions that send the user the wrong way.** `child_process.py:87-90`: a missing repo folder says "Install go or put it on PATH", and "Install .../goinspect" for the helper. `goinspect_client.py:158-161`: "usually a Go file that does not parse" also appears for a missing `go.mod` and for `exit -9` with empty stderr.
7. **Non-UTF-8 literals merge.** `tools/goinspect/main.go:130-134`. `encoding/json` replaces invalid bytes, so `"GET /caf\xe9"` and `"GET /caf\xe8"` became one route, `/caf�`. Treat a literal that fails `utf8.ValidString` as not a literal.
8. **Any socket error reads as "not listening".** `service_runner.py:176-181`. With the file table full and a listener up: `_accepts_connection` False, `_port_is_released` True. The stop check then passes on a port it could not test. Catch `ConnectionRefusedError` and timeouts only.
9. **`changed_declarations` is blind to imports.** `tools/goinspect/declarations.go:111-120`. Swapping `html/template` for `text/template` gives `[]`, though the fingerprint moved. Adding `_ "net/http/pprof"`, which adds served routes, gives an equal inventory, `[]` and an unmoved fingerprint, and only `compare` sees it. Design line 271 knows this, and Task 24 plans to classify it as real, so only `limitations.md` needs a line, because a report for such a change names a file and nothing else.
10. **Two limits are missing from `limitations.md`.** A struct with no `json` tag is not a response field, though `encoding/json` still emits it (R32 rules on it, so document it, don't reopen it). A dangling Makefile symlink returns `make_targets: []`, the same as no Makefile.

## What held

- **`goinspect` failure through the client.** Eight fake binaries: exit 1 with no stderr, exit 0 with empty, partial, `null` and extra-field replies, a valid reply followed by `kill -9`, and a valid reply with exit 3. All seven bad replies raised `GoinspectError`, and none was accepted. The eighth, valid JSON with a stderr warning, was accepted, which is safe because goinspect writes stderr only on failure (`main.go:41` is its one write).
- **One package fails.** `fingerprints` exits 1 and lists the problem, so a partial call graph can't happen. `run_checks` gives `False False False`. An unreadable file or folder stops `match` and `declarations`.
- **Half-working `go vet` and `go test`.** `halfcompile`: `vet_ok: false`, `test_ok: false`, and the failing package is named in `output`.
- **`go list -e` partial answers.** My hypothesis was wrong: in `elist`, a folder that doesn't load still reports its hidden test file.
- **A timeout mid-way stops the run.** `ChildTimeoutError`, with no partial `CheckResult`.
- **Every `except` in Python:** the only ones that swallow are `adapter.py:37` (settled), Minor 8, and the cleanup in `toolchain.py:57`. The rest re-raise as `ArdeError` or fill in a message. In `service_runner.py`, each `contextlib.suppress` is followed by a check that reports a process that survived. In `child_process.py` they sit on paths that already raise or have already reaped the child, and the grandchild a timeout can't reach is a listed limit.
- **Empty repo and test-only repo:** `NoRoutesError` from `extract_inventory`, and `build_service` refuses both. Two mains: `build_service` builds the named one.
- **`catalog-poc` copy** (with `cp`, no git in it): 3 routes, 8 tests, the known `dropped` line, every `reach` correct, and no false alarm. It also can't reveal C1, C2 or C3, because it has one `main`, named methods and no skips.

## Plan changes

- **Task 8 follow-up, before the gate:**
  - `Route` gains `file` and `line`.
  - `extract_inventory` scopes to `go list -deps` of `main_package`, and the rest goes to `dropped`.
  - `fingerprints` resolves handlers at their registration through `TypesInfo`.
  - `run_checks` uses `go test -json -count=1 -timeout 540s`.
  - `CheckResult` gains `tests_run`, `tests_skipped` and `unchecked_modules`.
  - `_handler_name` raises on a missing key.
  - `route_fingerprints` returns hashes plus a reason for each unhashed route.
  - `match` reports zero-match patterns and unmatched registration calls.
- **Plan line 83:** replace "Non-regular files are skipped" with "A symlink to a regular file is listed, as the Go tool builds it; other non-regular files are skipped".
- **Plan line 97 (Task 24):** drop the retargeted-symlink clause once `compare` follows links, and count a route in `unhashed` as changed, recording its reason in the change report.
- **Task 12:** add `test_submit_profile_rejects_pattern_matching_nothing`, for every kind, and `test_validate_shows_dropped_lines_to_the_model`. `validate_profile` also returns a finding for each unmatched registration call.
- **Task 17:** replace `test_zero_tests_gives_warning` with `test_zero_tests_run_gives_warning`, and add `test_skipped_tests_give_warning`, `test_dropped_lines_become_warnings`, `test_unhashed_routes_become_warnings` and `test_unchecked_modules_give_warning`. `test_baseline_red_stops_with_clear_message` must include a broken `go.mod`.
- **`docs/limitations.md`:** correct line 53, and add Minors 9 and 10, the nested-module check limit, and the `go test` environment: a test that needs a variable outside the allow-list skips or fails under ARDE.

## Limits of this review

Measured on one host and one Go version. Vet and test failing on "no packages" is a Go 1.27.1 result. What later phases would do is inferred from the plan. One scratch command was refused by a safety check because it ran `rm` inside `sh -c`. Nothing in it ran, the file it named did not exist, and I redesigned the experiment to need no removal, so nothing is left to clean up.

**Verdict: not ready for the gate. Phase 1's failure paths are loud, but its success paths can lie, so fix C1, C2 and C3 first, because Phase 2 builds on all three.**

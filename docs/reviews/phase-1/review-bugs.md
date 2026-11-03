# Phase 1 review: bugs

Reviewed `phase-1` at `f145fe7`. Measured on macOS arm64 with Go 1.27.1 and `GOPROXY=off`.

**Samples:** 13 real programs copied from the module cache and `GOROOT` (10 plain `net/http`, 3 chi), plus 9 small synthetic probes marked `p1` to `p9`, written for shapes the cache doesn't hold.

**Hard limits kept:** nothing downloaded, cloned, pulled or run from third parties. `goinspect fingerprints` was never run on a third-party copy, because it compiles: `packages.go:1594` makes `LoadSyntax` use `go list -export`. No Linux or Docker was used.

"Measured" means I ran it. "Inferred" means I reasoned it and did not run it.

## Critical

### C1. Mounted, prefixed and table routes come out wrong or missing, and nothing says so

`build_inventory` reports what the regex saw, and `dropped` can't carry a route: it holds params, errors and env only (`inventory_builder.py:87`). The spec's rule is "if a route can't be understood, the tool says so or stops". Here neither happens.

Measured:

| Sample | Real routes | Inventory | `dropped` about routes |
|---|---|---|---|
| `s1` chi `todos-resource`, profile `^r\.(Get\|Post\|Put\|Delete)$` | 14, grep agrees | 8 rows, only 1 with its true path. `GET /users/{id}` reads `GET /`, and `GET /todos/{id}/sync` reads `GET /sync` | none |
| `s4` google/pprof `webui.go`, plain `net/http`, profile `^mux\.Handle$` | 12: an 11-entry map under `http.StripPrefix("/ui", …)`, plus a `/` redirect | 2: `ANY /`, `ANY /ui/`. 10 URLs missing | none |
| `p1` two muxes behind `http.StripPrefix` | `GET /api/items`, `GET /api/items/{id}`, `GET /admin/items` | `GET /items` twice, `GET /items/{id}`, stray `ANY /api/`, `ANY /admin/` | none |
| `p9` wrapper adds a prefix and a method | `/v1/items`, `POST /v1/items` | `ANY /items` twice | none |

`set()` in `_sorted_unique` makes it worse: identical rows from two mounted routers merge, so the route count falls with no trace (14 to 8 in `s1`).

`docs/limitations.md:34` lists this as a Gin-style group limit with a 404 backstop. That understates it in three ways:

- It happens in plain `net/http`, through `http.StripPrefix`, which is inside the stated scope.
- The backstop has no owner (see I6).
- The backstop often can't fire. 6 of the 7 plain samples that gave an inventory register `"/"`, which is a catch-all, so a wrong path gets that handler's answer and not the mux's 404. In `s1`, `GET /` really answers. (Inferred from `net/http`'s pattern rule, not run.)

**Fix:** stop where the shape can be seen. A matched route whose handler text holds `http.StripPrefix(`, or a `go.mod` that requires a known router, raises instead of guessing. Limitations says only `net/http` is supported, so refusing is the honest answer. A table served by one handler (`s4`, `s8`) can't be detected statically, so it stays a stated limit.

### C2. Nothing checks that a route pattern found all the routes, and ARDE's own error tells you how to lose them

`match.go:189` applies a regex to callee text, with no types, so a missed spelling looks exactly like no route.

Measured, `p4` (synthetic):

```go
mux.HandleFunc("GET /healthz", health)
api.HandleFunc("GET "+apiPrefix+"/items", items)
api.HandleFunc("POST "+apiPrefix+"/orders", orders)
```

- Profile `^(mux|api)\.HandleFunc$` stops with `RouteNotUnderstoodError`, exit 3. Its next action (`inventory_builder.py:39-42`) reads: "Change the route pattern in the profile so its path argument points at a string literal".
- Following that sentence gives `^mux\.HandleFunc$`, which returns **1 route, `dropped` empty, no error**. Two of three routes are gone.

Inferred: Task 12 sends this text back to the model as a tool error, and the model's job is to make the check pass, so this is the likely path.

Same cause, measured:

- `p2`: a mux passed into helpers under the names `mux`, `r` and `s.router`. `^mux\.HandleFunc$` finds 1 of 3, with `dropped` empty.
- Dropping the receiver doesn't work either. `\.(Handle|HandleFunc)$` stops on `h.Handle(context.Background(), rec)`, a `slog.Handler` call. Across real trees that regex matched 66 calls in `GOROOT/src` with 2 literal paths, and 11 in wazero with 0.
- `p6`: `dropped` shrinks when a profile gets *narrower*, not more correct. A better param profile went from 2 dropped lines to 0 while `page`, `from` and `to` stayed missing.

**Fix:** see Task 8 under Plan changes. Use types to list the real `net/http` registration sites and compare them with what the patterns matched.

### C3. The inventory covers the module, not the service

`extract_inventory` never reads `main_package` (`adapter.py:57-64`), and `match` walks the whole tree.

Measured:

- `golang.org/x/tools` with `main_package=cmd/present`: 9 routes, and 4 of them (`/` from `handleRoot`, `/main.js`, `/main.css`, `/select.json`) belong to a different program, `go/types/internal/play`.
- `p8`: `cmd/api`, `cmd/worker` and `examples/demo` give 3 routes. The service has 1. `dropped` is empty.

Inferred: Task 13's `coverage_gaps` then asks for a fact on a route the running service doesn't have, so the run can't finish honestly.

**A directory filter is the wrong fix.** `cmd/present` gets `/compile` from `_ "golang.org/x/tools/playground"` (`play.go:23`), in another folder. Scope must follow `go list -deps <main_package>`.

## Important

### I1. A path held in a constant stops the run, and no profile can fix it

3 of 10 real plain `net/http` samples never produce an inventory (measured):

| Sample | Call | Result |
|---|---|---|
| `s5` `net/http/pprof` | `http.HandleFunc(prefix+"/debug/pprof/", Index)` | `RouteNotUnderstoodError`, exit 3 |
| `s6` `x/net/trace` | `http.HandleFunc(debugRequestsPath, Traces)` | same |
| `s11` aws presign | `http.HandleFunc(presignPath, …)`, a local const | same |

`args` holds positions, so no pattern can reach the value, and ARDE doesn't edit the repo. This is loud, which is correct, but for these repos "no config" means "not at all". The compiler already knows each value: `types.Info.Types[expr].Value`, from the same load `fingerprints.go:130` does. A true variable, such as `route.pattern` in a loop (`p3`), should stay a stop.

### I2. An `(inline)` route has nowhere to put its annotation, and most real routes are inline

39 of 49 routes in the plain samples are `(inline)` (measured): `s2` 19 of 19, `s3` 14 of 20, `s7` 3 of 4. Only 10 of the 49 keys get a fingerprint. Factories such as `mux.HandleFunc("/x", s.handleX())` are the usual Go style, and `http.HandlerFunc(a)` goes inline too (`p7`).

`docs/limitations.md:57` says the cost is a missing fingerprint. The larger cost is Task 14. The renderer places a block above a function (`plan:771`), the spike anchors on `func (h *handlers) product(`, and `EndpointFact` names no handler. No plan or design text says where an inline route's block goes (searched).

The named case has a smaller version of the same problem. `Route.handler` holds the receiver *variable*, such as `rs.List`, and `s1` has two `List` methods. A union of both is safe for hashing, but a comment has to go on one function. `Route` also throws away the `file` and `line` that `CallMatch` holds.

### I3. `ANY` is the normal result on plain `net/http`, and it has nowhere to go

48 of those 49 routes read `ANY` (measured). In the plan, `ANY` appears only in the Task 8 amendment (`plan:91`). `RoutePattern` accepts it, Task 14's tests cover only `[get]`, and catalog and shop both register with a method, so no test or run has carried an `ANY` past the inventory.

OpenAPI has no wildcard operation, so the spec can't hold one. Swag isn't on this machine, so I couldn't read what it does with `[any]`. My memory is that it refuses the method.

`ANY` is also not always true: `s8` is `GET`, `PUT` and `DELETE` behind one `switch req.Method`.

### I4. The inventory holds items no fact can carry

Measured with the real models:

```
ErrorCode(code="Internal server error", ...)                   accepted
ResponseFact(error_codes=("Internal server error",))           ValidationError
ParamFact location: header, form, cookie                       ValidationError
ParamFact declared_type: bool, number                          ValidationError
```

`ErrorCode.code` is `str` (`inventory.py:21`), and `ResponseFact.error_codes` is `ErrorCodeName` (`facts.py:86`, `base.py:80`), which refuses spaces. `http.Error(w, "text", status)` is the plain `net/http` error call, and it gives exactly such a "code" (`s4`, `s8`). `test_missing_fact_for_inventory_item_rejected` (`plan:727`) needs a fact for every item, so that gap can never close. `ParamFact` has the same problem at `facts.py:72-73`: `p6` lists `X-Trace-Id`, which no fact can hold.

### I5. A fingerprint can start from the wrong function, because roots are looked up by bare name

R54 takes the last dotted part of the handler, and `declaredFunctionsByName` matches any function with that name. A *variable* with a function's name then hashes the function.

Measured on a `shop` copy: `list := traced(s.listing)`, `app.handle("GET /{$}", list)`, and an unrelated `func list()` in package `admin`.

| Edit | `GET /` moved? | `declarations` |
|---|---|---|
| The real handler, `server.listing` | **No** | names `func server.listing` |
| The unrelated `admin.list` | **Yes** | names `func list` |

`reach` for the root is `example.com/shop/admin.list` alone. Finding no function counts as changed, which is safe. Finding the wrong one is silent. It needs a name coincidence, and `declarations` plus `uncovered_changes` give a partial backstop, so this is Important and not Critical.

### I6. The design's backstops have no owner

- "Routes must answer live" (`design:209`) and "should get a 404 and stop the run" (`design:218`): `404` appears nowhere in the plan. `validate_profile`'s tests (`plan:680-688`) cover zero routes, regex, main file and port. Task 19 has no such test.
- `design:216` says `suspected_gaps` triggers a profile re-check. Its only test is in Task 27 (`plan:1319`), the later-run pipeline, so the first run ignores it.

## Minor

- **M1. `args` is not checked for extras or range.** `s10` accepts `"method": 0` and ignores it, giving `ANY /link` for `r.MethodFunc("LINK", …)`. `p7` with `handler: 9` makes every route `(inline)`, with `dropped` empty. `profile.py:26` checks only for missing keys.
- **M2. `dropped` is never empty on a healthy repo that wraps `os.Getenv`.** `DEFAULT_ENV_PATTERN` matches the helper's own body: `cmd/server/main.go:59` in catalog and `settings.go:7` in shop, on every run, with a correct profile. Task 17 will trace these as warnings, which teaches readers to ignore them.
- **M3. `response_fields` means "any struct with a json tag".** In `p6` it lists a request body. In `s4` all 30 fields are structs written to a settings file (`settings.go:54`), none a reply. With a fact required per item, that is paid work on fields no client sees. R32 ruled this for now, and the coverage rule is what gives it a cost.

## Job 1: which real shapes can't a profile express?

Evidence is measured unless marked.

| Shape | Profile can express it? | Result | Loud or silent | In `dropped`? |
|---|---|---|---|---|
| Group with a prefix: `r.Route`, `r.Mount` (`s1`) | No | Wrong paths, rows merge | **Silent** | No |
| Group with no prefix: `r.Group` (`s9`) | Yes | Correct, 5 of 5 | Fine | n/a |
| `http.StripPrefix`, nested mux (`s4`, `p1`) | No | Wrong paths, stray rows | **Silent** | No |
| Wrapper that adds a prefix or method (`p9`) | Partly | Wrong path and method | **Silent** | No |
| Loop calling `mux.HandleFunc(route.pattern, …)` (`p3`) | No | Stops, no way past | Loud | No, it raises |
| Table behind one handler (`s4`, `s8`) | No | Routes never seen | **Silent** | No |
| Path in a const or a sum (`s5`, `s6`, `s11`, `p4`) | No | Stops, no way past | Loud | No, it raises |
| Either stop, after narrowing the pattern (`p4`) | n/a | Routes vanish | **Silent** | No |
| `http.Handle` with a wrapped handler (`s2`, `s3`, `p7`) | Path yes, handler no | Path right, `(inline)`, no fingerprint, no anchor | **Silent** loss | No |
| Mux passed into a helper (`p2`) | Only by naming every receiver | Others missed | **Silent**. A receiver-free regex is loud | No |
| Method in the pattern: `"GET /x"` | Yes | Correct | Fine | n/a |
| Method in the call name: `r.Get`, `app.get` (`s1`, shop) | Yes | Correct | Fine | n/a |
| Method as an argument: `r.MethodFunc("LINK", …)` (`s10`) | No, the key is ignored | `ANY` | **Silent** | No |
| Method chained after: `.Methods("GET")` (`p5`) | No | `ANY` | **Silent** | No |
| Method inside the handler: `switch r.Method` (`s8`) | No | `ANY` | **Silent** | No |
| Param helper with a literal at the call (`p6`) | Yes, one pattern per helper | Correct | Fine | n/a |
| Param helper, pattern on its body (`p6`) | Partly | Name lost | Loud | **Yes**, one line, no name |
| Param names from a table (`s4`: a 27-entry table, and the inventory holds only `f` and `config`) | No | Missing | Mostly silent | One line, only if a pattern hits that call |
| Param without a call: `Query()["page"]` (`p6`) | No | Missing | **Silent** | No |
| Request body fields (`p6`) | No kind for it | Missing. The struct is listed as a response | **Silent** | No |

Only the `param`, `error` and `env` kinds can reach `dropped`, and only for calls the current regex matches. **No route failure ever appears there.**

Other real samples: `s3` gopls, 20 of 20 paths, though 3 sit behind an `if` and read as unconditional. `s5b` expvar lists both arms of an `if`, `ANY` and `GET /debug/vars`, and only one exists at runtime. `s7` present, 4 of 4 in its folder. `s12`, 1 of 1, inline. `s10`, 4 of 4 paths, 2 methods wrong.

## Job 2: done criteria 3 to 5

All pass, on `cp` copies, through a real `GoAdapter`.

- **Item 3, catalog:** 3 routes, 3 params (`currency`, `id`, `qty`), 6 error calls with 5 distinct codes. The spec's own `stdlib.json` gives the same, and grep agrees (6 `writeError(w,`, 3 `mux.HandleFunc(`). The copy is identical to the source.
- **Item 3, shop:** with only a different profile, 2 routes, 2 params, 2 errors, env, make targets and 5 response structs.
- **Item 4:** 19 of 19 cases. 8 comment edits changed no hash, no route and answered `is_comment_only` True. 11 one-token edits each named exactly the one right declaration.
- **Item 5:** 21 of 21 cases. `Convert` moves `GET /` and `GET /products/{id}`, not `healthz`. `writeJSON` moves all three. Unreached functions move nothing.
- **Controls:** three runs and a different folder gave identical hashes.

Item 3's "the same code works on shop" is true, and it says less than it seems to: both repos use literal paths, explicit methods and no mounts, which is the set of shapes the code handles.

## Job 3: Linux portability

Read, not run, except where marked. Nothing here is a product bug on Linux so far. The risks are in tests and in Task 28 wiring.

**Tests that fail as root:** none. Three skip, by `skipif(os.geteuid() == 0)`, so they lose their coverage under root:
- `tests/adapters/go/test_comment_only.py::test_a_file_that_cannot_be_read_is_not_comment_only`
- `tests/adapters/go/test_comment_only.py::test_a_file_in_a_folder_that_cannot_be_entered_is_not_comment_only`
- `tests/adapters/test_service_runner.py::test_a_log_the_service_makes_unreadable_cannot_replace_the_error_about_the_service`

There are no other `chmod` denials in the tests, and no `Chmod` in the Go tests (grep).

**Tests that fail in a container with no process reaper (5 cases):** this comes from PID 1, not from root. `is_running` uses `os.kill(pid, 0)`, which succeeds on a zombie (measured here: `True` before reaping, `False` after). macOS and a normal Linux host reap orphans. A container whose PID 1 is `pytest`, `uv` or `tail` does not (inferred), so `wait_until_gone` returns False:
- `test_child_process.py::test_a_timeout_also_stops_what_the_child_started`
- `test_child_process.py::test_a_process_the_child_left_running_in_its_group_is_stopped_when_the_child_exits`
- `test_service_runner.py::test_nothing_in_the_process_group_is_left_running` (both params)
- `test_service_runner.py::test_a_service_that_dies_leaving_a_child_is_reported_and_the_child_is_stopped`

`live_group_members` already ignores zombies (`process_support.py:33`), and `is_running` should too. The two fork-race tests would also pile up zombies there, up to thousands per round.

| Item | On Linux | Effect | Status |
|---|---|---|---|
| `ps -axo pid=,pgid=,stat=` (`process_support.py:36`) | From memory, the procps man page says `-o a=X,b=Y` varies by personality, and may give one column. `-slim` images have no `ps` | The 2 fork-race tests would fail in `line.split` or on a missing binary | Inferred. Fix: `ps -A -o pid= -o pgid= -o stat=` |
| SIGTERM as PID 1 | The kernel drops a signal PID 1 has no handler for, so Python ignores SIGTERM until Task 17's handlers exist | `docker stop` waits, then kills. If `uv run` is PID 1, its forwarding decides | From memory. Needs a real test |
| `XDG_CONFIG_HOME` | Used on Linux, inert on macOS (`os/file.go`; measured: with it set, `go env GOENV` still printed `~/Library/Application Support/go/env`) | Passing it is right, but it has never had any effect in any run. Real-Go tests name the file through `GOENV` (`test_go_environment.py:351`), and line 255 uses a stand-in | Test gap |
| `XDG_CACHE_HOME` | `go` uses it on Linux, and ARDE's list drops it (`child_env.py:10`) | Native Linux gets a different `GOCACHE`, a cold rebuild. Loud if `HOME` is also unset | Task 28 sets `GOCACHE`, so native only |
| `go_adapter` always runs `go build` (`adapter.py:84`, `goinspect_client.py:85`) | Plan says "prebuilt `goinspect`" (`plan:1346`), and the spike's image deletes `/go/pkg` | Measured with an empty module cache and `GOPROXY=off`: `GoinspectError`, blaming "Check that Go is installed". The first run needs the network, and `tools/` in the image | **Task 28 must change** |
| `GOINSPECT_SOURCE = parents[5]` (`goinspect_client.py:21`) | Correct only for a source tree. In a non-editable install it points into `lib/python3.12` | Build fails | Inferred |
| `/tmp` mounted `noexec` | From memory, Docker's `--tmpfs` defaults to it | `build_service`, `go test` and every `stand_in` test fail with `EACCES`. Loud | Task 28 test |
| Probe reads `$HOME/.config/go/env` | `HOME=/data`, the volume repo code can write | Repo A's test could set `GOPRIVATE=*` for repo B's checksums. Part of the known `/data` risk, but this path isn't listed | Task 28: set `GOENV=off` for the probe in the image |
| `lsof` in next actions (`service_runner.py:192`, `:298`) | Probably absent from `golang:1.27-bookworm` (from memory) | The next action can't be followed. Tests assert the word (`test_service_runner.py:721`, `:755`) | Install it, or reword both |
| `HTTPS_PROXY`, `NO_PROXY`, `SSL_CERT_FILE` | Not on either allow-list | Module downloads fail behind a proxy under ARDE while working by hand | Minor |
| `killpg` errors (`child_process.py:152`, `:163`) | macOS gives `EPERM` for zombie-only groups, Linux gives success or `ESRCH`. All handled | None | Fine |
| Double kill (`child_process.py:156`) | The miss is a macOS note. The second kill is harmless | None | Fine |
| `O_NOFOLLOW`, FIFO, folder as log | `ELOOP`, `ENXIO`, `EISDIR`. Tests assert the path, not the message | None | Fine |
| `/dev/fd` (`test_service_runner.py:964`) | Links to `/proc/self/fd`; before and after are compared on one platform | None | Fine |
| `ru_maxrss` units (`test_adapter.py:307`) | Handled by `sys.platform` | None | Fine |
| `#!/bin/sh` stand-ins | dash on Debian. Only POSIX is used | None | Fine |
| `walk.go` | A non-UTF-8 file name, possible only on Linux, reaches JSON as U+FFFD, so the reported path doesn't exist | Rare | Minor |
| Self-connect probe | From memory, a loopback connect to a closed ephemeral port can connect to itself. Linux's odd and even port split makes it unlikely | `_accepts_connection` false positive | Low, unverified |

## What held

- **No crash on real trees.** `match` and `declarations` ran over chi, gorilla, x/net, x/tools, gopls, delve, pgx, aws s3, wazero and all of `GOROOT/src`. The largest was `GOROOT/src`: 0.56 s and 8.3 s, 28 MB, against limits of 120 s and 64 MB.
- **Bad input stays loud.** My own malformed patterns file was refused with exit 1 and a clear message.
- **Linux cross-builds (measured).** `go build`, `go vet` and `go test -c` pass for `linux/amd64` and `linux/arm64`.
- **Fingerprints agree across platforms (measured).** Catalog and shop hash the same on darwin/arm64, linux/amd64 and linux/arm64. Control: a function that differs per OS moved `listing` and not `quote`, so `GOOS` did reach `go list`.
- **Portable tests.** No arch-specific Go test and no golden hash.
- **An unreadable `.git` is fine, and I expected otherwise.** The control shows the trap is real: plain `go build` and a hand-run `go list -export` both fail there. `route_fingerprints`, `build_service` and `run_checks` all pass, because `golist.go:885` passes `-buildvcs=false`.
- **Cold cache.** `fingerprints` took 11 s with an empty `GOCACHE`, 0.4 s warm.
- **Case sensitivity.** APFS here is case-insensitive, and all 91 real doc paths match exactly. `make_targets_of` tries all three makefile names. I suspected a bug here and there isn't one.
- **Middleware.** Edits to `recoverPanics` and `logRequests` move no route, and `declarations` names them, as `design:252` intends.
- **`dropped` does its job for what it covers.** `s2` listed all 7 `http.Error` calls whose message is computed.

## Plan changes

**Needs Ionel's decision first:**
- `[NEEDS CLARIFICATION: mounts and routers. Refuse them (cheap, and honest given limitations.md:32), or resolve prefixes (real work)? This review recommends refusing.]`
- `[NEEDS CLARIFICATION: ANY. Must Explore split it into real verbs with probes, or does ARDE stop on it?]`
- `[NEEDS CLARIFICATION: inline routes. Swag reads comments on declarations (from memory), so where does the block go? The promise "rendered output adds only comment lines" may not allow an answer. If not, inline routes are out of scope and must stop loudly.]`

**Phase 1 fix round (Task 8):**
1. Reword `USE_A_LITERAL` (`inventory_builder.py:39-42`) so it can't be followed by narrowing: "ARDE cannot read this route. Do not narrow the pattern to skip it, because a route left out is worse than a stopped run. If the path is computed or comes from a table, this repo can't be documented yet: report that."
2. Add a `goinspect registrations DIR` command on the existing `loadPackages`. For each call resolving through `types.Info` to `net/http.Handle`, `net/http.HandleFunc` or `(*net/http.ServeMux).Handle`/`.HandleFunc`, print file, line, the path's constant value from `types.Info.Types[arg].Value`, and the handler's resolved function. Then `extract_inventory`:
   - uses the constant when no literal exists (I1);
   - stops if a site is neither matched by a route pattern nor inside a wrapper the profile matches (C2);
   - passes the resolved function to `route_fingerprints`, so a variable is unresolved and not wrong (I5).
   `Route` gains `file` and `line` in the same change (I2).
3. Keep a match only if its package is in `go list -deps <main_package>` (C3).
4. Raise on `http.StripPrefix(` in a matched route's handler text, and on a known router in `go.mod` (C1, if refusing is chosen).
5. Refuse an unknown key in `ExtractionPattern.args`, and an out-of-range `handler` position (M1).
6. Make `is_running` ignore zombies, and use `ps -A -o pid= -o pgid= -o stat=`.

Deviation: a fifth `goinspect` command, against the spec's "four commands". The reason is C2, and the cost is that `extract_inventory` then needs a repo that compiles. Task 17 already runs baseline checks first (`plan:916`).

**Task 12:** add these tests:
- `test_validate_profile_flags_a_route_that_answers_like_a_missing_path`: request each route and one path that can't exist, and flag any route that answers the same. This owns `design:209`.
- `test_unreadable_route_cannot_be_passed_by_narrowing_the_pattern`.
- `test_invalid_regex_is_judged_by_go_regexp`: Python's `re` accepts a lookahead that RE2 refuses.

**Task 13:** an `error` match whose code fails `ErrorCodeName` goes to `dropped` and not into `error_codes`, so `http.Error` text isn't a code. Add `header` and `form` to `ParamFact.location`, and `bool` and `number` to `declared_type`, or have the inventory leave those out. Limit coverage to structs a route replies with, or accept the cost in writing (M3).

**Task 14:** add `test_any_route_renders_real_verbs` and `test_inline_route_is_placed_or_stops`, once decided. Find a handler through `Route.file` and `Route.line`, never by bare name.

**Task 17:** consume `suspected_gaps` on the first run too, and add `test_first_run_suspected_gap_rechecks_profile`.

**Task 19:** say which verbs an `ANY` route is requested with.

**Task 28:**
- Build `goinspect` in the image and pass it to `GoAdapter`. Add `test_runs_offline_on_an_empty_volume`, because `go_adapter` always builds today.
- Set `init: true` in compose.
- Add `test_docker_stop_stops_a_running_service`.
- Add `test_make_check_and_make_integration_pass_in_the_image`, as non-root and as root.
- Add a real-Go test of `XDG_CONFIG_HOME` with no `GOENV`.
- Add `test_build_service_runs_from_tmpdir`, and assert `ps` and `lsof` exist.
- Set `GOENV=off` for the probe in the image.

**`docs/limitations.md`:**
- Line 34: name `http.StripPrefix`, and drop "a live check should get a 404" until a task owns it.
- Line 57: say `(inline)` also has no anchor, once decided.
- Line 97: replace "a claim, not a result" with the result: 3 of 10 stop, and the rest vary as above.
- Add: routes behind a table or a `ServeHTTP` switch are invisible to patterns.

## Verdict

Phase 1 meets its done criteria 3 to 5 on `catalog-poc` and `shop`, but "works on another Go `net/http` service with no config" is not true yet: real code either stops with no way past or gets a wrong route list without a word, so fix C1 to C3 before Phase 2 builds on the inventory.

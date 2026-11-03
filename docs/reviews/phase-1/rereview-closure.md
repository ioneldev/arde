# Phase 1 re-review: closure

I ran everything on `git archive 5298e7b`, in `/private/tmp/claude-501/-Users-ionel-work-personal-emag/0f37fc47-1c0a-42ed-bd77-f0a6f96a2ead/scratchpad/p1r/closure`. `arde` was confirmed loaded from the export, on Go 1.27.1 with `GOPROXY=off`. The probe repos are in `repos/`, the scripts in `probes/`, and the outputs in `probe.out`, `cap-*.json` and `*.txt`. The checkout, `.env` and `catalog-poc` were left untouched. I used a `cp -R` copy of `catalog-poc` with its `.git` removed. Its `.git` mtime is still Nov 2 20:01:59.

## New findings

None is Critical or Important.

1. **Minor: a hand-rolled service runner is back in the tests.** `tests/adapters/go/test_service_scope.py:58-92` holds `free_port`, `wait_for` and `serving`, with 3 callers at :187, :216 and :281. Group B added it in `ff950ed`, and it is the shape code review 13 removed from `test_adapter.py`. It also starts the binary with `{**os.environ}` (:81) instead of the allow-list. Fix: use `local_service_runner()`.
2. **Minor: the const twin of R104's var mutant survives.** I changed `declarations.go:179` from `pos: name.Pos()` to `valueSpec.Pos()` and the whole Go suite passed (`ok goinspect 34.1s`). Add `TestEachConstNameKeepsItsOwnLine`.
3. **Minor: the plan carries the rules but not the tests code review asked for.**
   - Task 12 has no test that a bad `port_env` is refused at submit.
   - Task 12 has no test that the regex correction names the model's own pattern number.
   - Task 12 has no test that a repo file that doesn't parse stops the run and never reaches the model.
   - Task 25's `facts_to_delete(..., removed_inventory: Sequence[str])` doesn't say `Route.key`.
   - Plan line 187 still reads `port_env: str`.
4. **Note, settled by R94 and not reopened: ARDE's own next action can lead to a silently short path.** In `repos/group` the wrapper keeps its prefix in a field.
   - With no pattern, the run stops at `router/router.go:16`, which is right.
   - Following `READ_A_CONSTANT_PATH` and adding `^r\.Get$` gives `['GET /{id}']` for the real `/users/{id}`, with `dropped` empty.
   - `limitations.md:45` and the Task 12 live check cover this case, so that test should use this shape.
5. **Minor: one glossary line is still incomplete.** The `dropped` entry at `glossary.md:13` leaves out "outside main_package" and "a file this build leaves out".

One false alarm of my own: one Go test failed only under the `GOFLAGS=-mod=readonly` I had set, because it overrides vendor mode. It passes with the default environment.

## Fix-report figures I re-ran

| Claim | Re-run result |
|---|---|
| Gates (D2: 1930 / 241 / 209) | ruff, format and mypy clean. `make check` 1930 passed with 249 deselected. Integration 241 passed with 1938 deselected. Go 209 top-level tests, all pass. `gofmt -l` empty, vet clean. |
| D: 0 differences against base on shop and the `catalog-poc` copy | 0987d90 against 5298e7b, comparing inventory, route hashes, declaration hashes and `run_checks`: 0 differences on shop, the `catalog-poc` copy with `stdlib.json`, parcels and library. A control with 2 injected changes found exactly 2. |
| D2 REDs | Dropping `testdata`: "Extra items… 'testdata/x'". Var mutant: `got map[first:4 second:4]`. Field mutant: `got map[High:5 Low:5]`. All three match the report. |
| D2: no function past two positional arguments | My AST scan found 0. |
| B: cold `routes` on shop, 4.3 s and 296 MB | 4.38 s and 311 MB cold, 0.18 s warm. |
| C: 300,000 lines make a 53.5 MB stream, peak 359 MB | 55,767,847 bytes (53.2 MiB), and `run_checks` peaked at 358 MB. |

Not re-run: the mutant totals (B 41, C 89, D 30), the load runs, the Task 9 timing figures, and Group A, which belongs to the security reviewer.

## Closure table

"probe" means I re-ran the review's input on 5298e7b. Plan lines are from `docs/superpowers/plans/2026-11-01-arde.md`.

| Review | Id | Status | Evidence |
|---|---|---|---|
| arch | 1 | closed + moved | `inventory.py:52-54` `Route.key`, `:88` declarations (25 and 58 ids measured). `validate_batch` at plan:106 |
| arch | 2 | closed | `ports.py:127`. `inventory_builder.py:98,107`. `errors.py:19,27`. Probe: bad regex gives `BadPatternError` exit 3, a broken file gives `GoinspectError` exit 1 |
| arch | 3 | closed + moved | `profile.py:49`. Probe: `"APP PORT"` refused. `merge_override` and `save_profile` at plan:105 |
| arch | 4 | moved | plan:105 (reject first submit), :112 (warnings) |
| arch | 5 | closed by ruling | R83. plan:107, :109, :113 |
| arch | 6 | moved | plan:109 |
| arch | 7 | closed | `adapter.py:118-120`, `toolchain.py:83-89`, `ports.py:235` |
| arch | 8 | closed + moved | `inventory.py:77` `package`. plan:111 |
| arch | Minor: Owner, PipelineDeps | moved | plan:105 |
| arch | Minor: Task 28 prebuilt | moved | plan:116 |
| arch | Responsibilities | closed | `ports.py:196`, `inventory.py:73`, `test_architecture.py:37,45-47` |
| arch | Size | closed in part | `declarations.go:156-283` plain functions. `service_runner.py:274` two names. `walk.go` not taken under fix-list D4, which names no ruling. Log open kept by R77 |
| code | 1 | closed | As arch 3 |
| code | 2 | closed | Probe: "pattern 1 (kind "route")", exit 3. Default pattern last, `adapter.py:31-32` |
| code | 3 | closed | `declarations.go:299-305` uses reflect. Probe: `café`, `a"b`. `_TAG_ENTRY` is gone |
| code | 4 | closed | `inventory.py:52`, `adapter.py:147` |
| code | 5 | closed + moved | `inventory.py:88`. plan:106 |
| code | 6 | closed | `errors.py:15` |
| code | 7 | closed | Probe with `cwd=/no/such/folder`: the next action names the program and the folder |
| code | 8 | closed | `normalise.go:42,96`, `fingerprints.go:316,378` |
| code | 9 | closed | `goinspect_client.py:39-134` wire names, `callee` :77, `fingerprints(repo, roots)` :218 |
| code | 10 | closed by ruling | R102, R106. `test_checks_measured.py:396`. RED re-run |
| code | 11 | closed | `go_environment.py:23-32`, `timeouts.py` EXTRA = GO_TEST, `child_env.py:14` |
| code | 12 | closed except one line | 8 rows read true. `glossary.md:13` (new 5) |
| code | 13 | open in part | Old helpers gone, new runner added (new 1) |
| code | 14 | closed | `test_go_environment.py:88-91` |
| code | 15 | closed | `walk.go:112`, `deviations.md:15` |
| code | Plan changes | moved in part | plan:86, :92, :106. Task 12 tests and Task 25 key missing (new 3) |
| silent | C1 | closed | Probe `skips`: `tests_run` 0, 3 skipped, `tests_listed` 3. `toolchain.py:177`. plan:112 |
| silent | C2 | closed | Probe `struct1`: editing `discount` moves `GET /items`. `wrongroot` and `fieldroot` give `no function` |
| silent | C3 | closed | Probe parcels: 2 "outside main_package" lines. `service.go:85-97` |
| silent | I1 | closed + R94 | Probe `inv`: unmatched `error ^writeError$`, `GET /receipts` and `API_TOKEN` found. A partial match stays silent, `limitations.md:71` |
| silent | I2 | closed | Probe `linked`: `POST /hidden` and `routes.go` ids listed. `walk.go:250-258` |
| silent | I3 | closed | Probe `handler: 5`: exit 3. `inventory_builder.py:324-330` |
| silent | I4 | closed + moved | `ports.py:175-181`. plan:105, :112, :115 |
| silent | I5 | closed | Probe `broken_mod`: all false, `tests_listed` None. `toolchain.py:233-239` |
| silent | I6 | closed | Probe `nested`: `('billing',)`. `limitations.md:86` |
| silent | M1 | closed | Probe `empty_with_mod`: `build_ok` false. `toolchain.py:46,170` |
| silent | M2 | closed | `-count=1` at `toolchain.py:177` |
| silent | M3 | closed | `child_process.py:66-70,108`, 540 s |
| silent | M4 | closed | `ports.py:165` |
| silent | M5 | closed | Probe: mode-000 Makefile gives `MakefileNotReadableError` exit 2. `goinspect_client.py:169-176` |
| silent | M6 | closed | `child_process.py:96`, `goinspect_client.py:251-269` |
| silent | M7 | closed | Probe: `\xe9` and `\xe8` stop with exit 3, not merged. `match.go:282`, `routes.go:336` |
| silent | M8 | closed | `service_runner.py:182` catches `ConnectionError` and `TimeoutError` (controller's `231c6fd`). Other `OSError` raises at :190, :327 |
| silent | M9, M10 | closed | `limitations.md:49`, `:67`, `:79` |
| silent | Plan changes | moved | plan:83, :97, :105, :112, :115 |
| bugs | C1 | closed by ruling | R82, R96. Probe `strip`: stop at `main.go:14`. Probe `fileserver`: a route with no id. Router groups R94, `limitations.md:45`. The go.mod known-router stop wasn't taken and no ruling covers it |
| bugs | C2 | closed | Probe `p4` with the narrowed pattern: 3 of 3. `routes.go:24-29`. `inventory_builder.py:63-71` |
| bugs | C3 | closed | As silent C3 |
| bugs | I1 | closed | Probe `p4`: a const sum is read. `routes.go:324-337`. The 3 of 10 is not re-measured, R95, `limitations.md:126` |
| bugs | I2 | closed by ruling | R84. `inventory.py:44-50`. plan:110 |
| bugs | I3 | closed by ruling | R83. plan:107, :113 |
| bugs | I4 | closed + moved | `inventory.py:32` `CodeText`. plan:108 |
| bugs | I5 | closed | As silent C2. `route_handlers.go:85-100` |
| bugs | I6 | moved | plan:105 (404 test), :112 (`suspected_gaps`) |
| bugs | M1 | closed | Probe: a `method` key is refused. `profile.py:33-38` |
| bugs | M2 | closed | Capture: shop `dropped` is `[]` |
| bugs | M3 | moved | plan:108 |
| bugs | Job 3: zombies, `ps` | closed | `tests/process_support.py:12-24,41-46` |
| bugs | Job 3: Task 28 rows | moved | plan:116 |
| bugs | Job 3: proxy variables, non-UTF-8 file names on Linux | open (Minor) | No limitations line and no plan line |
| bugs | Plan and limitations changes | moved or closed | plan:105, :109-110, :112, :113, :116. `limitations.md:44-45`, `:77`, `:126` |
| t9 | New 1 | closed | `limitations.md:19` |
| t9 | New 2 | closed | `service_runner.py:209`, `:313` |
| t9 | New 3 | closed | `limitations.md:23` |

**Verdict:** the fixes close every in-scope finding I can check, by code or by ruling. What's left is the plan-text and test gaps below. All are Minor and none blocks Phase 2.

## Plan and test changes

- **Task 12, Step 1:** add three tests.
  - `test_submit_profile_rejects_a_port_env_that_is_not_an_identifier`
  - `test_invalid_regex_correction_names_the_models_own_pattern_number`
  - `test_repo_file_that_does_not_parse_stops_and_is_not_sent_to_the_model`
- **Task 12, the live check:** make `test_validate_profile_flags_a_route_that_answers_like_a_missing_path` include a wrapper that keeps its prefix in a field.
- **Task 25:** change the signature to `facts_to_delete(wiki, removed_routes: Sequence[str])`, documented as `Route.key` values, and add `test_removed_route_is_matched_by_route_key`.
- **Plan line 187:** `port_env: CodeIdentifier`.
- **Task 28:** add a test or a limitations line for `HTTPS_PROXY`, `NO_PROXY` and `SSL_CERT_FILE`, and for non-UTF-8 file names on Linux.
- **Tests now, not in the plan:**
  - Swap the runner in `test_service_scope.py` for `local_service_runner()`.
  - Add the const-line test.
  - Complete `glossary.md:13`.

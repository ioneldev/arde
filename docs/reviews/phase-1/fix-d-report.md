# Phase 1 fix report: Group D, tidy-up

**Status: DONE_WITH_CONCERNS.** Every item has an outcome below, on branch `phase-1`, base `0987d90`, not pushed. Code review 10 is stopped, because its fix rests on a premise I measured to be false, and it needs a ruling. The rest is done or was already done.

## Commits

The gates ran on each commit's exact tree, matched by tree hash, with no unstaged or untracked file. `make check` and `make integration` are the real targets, run in the repo. Go is `go test -count=1 -v ./...` on the same tree, counted as top-level `=== RUN` lines, which is how Group C counted.

| Commit | Item | `make check` | `make integration` | Go |
|---|---|---|---|---|
| base `0987d90` | | 1925 | 240 | 206 |
| `92695a8` | Code review 6, one Go error base | 1925 | 240 | 206 |
| `9483429` | Code review 8, hash and fingerprint | 1925 | 240 | 206 |
| `7b5ec8f` | Code review 9, `callee` | 1925 | 240 | 206 |
| `b44458c` | Code review 9, registered path, `repo` beside `roots` | 1925 | 240 | 206 |
| `db41134` | Code review 9, wire types take Go's names | 1925 | 240 | 206 |
| `5afa5a4` | Code review 11, each constant once | 1925 | 240 | 206 |
| `15cca81` | Code review 14, the `go env` cache | 1925 | 240 | 206 |
| `5bced10` | Code review 13, `Edit.written_as_files`, one `shop_support` | 1925 | 240 | 206 |
| `6d517b6` | Code review 13, the real runner | 1925 | 240 | 206 |
| `bcbf4f8` | Code review 13, one known environment, two test lists renamed or imported | 1925 | 240 | 206 |
| `0e1fa34` | Code review 12, stale comments and docs | 1925 | 240 | 206 |
| `6059b79` | Code review 15, Go | 1925 | 240 | 206 |
| `67a46bc` | Code review 15, the Python helpers it names | 1925 | 240 | 206 |
| `8cb17b8` | Item 3, relative directory | 1925 | 240 | 207 |
| `e8caa98` | Item 3, zombies and `ps` | 1928 | 240 | 207 |
| `b1412a4` | Item 4, plain functions in `declarations.go` | 1928 | 240 | 207 |
| `5b6dfe6` | Item 4, the problem list | 1928 | 240 | 207 |
| `991c9e2` | Item 2, New 2 | 1930 | 240 | 207 |
| `8045e4a` | Items 2 and 5, `limitations.md`, with New 1 and New 3 | 1930 | 240 | 207 |
| `c1f4647` | A test the mutants asked for | 1930 | 240 | 207 |

Every gate exited 0 on all three. Logs: `gate-*.txt`, `check-*.txt`, `int-*.txt` and `go-*.txt` in `/private/tmp/claude-501/-Users-ionel-work-personal-emag/0f37fc47-1c0a-42ed-bd77-f0a6f96a2ead/scratchpad/fixD`.

**Figures against their breakdown:**
- **`make check`:** 1925 + 3 (`e8caa98`, `tests/test_process_support.py`) + 2 (`991c9e2`) = 1930.
- **`make integration`:** 240 throughout. Eval stays at 8: check deselects 248 = 240 + 8, and integration deselects 1938 = 1930 + 8.
- **Go:** 206 + 1 (`8cb17b8`, one test with five subtests) = 207.
- **Refactors:** no refactor commit changed a count. `test_a_toolchain_variable_that_is_exported_reaches_a_command` keeps all 11 of its cases.
- **Diff `0987d90..c1f4647`:** 47 files, +705 −569. The commits sum to +709 −573, because four lines on each side were written by one commit and rewritten by a later one.

## Item outcomes

### Item 1: code review 6 to 15

| # | Outcome | Notes |
|---|---|---|
| 6 | Done | `GoinspectError(GoCommandError)` lives in `errors.py`, with a docstring only. The claim "no `except` names either one" had become false: Group C added `except GoCommandError` in `_ignored_files`. Its `try` runs `go list` alone, so it can't swallow a goinspect failure. The exit code is pinned at `test_goinspect_client.py:216` and `:241`. |
| 7 | Already done | Group C's `4d00447`. The next action names both the program and the folder (`child_process.py:96-97`). |
| 8 | Done | `hashOfDump`, `normalisedHash`, `constMeaning.hash`, `normalisedSyntaxHash`, `fingerprintOf`. Three test names in `bounds_test.go` followed. Every declaration hash is unchanged on both repos. |
| 9 | Done, in 3 commits | The wire key is `callee` on both sides, and the patterns file keeps `call`, because that one is the regex. `REGISTER_A_PLAIN_PATH` and `_method_and_path` call the text a registered path. Every client method takes `repo`. The wire types take Go's names: `CompareReport`, `Declaration`, `StructField`, `DeclarationReport`, `MatchReport`, `RouteReport`, `FingerprintReport`, `PatternName`. |
| 10 | **Stopped: the fix is wrong** | See below. |
| 11 | Done | The review's test advice needed one more change it didn't name. `clean_environment` builds its delete-list from the same constant, so without `*USER_SETTINGS` an exported `GOPRIVATE` would leak into `test_nothing_is_invented...`. |
| 12 | Done | All 8 rows were checked, and all were still untrue or half-true. In row 5, Groups A to C had added "could not be read / outside the service", but not "missing" or "empty". `directives.py`'s comment held the glossary's untruth too, so it's fixed as well. |
| 13 | Done, in 3 commits | `test_child_env`'s list stays typed by hand, renamed `GO_VARIABLES`: a test of what must not reach a child shouldn't read the list it guards, and it holds names the product list leaves out. The real runner test still fails with a wrong port variable (`ServiceStartError`). |
| 14 | Done | RED: a later test saw `corp.example/*` for all three settings, and the probe failed (1 failed, 54 passed). GREEN: it sees `None` (55 passed). |
| 15 | Done, in 2 commits | `fingerprints_test.go:56` was already fixed by Group B. `argumentAt`, `routesOf` and `editFile` have the same shape, so they follow. `run` and `parseWithoutComments` are recorded in `deviations.md`. A Go AST scan now finds only those two. |

#### Code review 10 (stopped)

The fix says to add `folders` to the `declarations` reply and delete the Python walk. I measured its premise first:
- **`declarations` refuses a broken repo.** It exits 1 on a file that doesn't parse (`cannot parse oops.go...`). `run_checks` must give a red result on that repo, and on the base it does: `False False False`. Taking folders from that reply would turn the red result back into an error.
- **`toolchain.run_checks` has no goinspect client,** and about 60 tests in `test_toolchain.py` drive it with a stand-in `go`.
- **The two walks already disagree,** though nothing visible depends on it. Python lists a folder holding only `_x.go` or only a broken link, and Go skips both. Measured: this has no effect on `ignored_files`, and a broken link turns the build red anyway.

`[NEEDS CLARIFICATION: (a) a sixth goinspect command, folders DIR, with no parsing, which is a deviation and touches about 60 stand-in tests; (b) keep both walks and add one integration test that drives both over every edge shape and asserts they agree; (c) leave it.]` I recommend (b).

### Item 2: Task 9 re-review New 1, 2, 3

- **New 1, as docs (`limitations.md:19`).** I re-measured on `991c9e2` with the re-review's own scripts:
  - `Popen` itself takes about 6.2 ms.
  - From `Popen` returning to the `try`: a median of 77 µs, at most 118.
  - A second interrupt between entering the stop and the first signal: a median of 27 µs, at most 41.
  - An interrupt at each point left the service running.

  The code is unchanged. Moving `poll()` would leave the died check without a place, and a `finally`'s entry is always unprotected.
- **New 2, done (`991c9e2`).** The messages now say "Something is holding port P". RED: the 2 new tests failed on the old text, while the test I refactored still passed. GREEN: 3 passed, then the runner file 3 times in a row, 62 passed each.
- **New 3, as docs (`:23`).** Re-measured with 60 runs each:

  | Case | Signals after the reap | Last signal, median | At most |
  |---|---|---|---|
  | `run_child` | 1 | 39 µs | 411 |
  | Normal stop | 2 | 50 µs | 81 |
  | Died in use | 3 | 184 µs | 596 |
  | Exited during start | 3 | 624 µs | 829 |

  The load average was about 9 on 12 cores, and the doc says so.

### Item 3: zombies, `ps`, and a relative directory

- **`is_running` (`e8caa98`).** It now asks `ps -o stat= -p PID`, counts state `Z` as gone, and fails on any stderr. `live_group_members` uses `ps -A -o pid= -o pgid= -o stat=`, which gave the same 737 rows as `-axo` here. RED: `is_running(43229)` returned True for an uncollected zombie. GREEN: 3 passed.
- **`resolveDirectory` (`8cb17b8`).** It now calls `filepath.Abs` first. My first RED passed for `fingerprints`, which contradicted my probe, and the cause was in the test: the handler sat in the root package, where a load with no folders falls back on the current one, right by accident. The test now uses a sub-package and refuses to pass when the absolute run didn't read the handler. That guard also caught `match` with no patterns.
  - Honest RED: `routes` gave an empty report, and `fingerprints` gave `"unresolved":["example.com/depot/desk.Parcels"]`, both exit 0. `compare`, `declarations` and `match` pass as guards.
  - GREEN: 5 of 5. So `fingerprints` was silent too, not only `routes`.

### Item 4: architecture size

- **`declarationCollector` and `fieldReader`** are plain functions plus methods on `parsedSource`. Net −9 lines, not the estimated 25. Every raw declaration and field reply is unchanged.
- **The problem list** is two names, +13 −15. A mutant showed the new "no note of itself" guard had no test, so `c1f4647` adds one, and that mutant now dies.
- **Not done, by ruling:** `walk.go`'s comment reading and the `service_runner.py` log open.

### Item 5: `docs/limitations.md`

| Report's line or topic | Outcome | Now |
|---|---|---|
| 34, `StripPrefix` | Already named. The 404 sentence names its owner instead: plan line 105, Task 12, not built | 45 |
| 53, `dropped` incomplete | Already said by Group B. "Route pattern" became "registered path" | 71 |
| 57, `(inline)` | Decided at plan line 110: no swag block, now said | 77 |
| 97, the result | 3 of 10, marked as before the fix and not re-measured, with the reason (R95). **Correction:** by reading their source, `x/net/trace` holds a const, which `routes` now reads, but `net/http/pprof` adds a variable prefix, which still stops the run. The aws sample was not checked | 126 |
| Tables and `ServeHTTP` switches | Added, measured on a fixture: one `ANY` route, no `dropped` line | 44 |
| Structs with no `json` tag | Added, measured | 67 |
| Nested modules in checks | Already there | 86 |
| Imports and `changed_declarations` | Added, measured: both edits give `[]`, and only `compare` sees them | 49 |
| The `go test` environment | Corrected, measured: an extra command helps only as `env VAR=... go test` | 85 |
| Symlinks | Already there. Added the broken-link Makefile case, measured | 11, 69, 79 |
| `go.mod` toolchain, `go.work` | Already there | 12, 14 |
| Line 7 to measured | Done, citing the security review, not re-run | 7 |
| The `go env` file in `/data` | Added, reasoned | 9 |

I read the whole file against the code. Groups A to C had left four lines untrue, and they're fixed:
- Line 20 said "now".
- Line 57 said "as before", and gave the 170× figure as fact, though it was worked out and not measured.
- Line 66's Task 13 had already been decided (by `package`).
- Line 122 said "As of Phase 0".

Other doc edits: `glossary.md`, `phase-1-reading-go.md:40`, `deviations.md`, and one sentence of `architecture.md`.

## Required at the end

### Comparison with the base

`capture_all.py` ran the real `GoAdapter` from an export of the base `0987d90` and of the final `8045e4a`. The `c1f4647` commit changes one test only. It ran on `shop` and on a `catalog-poc` copy under two profiles, and recorded the inventory, route hashes and reasons, `changed_declarations` after a one-token edit, `run_checks`, and the **raw reply of all five goinspect commands**. `compare_d.py` walks every field, undoing only the `call`→`callee` key and test timings.

| Repo and profile | Differences | Routes | Route hashes | Declaration hashes | Fields | Matches | Sites | `tests_run` |
|---|---|---|---|---|---|---|---|---|
| `shop` | **0** | 2 | 2 | 25 | 20 | 8 | 2 | 3 |
| `catalog-poc` | **0** | 3 | 3 | 58 | 57 | 13 | 3 | 8 |
| `catalog-poc` + env pattern | **0** | 3 | 3 | 58 | 57 | 14 | 3 | 8 |

Two controls show the comparison can see a difference. One flipped hash gives exactly 1. Without the rename undo it gives 86 = (8+2)×2 + (13+3)×2 + (14+3)×2. The original `catalog-poc` is untouched: `diff -rq` is clean, and its `.git` mtime is still Nov 2 20:01:59. No git ran in it.

### Load runs

`make check` ran 5 times in a row on `c1f4647`, with `make integration` looping alongside, and full logs are in `fixD/load/`. All 5 checks exited 0 with 1930 passed, and all 3 integration runs exited 0 with 240 passed. The load average was 6.5 to 8.8. That's no failure, but five runs are weak evidence against a one-in-18 failure: at that rate, five clean runs would still come up about 75% of the time.

### Mutants

30 mutants on an export of `8045e4a`, where a kill counts only when a named test fails: 25 killed, 5 survived.
- A `var`'s line, and a field's line, when names sit on several lines: test gaps that predate Group D.
- Re-raising the body's error: equivalent.
- `EXTRA_TEST_COMMAND_TIMEOUT_S = 1`: a constant's value, which no test pins.
- `argument()` reading position 0 for a missing name: unreachable through ARDE, because `REQUIRED_ARGUMENTS` refuses such a pattern.

The 30 include one first marked invalid, rerun in a form that compiles, and the self-note mutant `c1f4647` now kills.

## Additions beyond the list

1. `UnmatchedPattern` → `PatternName`.
2. The `fingerprints` relative-directory symptom.
3. `argumentAt`, `routesOf`, `editFile` and `a_route`.
4. Two reworded messages (`REGISTER_A_PLAIN_PATH`, goinspect's `args` error).
5. `directives.py`'s comment.
6. `c1f4647`.
7. The `limitations.md` lines 7, 9, 20, 57, 66 and 122, and the 3 of 10 correction.
8. One sentence of `architecture.md` and the `ServiceStopError` docstring.

## Plan lines now stale (not edited)

- **Plan line 446:** the `match` reply's key is now `callee`, and it carries `literals`.
- **`phase-1-fix-list.md` item 1** promises "one source for what ./... skips", which is stopped.

Nothing else in the plan or design names a renamed item (searched).

## Not verified

- Linux: procps `ps`, and the five container tests.
- The aws sample's const, and the 3 of 10 itself, which is the bugs review's measurement.
- Line 7's `KERN_PROCARGS2` result, cited and not re-run.
- Line 9's `/data` path, reasoned.
- The re-raise equivalence, reasoned.

## Process notes

Two of my scripts failed partway. One stopped on an assertion before writing anything. The other wrote some files, and I restored exactly those paths with `git restore`. Two gate attempts stopped on a line one character too long, and each was fixed and gated again on the exact tree. After that I added a quick ruff and mypy pre-check before every gate.

`.env` was never read. Nothing called the API. Only explicit paths were staged, nothing was pushed, no subagent was spawned, and the plan and design are untouched.

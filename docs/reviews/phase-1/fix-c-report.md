# Phase 1 fix report: Group C, honest checks and failure paths

**Status: DONE_WITH_CONCERNS.** Items 1 to 13 are done, on branch `phase-1`, base `708a59e`, not pushed. Nothing blocks Group D. The concerns are in [Concerns](#concerns), and one needs a ruling.

## Commits

| Commit | Items | `make check` | `make integration` | Go tests | Gated how |
|---|---|---|---|---|---|
| base `708a59e` | | 1774 | 202 | 192 | `make` |
| `60c4bfd` | 1, `run_checks` measures what ran | 1821 | 217 | 192 | `make`, exit 0 |
| `4b8397e` | 2, 3, red result, nothing built | 1830 | 221 | 192 | `make`, exit 0 |
| `b870a40` | 4, timeout tail | 1834 | 222 | 192 | `make`, exit 0 |
| `1e507e3` | 6, closed ports | 1841 | 222 | 192 | Clean export, **after** the commit: see concern 9 |
| `4d00447` | 7, next actions | 1846 | 222 | 192 | `make`, exit 0 |
| `25ed8be` | 8, `ProfileProblem`, R92 | 1878 | 228 | 198 | `make`, exit 0 |
| `38eba3e` | 9, `port_env` | 1886 | 228 | 198 | Staged tree in a clean export, before the commit |
| `36a0acf` | 5, 10, literals and tags | 1887 | 233 | 203 | Same |
| `45d65d6` | 11, `built_service` | 1887 | 237 | 203 | Same |
| `28596d9` | 13, file server under `StripPrefix` | 1887 | 240 | 206 | Same |
| `1df871d` | 12, Go idioms behind the port | 1917 | 240 | 206 | Same |
| `0e00e62` | Tests for what the mutants found, dead clause removed, docs on memory and forged events | 1921 | 240 | 206 | `make`, exit 0 |

**Figures against their breakdown:**

- `make check`: 47 + 9 + 4 + 7 + 5 + 32 + 8 + 1 + 0 + 0 + 30 + 4 = 147 new tests, and 1774 + 147 = 1921.
- `make integration`: 15 + 4 + 1 + 0 + 0 + 6 + 0 + 5 + 4 + 3 + 0 + 0 = 38 new tests, and 202 + 38 = 240.
- Eval tests stay at 8: 1921 + 240 + 8 = 2169, and pytest deselects 248 = 240 + 8 from `make check`. Each row's deselected count held the same way.
- Go: +6 at `25ed8be`, +5 at `36a0acf`, +3 at `28596d9`, so 14 new, and 192 + 14 = 206. Measured with `go test -v` at base, `25ed8be` and the final tree. The rows between are counted from the committed `_test.go` files, and both methods agree where both ran.
- Mutants: see [Mutation checks](#mutation-checks). 89 on the final code, 88 killed, 1 survivor.
- Diff: 47 files, +2797, -335. The commits sum to +2824, -362. The 27 lines of difference on each side were added in one commit and rewritten in a later one.
- `go test` read `(cached)` in the last `make integration`, because the source hadn't changed since the run before it. I ran `go test -count=1 ./...` on that tree as well: `ok goinspect 28.543s`.

## Required at the end

### `run_checks` on real Go

Each was measured through the real `GoAdapter`, base source against final source, by the same `probe_checks.py` and `probe_hang.py`. `arde.__file__` printed each time, to prove which tree ran.

| Repo | Before | After | True? |
|---|---|---|---|
| Skipped tests (`skips`: 3 tests that skip without `DATABASE_URL` and fail with it) | `test_ok: true, test_count: 3`, output `ok` | `test_ok: true, tests_run: 0, tests_skipped: 3 names, tests_listed: 3`, and each skip's reason in `output` | Yes. No test failed, none ran, and the reason says what to set |
| Tests all compile out (`compiled_out`: code, plus a failing test behind `//go:build e2e`) | `test_ok: true, test_count: 0, ignored_files: [main_test.go]` | `test_ok: true, tests_run: 0, tests_listed: 0, ignored_files: [main_test.go]` | Yes. Before was also true here, but read the same as a repo that passed |
| Nested module (`nested_tested`: a passing root test, a `billing` module with a test that always fails) | `test_ok: true, test_count: 1`, `billing` invisible | `test_ok: true, tests_run: 1, unchecked_modules: [billing]` | Yes. `./...` never reached it, and now the result says so. Also true with the repo's own `go.work` naming it (measured: `./...` still skips it) |
| A test that hangs, Go's clock first (`hang`, clock cut to 2 s) | ARDE's clock ran out first, as both were 10 minutes: `ChildTimeoutError`, "ran longer than 8 s and was stopped", no clue | Red result: `test_ok: false, tests_run: 0`, output holds `test timed out`, `TestHang` and the test's own `CLUE: waiting for the database at db:5432` | Yes |
| A test that hangs, ARDE's clock first (8 s) | "ran longer than 8 s and was stopped." and nothing else | The same line, then the end of the output, naming `TestHang` and the clue, as events | Yes |

The real clocks were cut for the hang: 540 s and 600 s were not waited out.

The review's other shapes:

| Repo | Before | After |
|---|---|---|
| `benches` | `test_count: 2`, nothing said | `tests_run: 0, tests_listed: 2` |
| `testmain` (`TestMain` exits before `m.Run`) | `test_ok: true, test_count: 0` | `tests_run: 0, tests_listed: 0`. The listing goes through `TestMain` too, so the one test isn't seen |
| `all_tagged` | `build_ok: true` | `build_ok: false`, `tests_listed: None`, `ignored_files: [main.go]` |
| `empty_with_mod` | `build_ok: true` | `build_ok: false`, `tests_listed: None` |
| `broken_mod` | **Raised** `GoCommandError`, blaming a side step | Red result, all false, Go's words in `output` |
| `no_mod_with_go` | **Raised** the same | Red result, all false |
| `halfcompile` | `test_count: 3` of 8 | `tests_run: 3, tests_listed: None` |

### `shop` and a `catalog-poc` copy against the base

`run_checks`, inventory, hashes and `changed_declarations`, through the real `GoAdapter`, base source against final source, each with a one-token edit for `changed_declarations`. `capture.py` and `compare.py` in the scratchpad's `fixC`. The compare undoes only the named rename and the added fields, and lists everything else. I ran it again on the final commit `0e00e62`, along with the probes for the four required repos, and the results below are from that run.

| Repo and profile | Differences | Routes | Hashes | `tests_run` / listed |
|---|---|---|---|---|
| `shop` | 1 | 2 | 2, all equal | 3 / 3 |
| `catalog-poc` copy, spec's profile | 1 | 3 | 3, all equal | 8 / 8 |
| `catalog-poc` copy, plus an env pattern | 1 | 3 | 3, all equal | 8 / 8 |

**Every other difference, and why it's right.** There is one kind, and it's the same in each run: `CheckResult.output` gains a `PASS` line above each package's `ok` line.

```
== test (exit 0) ==
?   	example.com/shop	[no test files]
PASS
ok  	example.com/shop/pricing	TIME
```

`-json` implies `-v`, so `go test` prints that line and it reaches `output` as package text. It is one harmless line per package, and filtering it would add a rule for nothing. Code decides from the booleans.

**Added or renamed only:** `CheckResult.tests_run`, `tests_skipped`, `unchecked_modules`, and `test_count` to `tests_listed` (equal to `test_count` on all three). `ResponseField.package`, and `omitempty` to `may_be_absent` (equal values). `build_ok`, `vet_ok`, `test_ok` and `ignored_files` are unchanged.

`catalog-poc` original: `diff -rq` without `.git` matches the copy, and the `.git` mtime is still Nov 2 20:01:59. No git ran inside it. `go test` ran on the copy only, as a baseline.

### Docs

`docs/limitations.md` and `docs/architecture.md` were updated in the commit of each item, and the last commit corrects a claim of mine: "costs disk and never memory" no longer holds for the event stream, in those docs and in `child_process.py`'s docstring. `docs/deviations.md` says twelve methods. `docs/specs/phase-1-reading-go.md` has the one `StripPrefix` line.

## What changed

### 1. `run_checks` measures what ran (`60c4bfd`)

Measured before designing, on 15 repo shapes through `go test -json`. A hang has a `run` event and no ending, with its text attributed to the test. A build failure arrives as `build-output` with no `Package`. `go.work` does not pull a nested module into `./...`. Every stdout line was an event: 180 lines, 0 that weren't. My first look at that was cut off by `head`, so I re-ran all 15 through a script that parses every line.

New `go_test_events.py`, pure, reads the stream. `tests_run` counts top-level tests that reached pass or fail. `tests_skipped` names tests and subtests. A test with no ending isn't counted.

**RED:** a first run was only a collection error, which proves little, so I added the module's shape and the constant alone and ran again:

```
39 failed, 43 passed in 1.72s       (fast)
15 failed in 34.24s                 (real Go)
E   AttributeError: 'CheckResult' object has no attribute 'tests_run'
```

**GREEN:** `95 passed in 1.20s`, `15 passed in 24.13s`.

**An existing test caught a real consequence.** `test_a_test_that_prints_400_megabytes...` wanted a red result with a tail. `-json` holds every line a test prints, so that stream is cut, and the list says a cut stream stops the run. I kept the list's rule and changed the test to the new contract, with its memory bound. `_require_whole`'s next action, "split the repo's packages", was wrong for this case, so this stream has its own message, which shows the end of the stream and so names the last test.

### 2, 3. A repo that doesn't load, and building nothing (`4b8397e`)

**RED:** `4 failed, 62 passed` and `4 failed` on real Go, including the real raise:

```
GoCommandError: Cannot list the files the build leaves out: go: errors parsing go.mod:
```

**GREEN:** `66 passed`, and then the real-Go run showed my expectation was wrong, not the product: Go's words with no `go.mod` are "does not contain main module". I corrected the test.

`build_ok` reads the whole line `go: warning: "./..." matched no packages` from stderr, because a compiler warning quotes source and a repo chooses that text.

### 4. A timeout carries its tail (`b870a40`)

**My first RED was false.** All the new tests passed with no implementation. `python -c CODE` puts the code in `argv`, and the message quotes the command, so the "clue" was found in the program's text and not in its output. The child now works the text out as it runs. Honest RED: `3 failed, 6 passed`, and on real Go:

```
assert 'waiting for the broker at queue:5672' in '`go test -json ...` ran longer than 6 s and was stopped.'
```

**GREEN:** `81 passed`, `1 passed`.

`read_tail` now also serves `service_runner._log_tail`, which held the same three lines.

### 5, 10. Literals and tags (`36a0acf`)

Items 5 and 10 share a test file and one rule: text JSON can't carry isn't text.

**RED, item 5:**

```
match_test.go:277: match 0: JSON would turn these bytes into another text, got "caf�"
E   AssertionError: assert not ['A�']
```

**RED, item 10:** `23 failed, 373 passed` in Python, and in Go:

```
declarations_test.go:271: an escaped quote in the tag: got <nil>, want "a\"b"
```

**GREEN:** `ok goinspect`, `396 passed`, `1 passed`.

`_TAG_ENTRY` and `_json_tag` are gone, and `reflect.StructTag` reads the key. `tag` stays on the wire. A tag past the cap has `tag` as the stand-in and `json_tag` null, so `test_a_tag_past_goinspects_cap_is_reported_by_the_inventory` passes unchanged, and `TOO_LONG_TO_PRINT` and `tooLongToPrint` are untouched and still match. The parser's Python tests moved to Go, which now does the parsing.

### 6. Closed ports (`1e507e3`)

Narrowing the `except` alone would have let a raw `OSError` out of `_wait_until_listening`, breaking "every error names its next action", and let one replace the "exited with code N" message being built. So each of the three callers answers for itself: start raises `ServiceStartError`, stop raises `ServiceStopError`, and the message being built treats an untestable port as closed.

**RED:** `2 failed, 5 passed`, including:

```
Failed: DID NOT RAISE ServiceStopError
```

`test_a_connection_reset...` passed at RED only because the wait ran out, which is the wrong reason, so it now asserts the error isn't that timeout. **GREEN:** `56 passed`.

### 7. Next actions (`4d00447`)

**RED:** `7 failed, 7 passed`. **GREEN:** `14 passed`. goinspect's message now depends on whether there is a reason, because "fix what the message names" is wrong when a kill left none.

### 8. `ProfileProblem` and R92 (`25ed8be`)

Two traps, both found by measuring.

- **Exit 2 is also what Go's runtime gives on a panic or `fatal error`.** So a crash on a repo's file would have read as "your pattern is wrong" and reached a model. The client needs exit 2 and a mark at the start of the message: `goinspect: bad patterns: ` or `goinspect: bad main_package: `. A test covers a panic, a fatal error and silence.
- **`go list` fails with `go.mod requires go >= 1.99.0` too.** Calling every listing failure a bad package would send a model to fix `main_package` for a toolchain mismatch. Measured on 14 values: a `go list` problem with no file position means the text named nothing, and one with a position is in a repo file. `service.go` uses that rule. `toolchain.py` reads `go: ` the same way, because that prefix is the command speaking about the module. A broken `go.mod` is `packages.Load`'s own error, which stays exit 1.

| `main_package` | Exit | Class |
|---|---|---|
| `./nowhere`, `./empty`, `./tagged` (all files tagged), a folder of a nested module, `./...`, a file | 2 | Profile |
| A package clause that doesn't parse, an import nothing provides, a body syntax error, a type error | 1 | Repo |
| A broken `go.mod`, a missing `go.mod` | 1 | Repo |

**RED:** Go, 5 tests. Python, `31 failed, 502 passed`. **GREEN:** both gates, `1878 passed` and `228 passed`, with `ok goinspect`. On the way, one R92 case failed, which is how I found `PurePosixPath` collapsing `./cmd/./api`, and five existing tests needed the new type.

The pattern error no longer names the temporary file, which was already deleted when the reader saw it. The number in it is the model's own, because the default pattern goes last since Group B.

R92 splits on `/`, because `PurePosixPath` silently collapses `./cmd/./api`. `./cmd/...` is refused now, where it used to fail later at the `main` check: R92's "any path element starting with a dot" covers it, and `./...` is the named exception.

### 9. `port_env` (`38eba3e`)

**RED:** `8 failed`. **GREEN:** `8 passed`.

### 11. `built_service` (`45d65d6`)

A plain method around the context manager, so the gate fires at the call and not at `with`. **RED** (a stand-in that kept its folder and skipped the gate): `3 failed` fast and `3 failed` real. **GREEN:** `99 passed`, `9 passed`.

### 13. File server under `StripPrefix` (`28596d9`)

`StripPrefix(both())` compiles with one argument when `both` returns two values, so reading `Args[1]` unguarded would panic. A test holds that case, and the mutant dies on it. A cgo file sits beside one case. A real binary serves a real file under the listed route. The old "every call" test is renamed, because that's no longer true.

**RED:** 6 of 6 cases stopped, plus the cgo test, and `2 failed` in Python. **GREEN:** `ok goinspect`, `19 passed`.

### 12. Go idioms behind the port (`1df871d`)

`discovery_guidance()` is the eighth method, and it holds no repo fact. `is_comment_only`'s Go detail moved to `GoAdapter`. `omitempty` became `may_be_absent`.

**RED, leak test:** it fired on `repo_links.py`, `ports.py` and `domain/inventory.py`: `Left contains 3 more items, first extra item: 'arde/adapters/repo_links.py: mentions go.mod'`. **GREEN:** `956 passed`.

I forgot to take RED for the guidance tests before implementing, so I took it after, by blanking the text: `9 failed, 3 passed`. That shows they bite, and it is not test-first.

## Mutation checks

Everything ran in a clean export, so no mutant could reach a commit. Harness: `fixC/mutate_c.py`. Results: `fixC/mutants-1.txt`, `mutants-2.txt`.

**First run: 89 mutants, reported as 84 killed and 5 survived. The honest count was 83 killed, 5 survived, 1 invalid.** I checked each kill named a failing test, and `GO a tag that is not UTF-8` had "died" only because the mutant left an unused import. A kill by a compile error proves nothing.

| Survivor | What it was | Outcome |
|---|---|---|
| `build_passed=build.succeeded` | Real gap: a list failing when nothing was built | New test |
| `read_tail` reads the whole file | Real gap: `bounded_text` hid it, and my test's name claimed what it didn't prove | Renamed, plus two direct tests |
| Backslash is no separator | Weak test: `./.git\x` starts with a dot either way | `./cmd\.hidden` added |
| `-` counts as a place | **Dead code.** `Pos` comes from `go list`, and `-` appears only when `Error()` prints an empty one | Clause removed |
| `ignore_cleanup_errors` removed | No portable way to make a folder undeletable | **Remains.** Cost if wrong: a cleanup error replaces the body's error |

**Second run:** the 3 closed gaps, the corrected mutant, and 2 new mutants on the rewritten clause. All 6 killed, each by a named test.

**On the code as it stands: 89 mutants, 88 killed, 1 survivor.** That is 82 from the first run that still apply, plus 6 from the second, and 82 + 6 + 1 = 89. Every kill names a failing test. The tests were only added to between the runs, so the first run's kills still hold. I didn't re-run the 82 after removing the dead clause, which changed only that line.

By area: 13 on the event reader, 7 on running the tests, 3 on listed tests, 7 on what doesn't load, 4 on nested modules, 2 on `go:`, 4 on `built_service`, 6 on the tail, 2 on next actions, 6 on ports, 7 on `ProfileProblem`, 5 on `main_package` and `port_env`, 3 on tags, and 20 in Go.

## Additions beyond the list

Each is small and tested. Please confirm or reject them.

1. **`output` leaves out what passing tests printed,** as plain `go test` does, and keeps a skip's reason. Without this, a failure early in a verbose run falls off the 1 MB tail.
2. **A passing run whose stream holds a line that is no event stops the run.** A failed run keeps such a line as text, because the tool's own words can land there. Nothing measured produces one on a pass, so this guards a lie, and a false stop would be loud.
3. **`tests_skipped` names subtests too,** because a table can skip every case and still pass as a whole.
4. **`tests_skipped` and `unchecked_modules` go through `printable_name`:** one line, escaped, at most 200 characters. So Task 17 needs no new stripping for them.
5. **The mark on goinspect's message.** See item 8.
6. **`fingerprints` also exits 2 on a bad `main_package`,** because it shares the load. The list names `match` and `routes`.
7. **`_cannot_load` keeps a module-level failure out of `ProfileProblem`.** See item 8.
8. **`ResponseField.package`,** where the list names `structField`. Task 13's amendment already reads it from there.
9. **A tag whose value isn't UTF-8 counts as no json key,** item 5's rule applied to item 10. That is also what `encoding/json` does.
10. **The leak test scans all of `src/arde` outside the Go adapter, `.py` and `.md`,** not only `.md`, which proves the application clean. It also meant rewording `repo_links.py`, whose docstring named `go.mod`, in a file the docs say names no language.
11. **The stream's own limit:** 64 MB, the same as a `goinspect` reply.

## Decisions to check

- **`test_ok` still comes from the exit code.** It doesn't go false on zero tests, because the review and the plan put that in a warning at Task 17.
- **A cut stream stops a red run too,** as the list says. A red result would also be honest, and it'd keep more text. I kept the list.
- **`tests_run` counts a parent whose subtests all skipped.** It ran. The skips are named.
- **`./...` passes the type and `./cmd/...` doesn't.** `./...` is exempt by exact text.
- **Nested modules inside `vendor`, `testdata`, `_x` or `.x` aren't reported,** because `./...` never looks there for anything.
- **`build_ok` false with exit 0.** The `build` section header reads `(exit 0)` above the warning, which reads oddly but is true.

## Concerns

**One needs a ruling:**

1. **A repo whose tests print tens of megabytes now stops where plain `go test` passes.** A stream over 64 MB stops the run, even when every test passes. Measured: a test printing 400 MB stops with a message naming it. 300,000 lines of 60 characters made a stream of 53.5 MB, so the limit is about 350,000 such lines. `t.Log` counts, because `-json` implies `-v`. **`[NEEDS CLARIFICATION: is 64 MB right, or should a passing run with a cut stream degrade to "tests_run unknown" instead of stopping? The list says stop, so I stopped.]`**

**The rest:**

2. **Two contracts are text.** The goinspect marks and the `go: ` prefix are strings. The marks are pinned on both sides by tests, like `TOO_LONG_TO_PRINT`. `go: ` belongs to the go command, so a future Go could change it. If it did, a module failure would read as a bad package, which is a model turn wasted, and loud.
3. **A test can forge the counts, and I first reported that it couldn't.** Plain text can't. A test that prints the byte `\x16`, which marks the test runner's own lines, can: measured, a repo with one failing test gave `tests_run: 2` and a named skip that doesn't exist. `test_ok` stayed false. My test's name overclaimed, so it now says plain text, and `limitations.md` has the limit. It can't be closed from outside, because the stream is the only source. It gives a hostile repo nothing new, since its tests already run as the user, and the docs already say repos must be trusted. So `tests_run` is evidence about a repo you trust.
4. **A test calling `syscall.Exit(0)` reads as a pass.** Measured: test2json reports `pass` for the test that was running, and the tests after it never run. Only `tests_run` below `tests_listed` shows it, and `limitations.md` says so. The Go exit code is 0, so `test_ok` agrees.
5. **A json name `encoding/json` refuses for another reason is listed as written.** `json:"a\"b"` reads `a"b`, as the review wanted from `reflect`, but the reply carries the field's own name, because `encoding/json` refuses quotes and backslashes. Rare, and in `limitations.md`.
6. **`goinspect routes` with a relative directory prints an empty report and exits 0.** Measured with `.`: `package_folders: []`, no sites, and `filepath.Rel` can't relate it to go list's absolute paths. ARDE always resolves first, so no path in ARDE reaches this, and I found it only through a mistake in my own experiment. It's a silent success, and `filepath.Abs` in `resolveDirectory` would close it. Group D, or leave it.
7. **The timeout's tail is events when ARDE's clock fires on `go test`.** It's readable and it names the test, but it's JSON. In a repo of many packages it may be little, because `go` holds a package's output until it ends. `limitations.md` says so.
8. **Memory near the stream limit.** A 53.5 MB stream peaked at 359 MB, and 17.8 MB at 142 MB. It's bounded and once per run. This corrects a claim the docs made.
9. **`1e507e3` was committed before its own gate.** I ran both gates on a tree holding items 6 and 7, then committed them apart, so that commit's tree was never gated. I then ran both on a clean export of exactly that commit: `1841 passed`, `222 passed`, `ok goinspect`, from the export. That first script echoed `tail`'s exit code and not pytest's, so I read the pass lines. I fixed the script, and every later tree was gated on the staged tree before its commit.
10. **A stray `cat > /tmp/nothing` of mine,** in an experiment, waited on stdin and left an empty file outside the scratchpad. I stopped it and removed it. Nothing else went outside.

## Plan lines now stale

I didn't edit them. Line numbers are from the plan as it stands.

| Line | Says | Now |
|---|---|---|
| 86 | A bad regex, over-long pattern or negative `args` exits 1 | Exit 2, with `goinspect: bad patterns: ` at the start of the message |
| 88 | Task 8 declares seven methods, eleven in total | Eight today, including `discovery_guidance`, twelve in total. `built_service` replaces `build_service` |
| 89 | Tests for `build_service` | `built_service` |
| 92 | `build_service` passes `-buildvcs=false`; `NoRoutesError(ArdeError)` | `built_service`; `NoRoutesError(ProfileProblem)`, and `BadPatternError` and `BadPackageError` exist |
| 195 | `ResponseField ... omitempty: bool` | `may_be_absent: bool`, plus `json_name`, `file` and `package` |
| 408 | `goinspect` exits 1 on bad input | Exit 2 for what the profile gave it |
| 413 | `fields` keys | Adds `package`, `json_tag` and `embedded` |
| 475 | "now eleven methods" | Twelve |
| 485 | `test_count: int; ignored_files` | `tests_run`, `tests_skipped`, `tests_listed: int \\| None`, `ignored_files`, `unchecked_modules` |
| 496 | `build_service(...) -> Path` | `built_service(...) -> AbstractContextManager[Path]` |
| 516 | `ResponseField(struct=..., omitempty=True, ...)` | `may_be_absent=True` |
| 799, 832 | Tests key on `omitempty` fields | The renderer reads `may_be_absent` |

**For later tasks:**

- **Task 12:** `validate_profile` builds inside `with adapter.built_service(...)`. Discovery's prompt takes `discovery_guidance()`. `ProfileProblem` carries exit 3.
- **Task 17:** warn on `tests_run == 0`, on any `tests_skipped`, on `unchecked_modules`, and on `tests_run` below a `tests_listed` that isn't `None`. That last one is the only signal for concern 4. The amendment's list of text to strip needn't grow: both new tuples are one line and escaped already.

Design spec lines 435 (`build_service`) and 544 (eleven methods) are stale in the same way.

## Not verified

- **Linux.** All of this ran on macOS arm64, go1.27.1, Python 3.12.
- **A really full file table.** Item 6's tests raise `EMFILE` by hand. The silent review measured the real case.
- **The real clocks.** 540 s and 600 s were cut to seconds in tests and probes. That 540 runs out before 600 is pinned by a test on the constants, and I did not wait them out.
- **Real third-party repos.** Only fixtures, the review's repos and the `catalog-poc` copy.
- **Whether `go test -json` always prints events on stdout.** 15 shapes did, on one Go version. Addition 2 is the guard.
- **A failed cleanup in `built_service`,** the one surviving mutant.
- **The goinspect mark against a real panic.** A stand-in binary plays the panic. No input is known that panics the real helper.
- **`go test` hanging on an orphan that holds its stdout.** Not probed. ARDE's clock is the backstop.
- **Cold-cache timing of the hang test.** It allows 10 s, including compiling the test binary, on a warm cache.

`.env` was never read, printed, copied or bound. Nothing called the API. Only explicit paths were staged, and one file by blob, to split two items that shared it. Nothing was pushed. No subagent was spawned. The plan and the design are untouched. `.superpowers/` is git-ignored, so this report is not committed.

## Files changed

**New:** `src/arde/adapters/language/go/go_test_events.py`, `discovery_guidance.py`, `tests/adapters/go/test_checks_measured.py`, `test_go_test_events.py`, `tests/application/test_profile_problem.py`.

**Product:** `src/arde/adapters/child_process.py`, `child_text.py`, `repo_links.py`, `service_runner.py`, `src/arde/adapters/language/go/adapter.py`, `errors.py`, `goinspect_client.py`, `inventory_builder.py`, `output_limits.py`, `timeouts.py`, `toolchain.py`, `src/arde/application/ports.py`, `src/arde/domain/base.py`, `inventory.py`, `profile.py`, `tools/goinspect/declarations.go`, `main.go`, `match.go`, `routes.go`, `service.go`.

**Tests:** `tests/adapters/go/test_adapter.py`, `test_goinspect_client.py`, `test_inventory_builder.py`, `test_patterns.py`, `test_service_scope.py`, `test_toolchain.py`, `tests/adapters/test_child_process.py`, `test_child_text.py`, `test_repo_links.py`, `test_service_runner.py`, `tests/domain/test_inventory.py`, `test_models.py`, `tests/test_architecture.py`, `tools/goinspect/bounds_test.go`, `declarations_test.go`, `fingerprints_test.go`, `match_test.go`, `routes_test.go`.

**Docs:** `docs/architecture.md`, `deviations.md`, `limitations.md`, `docs/specs/phase-1-reading-go.md`.

Full RED and GREEN output, probes, captures and mutant results are in `/private/tmp/claude-501/-Users-ionel-work-personal-emag/0f37fc47-1c0a-42ed-bd77-f0a6f96a2ead/scratchpad/fixC`.

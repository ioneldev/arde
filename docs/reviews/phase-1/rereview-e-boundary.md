**Scope.** I re-reviewed the boundary and small fixes (E1, E4 and the two closure items) at `722902e` on `phase-1`. Probes are in `/private/tmp/claude-501/-Users-ionel-work-personal-emag/0f37fc47-1c0a-42ed-bd77-f0a6f96a2ead/scratchpad/p1e/boundary/probes/` (`g1`–`g6`), and their logs are one level up (`g1.log` … `g6.log`, `m1_output.txt`). Each probe ran with `uv run python`, used a fake 3-line secret (`FAKESECRET_LINE_*`) and `GOPROXY=off`. I didn't touch `.env` or catalog-poc. The arde checkout is unchanged (`git status` shows only the existing untracked `fix-e-report.md`).

**Verdict:** E1 and N2 hold. I found four Minors and no Critical or Important.

One line of inquiry was left out on purpose: getting the Go toolchain to read files outside the repo by means other than links. I didn't pursue or measure it.

## Findings

### F1. Minor: a failing parallel test loses all its text when a passing test of the same name runs beside it (M1 not closed for interleaving)
- **Rule:** M1 says a failing test keeps its own output per run.
- **Input** (`repos/m1/par`):
  - `in_test.go`, package `par`: `TestSame` calls `t.Parallel()`, logs `PAR_FAIL_TEXT_1`, sleeps 300 ms, logs `PAR_FAIL_TEXT_2`, then `t.Fail()`.
  - `ex_test.go`, package `par_test`: `TestSame` calls `t.Parallel()`, sleeps 100 ms, logs `PAR_PASS_TEXT`.
- **What ARDE did:** `run_checks` gives `test_ok=False`, but the test section for `par` holds only `FAIL example.com/t/par`. `PAR_FAIL_TEXT_1`, `PAR_FAIL_TEXT_2` and `--- FAIL: TestSame` are all gone.
- **Why:** The real event order is `run, pause, run, pause, cont, cont, output(FAIL_1), output(PASS), --- PASS, output(FAIL_2), pass, --- FAIL, fail`. Every event after the second `run` is counted as run 2. Run 2 has a `pass`, so `_without_runs_that_passed` (`go_test_events.py:61-80`) drops the failing run's text along with it. The comment's premise, "each run event of a name starts the next one", fails once runs interleave.
- **Should:** keep a name's text whenever any run of it in that package failed. Keeping a bit too much is safe, and the text is already bounded.
- **Cases that held:** the sequential k2d pair, the same name in two packages, parallel subtests named `x` and `x#01`, a panic (log and value kept), and output with no line break. Counts were right: `tests_run` 9, `tests_listed` 9. The output was 1,696 bytes.

### F2. Minor: a route path carries raw direction controls into a `ProfileProblem` message, bypassing `visible_text` (N5)
- **Rule:** N5 and `limitations.md`: child output in a message shows no direction control.
- **Input:** `mux.HandleFunc("x‮evil⁦", index)`, written as Go escapes in `main.go`.
- **What ARDE did:** `extract_inventory` raised `RouteNotUnderstoodError` with this message: ``Cannot read the route registered at main.go:9: the path `x‮evil⁦` must start with /…``. Raw U+202E and U+2066 are in the message (measured `True` for both).
- **Why:** goinspect's decoded literal goes through `one_line` (`inventory_builder.py:322`, also `:332`, `:444`, `:457`). `one_line` uses `base._CONTROL_CHARACTERS`, which lacks U+202A–U+202E and U+2066–U+2069.
- **Should:** escape the same set as `visible_text`. The simplest fix is to add the direction controls to `base._CONTROL_CHARACTERS`.
- **What held:** `visible_text` itself escapes exactly the brief's list. I checked every code point: none missing, none extra.
- **Not in the brief's list, left raw:** U+200E, U+200F and U+061C (direction marks), plus U+FEFF and U+200B. Worth a decision, not a defect.

### F3. Minor: the gate passes a link in a folder it can list but not enter
- **Rule:** the gate's own rule (`repo_links.py:68`): a folder that could hide a link must not be passed over.
- **Input:** `d/go.mod` is a link to the secret, then `chmod 0444 d`.
- **What ARDE did:** `links_leaving` gave `['d/go.mod']` before the chmod and `[]` after it. `detects` returned `True`.
- **Why:** `os.path.islink` swallows the `EACCES` from `lstat` and answers False. The walk itself only stops on a folder it cannot list. A path longer than PATH_MAX would be passed the same way (reasoned).
- **Impact:** Go can't read through that folder either. With `go.work use ./d`, `run_checks` failed with `cannot load module d`, and the secret appeared 0 times. So the link only matters if repo code restores the mode partway through a call, which is the N3 class.
- **Should:** call `os.lstat` directly and turn any error into `RepoNotReadableError`, so the gate fails closed.

### F4. Minor: the N3 doc paragraph leaves out the measured in-call case
- **Rule:** the brief asked whether the N3 documentation is accurate.
- **Input:** a passing `TestPlant` runs `os.Symlink(SECRET, "leak.go")`.
- **What ARDE did:** `run_checks` raised `GoCommandError` quoting `leak.go:1:1: expected 'package', found FAKESECRET_LINE_ONE`, which is 1 secret line. The next call's gate refused the link.
- **The gap:** `limitations.md` now names only "a test's child that outlives `run_checks` or the service Explore runs" and says "a later Go command in the same call". It omits the simplest case, which the old line named: a test running inside `go test`, with `go test -list` and `go list` later in the same `run_checks` call. A child that outlives `run_checks` can only reach a later call, after that call's gate, so the example and the clause don't fit together.
- **Smaller doc point (reasoned, not measured, because the rules allow no clones):** "a clone does not create one [hard link]" isn't true of a local-path `git clone`, which hard-links `.git/objects` by default. This is harmless, because Go never reads those files.

## Checked and found right (measured)
- **E1, every caller of the gate:**
  - All 7 entry points refuse a `.git/hooks/pre-commit` link out of the repo, and the p11 shape: `detects`, `extract_inventory`, `route_fingerprints`, `run_checks`, `built_service`, and `changed_declarations` on either side. The secret appeared 0 times.
  - The message names the link (`.git/hooks/pre-commit`), and the next action ("replace it with the file it stands for") is one a person can follow.
  - `.git/modules/sub/config` linked out is caught.
  - An in-repo link inside `.git` passes.
  - A loop link `sub/loop -> ..` passes, and `run_checks` returns normally.
  - A name holding a newline, ESC and U+202E prints escaped: `evil\nname\u001b[2J‮gnp.go`.
  - APFS refuses names that aren't valid UTF-8 (`EILSEQ`), so I couldn't test those here.
- **`.git` as a file with `gitdir:` pointing outside:** all methods returned, and a stub `git` first on `PATH` never ran.
- **Hard link:** it passes the gate, as documented. `extract_inventory`, `run_checks` and `built_service` each quoted 3 of 3 secret lines.
- **`is_comment_only`:** it has no gate, but goinspect `compare` returns only file names and falls back to a byte comparison when a file doesn't parse, so no text comes back. This is from reading the code, not measured.
- **N2:**
  - The child env has `GOPACKAGESDRIVER=off`, `GOENV=off`, and no `GOFLAGS`.
  - A stub driver first on `PATH` never ran across `extract_inventory`, `route_fingerprints`, `run_checks` and `built_service`. That held with the user exporting `GOPACKAGESDRIVER=<stub>`, a user `GOENV` file naming it, an exported `GOFLAGS`, a repo `go.work`, and a repo `go.env`.
  - A test saw `off`.
  - x/tools v0.52.0 `findExternalDriver` reads only `cfg.Env`, and both `packages.Load` calls leave it at the process environment, so neither the repo nor `go env` can override it. The go commands themselves never consult a driver.
- **Closure 1:** `test_service_scope.py` uses `local_service_runner()` and contains no `os.environ`.
- **Closure 2:** on a scratch copy, `TestEachConstNameKeepsItsOwnLine` passes unmutated. The mutant `pos: valueSpec.Pos()` at `declarations.go:179` fails it with `got map[first:4 second:4]`.
- **Doc lines:**
  - M3: with a route pattern, `dropped` holds `cmd/admin/main.go:9: route outside main_package ./cmd/api`. With no pattern, `dropped` is `[]`.
  - M5: five tests and one benchmark give `tests_run` 5 and `tests_listed` 6.
  - Exit 2 for the gate is correct.
  - The `visible_text` code-point list in `limitations.md` matches the code.

## Not checked
- I didn't test on Linux, very deep trees (I relied on the earlier 699-level measurement), or paths longer than PATH_MAX (reasoned only).
- I didn't test a parent folder swapped partway through a call beyond the in-call plant.
- E2 and E3 (routes and anchors) were out of scope.
- I didn't run `make check`.
- I didn't verify the M2/M4 doc sentences or the glossary's `dropped` wording by measurement.
- Non-link ways for the toolchain to read outside files were deliberately not explored.
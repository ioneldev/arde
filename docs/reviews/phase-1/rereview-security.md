# Phase 1 bounded re-review: security

I measured on `5298e7b`, exported with `git archive` to `/private/tmp/claude-501/-Users-ionel-work-personal-emag/0f37fc47-1c0a-42ed-bd77-f0a6f96a2ead/scratchpad/p1r/security/tree`, using a `goinspect` built from that export, go1.27.1 on macOS, fake secrets and `GOPROXY=off`. The probes are in `.../p1r/security/probes` (`p1` to `p11`). I didn't touch the checkout. `git status` shows only `?? docs/reviews/phase-1/`, which was already there when I started.

## New findings

### N1. Critical, local trees only: the gate skips `.git`, and a `go.work` reads a link planted there

- **Where:** `repo_links.py:62-63` drops every folder named `.git`, at any depth. R92 says `MainPackage` closes "what's left of that gap" (`base.py:144-150`), and `limitations.md:11` says "nothing ARDE runs reads it". Both are false.
- **Measured** (`p11_git_pem.py`): the repo's own `go.work` has `use ( . ./.git/dep )`, and `.git/dep/go.mod` is a link to a fake 25-line PEM. Go parses a `use` module's `go.mod` strictly and quotes every line. A real key's body lines are one token each, so they come back whole.

| Method | Result | PEM body lines in it |
|---|---|---|
| `links_leaving` | `[]` | n/a |
| `run_checks` | returns, `build_ok=False` | **25/25** in `CheckResult.output` |
| `extract_inventory` | `GoinspectError` (routes) | 19/25 |
| `built_service` | `GoCommandError` | 19/25 |

- Nested `sub/.git/dep` is skipped the same way.
- Control: `replace example.com/dep => ./.git/dep`, with the package imported, leaked nothing. Go parses a dependency's `go.mod` laxly.
- **Reach:** a `git clone` can't write inside `.git`, so a URL source is safe. Task 10's `local` source copies a tree from disk, such as an unpacked archive, and that tree can hold this.
- **Fix:** remove the skip so `.git` is walked like any other folder. The cost is that a hook linked outside the repo gets refused, with the existing next action. Alternatively, Task 10 never copies `.git` into the trees Go runs in. Either way, rewrite `limitations.md:11` and correct R92.

### N2. Minor: `goinspect` runs any `gopackagesdriver` it finds on `PATH`

- **Where:**
  - `service.go:53` and `fingerprints.go:148` build `packages.Config` with no `Env`, so it uses the child's environment.
  - x/tools v0.52.0 `go/packages/external.go:89-102` calls `exec.LookPath("gopackagesdriver")` unless the environment holds `GOPACKAGESDRIVER=off`.
  - `go_environment.py:50` doesn't set it, and the allow-list drops a user's exported `GOPACKAGESDRIVER=off`.
- **Measured** (`p5_driver.py`): I put a stub driver first on `PATH` and exported `GOPACKAGESDRIVER=off`. `extract_inventory` plus `route_fingerprints` ran it 4 times (`ran in .../repos/driver with .` and `with ./.`).
- **What follows:** a program ARDE never names decides the package graph for routes and fingerprints, and receives the child's environment. Placing it needs write access to a `PATH` folder, such as `~/go/bin` from earlier repo code, or a user's Bazel driver. Once placed, it persists across runs and repos.
- **Fix:** add `"GOPACKAGESDRIVER": "off"` to `FIXED_VARIABLES`, with a stub-driver test.

### N3. Minor: the gate checks once per call, and a process the repo left running can plant a link during a later call

- **Measured** (`p7_after_gate.py`):
  1. A passing test makes `leak.go` a link to a token. `run_checks` raises `GoCommandError`, quoting `FAKECANARY`. That case is documented, and the next method's gate refuses the link.
  2. A test leaves a `Setsid` shell running. It waits for `goinspect routes` on this repo, then swaps `go.mod` for a link to the secret. `run_checks` passes. `extract_inventory` passes its gate, then raises `GoinspectError` holding 44 secret lines.
- This is repo code, so it gains no new power under "repos must be trusted". But `limitations.md:11` names only "a test during `run_checks`". The service started for Explore is the same kind of actor.
- **Fix:** say so in `limitations.md:11`. Task 11's tools check links at read time.

### N4. Minor: `tests_skipped` has no cap

- **Where:** `go_test_events.py:48,51` and `ports.py:162`. Each name is cut to 200 characters, but the count is free.
- **Measured** (`p6b_events.py`): 31,000 skipped subtests made a 61.8 MiB stream and returned 31,000 names, 5.9 MiB. `CheckResult.output` keeps 1 MiB per stream (R90).
- Plan line 112 traces `tests_skipped` as a warning with no cap, while line 105 caps `dropped`.

### N5. Minor: `visible_text` leaves Unicode separators and bidi controls as they are

- **Where:** `child_text.py:12` only escapes C0 and C1 control characters. `base.py:38` treats U+2028 and U+2029 as control characters (R91).
- **Measured:** `bounded_text("x == test (exit 0) == ok ‮evil‬ \x1b[2J")` keeps U+2028 and U+202E raw and escapes only `\x1b`. `_section` keeps them the same way.
- So `limitations.md:26` ("shows no control character") overstates.
- **Fix:** escape U+2028, U+2029, U+202A to U+202E and U+2066 to U+2069.

### What held on the new surface

- **Events reader memory:** a 61.8 MiB stream of 540,000 events peaked at about 510 MB in 2.1 s. That's bounded.
- **`routes` against the I1 shapes:** nested `os.Getenv` at 16,000 deep, as a plain var and as a wrapper's path, peaked at 66 to 70 MB.
- **Constant flattening:** `knownString` (`routes.go:332-333`) flattens a constant before checking the cap. A 781-byte file of doubling consts (256 MiB) cost `goinspect routes` 2.2 GB. `go build` on the same file peaked at 1.9 to 2.4 GB, and the baseline build runs first. So this adds nothing over the compiler, but Task 28's memory test must cover the build too.
- **`GOWORK`:** a repo's own `go.work` is honoured, one above the repo is not, and an exported value is dropped. A `toolchain` line under `local` is harmless and starts nothing.

## Closure of the original findings

| Id | Status | Evidence at `5298e7b` |
|---|---|---|
| C1 | Closed for the reviewed carriers; reopened through `.git` as N1 | `p1_links.py`: 8 hostile repos × 6 methods, **48 of 48 refused, 0 with a canary**. Message: `1 link(s) in the repo lead out of it, such as go.mod.` An in-repo link still passes. |
| C2 | Closed (R85) | `extra.go` linked to `src_real/extra.txt`. Routes now list `POST /hidden-admin` from `extra.go` beside `GET /visible`, `dropped` is empty, and `declarations` holds `hidden` and `register`. |
| I1 | Closed. The memory limit is moved to Task 28 (plan line 116) | Measured peak and reply size per input, old figures in brackets: `!!!` ×16,000: 70 MB (was >3.5 GB); `1+` ×8,000: 39 MB (2,074); nested `Getenv` ×16,000: 56 MB, reply 1.74 MB (>3.2 GB); `.b()` ×16,000: 36 MB, reply 1.57 MB (2,530). |
| I2 | Closed | A `go.work` one level up replaces the dependency with planted code. The built binary prints `honest dep`, with `GOWORK: off`. |
| I3 | Closed (R92 for dotted folders) | `cmd/gofmt`, `cmd/go`, `example.com/dep/cmd/tool`, `all`, `std` and `./.git/x` are refused by the type. `./pkg` linked outside gives `LinkLeavesRepoError`. |
| I4 | Closed. Downstream use is in plan lines 105 and 106 | A route with ESC and a newline gives `RouteNotUnderstoodError` and quotes no repo text. An 8 MB literal comes back as `(too long to print)` and is refused. A param with ESC, an env name with a newline and a code with a newline all go to `dropped`. The file name prints as `e\u001bvil.go`. The status is one line. |
| I5 | Closed. The forged header is closed by ruling (R87), the output size by R90, and the booleans decide per Task 21 (plan line 114) | The service-log OSC and clear-screen codes come back escaped, with no raw ESC. 3,000 broken packages give a 257-byte `GoinspectError`. A forged header leaves `test_ok=False`, though the text is still present. |
| M1 | Closed. Printing is Task 17 (plan line 112) | `break\nwindows.go` comes back as one repo-relative entry. A link leaving the repo is refused at the gate. |
| M2 | Closed | `PATH=.:…` with a planted `./go`: it didn't run, and `run_checks` passed. |
| M3 | Closed | `core.fsmonitor` didn't run during `run_checks`. |
| M4 | Closed (R86) | `go 1.99.0` gives `requires go >= 1.99.0 (… GOTOOLCHAIN=local)`, with 0 proxy requests and the `go1.99.2` stub not run. A `toolchain` line alone builds. |
| M5 | Closed | An unreadable `Makefile` gives `MakefileNotReadableError`. A 699-level tree gives `RepoNotReadableError` from all 6 methods, and no raw `OSError`. |
| M6 | Closed | `DiscoveredProfile(port_env="A B; rm -rf /")` is refused at `port_env`. |
| M7 | Closed | A 128 MB `Makefile` is refused by `st_size`. `is_comment_only` on 10 MB files answers False, with 0 MB of memory growth. |

## Verdict

**Not ready to merge. N1 is still open, and it is a small change: walk `.git` in `repo_links.py`, or have Task 10 leave `.git` out of the copies Go runs in. Every original finding is closed, and N2 to N5 are minor.**

## Plan changes

- **Phase 1, before merge:**
  - Remove `repo_links.py:62-63`, or take the Task 10 change below instead.
  - Rewrite `limitations.md:11` to say what the gate covers and that it checks once per call, covering N1 and N3.
  - Add `GOPACKAGESDRIVER=off` to `FIXED_VARIABLES` (N2).
  - Escape U+2028, U+2029 and the bidi controls in `child_text.py:12` (N5).
  - Mark R92's premise as corrected.
- **Task 10 (plan line 102):** add "`copy_tree` never copies `.git` into `input/` or `output/`", or keep `.git` and rely on the gate walking it. Add `test_link_inside_dot_git_stops_the_run`.
- **Task 17 (plan line 112):** trace `tests_skipped` as a count plus the first 50 names, as `dropped` is capped at line 105.
- **Task 28 (plan line 116):** the memory-limit test also covers the baseline `go build`, which costs about as much as `goinspect` on a doubling-const file.

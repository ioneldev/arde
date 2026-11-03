# Phase 1 fix, Group E: the re-review's findings

The bounded re-review read `f145fe7..5298e7b` and found three Criticals and some minors. Its reports are in this folder: `phase-1-rereview-security.md`, `phase-1-rereview-bugs.md` and `phase-1-rereview-closure.md`. Read the section each item names. They hold the exact inputs, and the services the bug hunter wrote are in `/private/tmp/claude-501/-Users-ionel-work-personal-emag/0f37fc47-1c0a-42ed-bd77-f0a6f96a2ead/scratchpad/p1r/bugs/svc/` (`w1` to `w7`, `t1`, `k1`, `k2`, `k2d`), with the security probes in `.../p1r/security/probes/`. The rulings R113 to R116 in `progress.md` here are requirements, not suggestions.

Branch `phase-1`, work in place, base `5298e7b`. Treat every claim in the reports as a claim to check: reproduce each failure on the base before you fix it.

## E1. The links gate walks `.git` (security N1, R113)

`src/arde/adapters/repo_links.py:62-63` drops every `.git` folder. A local tree whose `go.work` says `use ./.git/dep`, with `.git/dep/go.mod` a link to a secret, gets the secret's lines quoted back in `CheckResult.output` and in error text (`p11_git_pem.py`). Remove the skip, so `.git` is walked like any folder.
- Tests: a link inside `.git` that leaves the repo stops every `GoAdapter` method; the `p11` shape stops at the gate with no secret text in any result or error; a link inside `.git` that stays inside passes. Any test that pins the old skip changes, and say which.
- Rewrite `docs/limitations.md:11`: what the gate covers, that it runs once per call, and that a process the repo left running (a test's child, or the service Explore runs) can plant a link after it (N3).

## E2. Wrapper sites are read, or stop (bugs C1, R114)

Today a site inside a function a route pattern matches is skipped whenever its path isn't constant, and the method comes from the callee's last name. Make it exact:
- A site inside a matched wrapper is trusted only when its path expression is one of: the wrapper's path parameter alone; a constant `"VERB "` plus that parameter, where the constant gives the method over the callee's name; or the wrapper's method parameter plus `" "` plus the path parameter.
- For the third form, a route pattern names the method's position with a new `method` key in `args`. At each matched call, the method argument must be a constant, or the run stops. Allow `method` for `route` patterns only, and refuse it elsewhere, as unknown keys are refused today.
- Any other form stops with exit 3, naming the site and saying the wrapper builds its path from more than its parameters. So does a matched wrapper call whose wrapper registers nothing (`w7` with `handler` on the mux).
- Tests, one per bug-hunter service, each RED on the base: `w1` gives `POST /orders` and `DELETE /orders/{id}` with a `method` pattern; `w2` (both the `path.Join` and the local `item := base+"/{id}"` forms) stops; `w4` gives `GET /items`; `w7` stops; `t1`'s `post` registering `"GET "+path` gives `GET`. Flip the case in `TestRoutesDoesNotCallAMethodOrAPlainPathAPrefix` that pins the old method.
- `shop` keeps its routes and methods exactly.
- Add one `limitations.md` line saying exactly which wrapper forms are read.

## E3. Handler identity: fingerprint and anchor (bugs C2, R115)

Today `innermost` peels any call with exactly one handler-shaped argument, and "handler-shaped" accepts any function taking a `ResponseWriter` and a `*Request`, whatever else it takes. So `handleListItems(s, renderError)` roots at `renderError`.

Requirements:
1. **Handler shape** is exactly `(http.ResponseWriter, *http.Request)`, optionally returning `error`.
2. **Fingerprint:** a route's hash covers every function of the module named in its handler expression, callees and function values alike, at any depth. So an edit to the factory, the middleware or the handler moves it. `goinspect routes` reports them per site, for example as `roots`, and `route_fingerprints` hashes all of them. A route with exactly one root keeps exactly that root's fingerprint, so routes whose shape didn't change keep their hashes.
3. **Anchor:** `handler_id` is the one function the route's docs belong on. It's the innermost handler when every call on the way only wraps it, and otherwise the outermost function of the module, which is a factory. A function of the module only wraps its argument when its body calls `ServeHTTP` on that parameter, or passes it to a call whose result has handler type. A library call such as `http.TimeoutHandler` or `http.HandlerFunc` wraps.
4. **Mounts:** a `*http.ServeMux` reached only through calls that wrap it is a mount, as now. A mux passed to a function of the module that doesn't wrap it, such as `routesPage(a.mux)`, is not a mount, and the call is a route row anchored at that function.
5. When no single function fits, `handler_id` is None with a reason, as today.

Tests, each RED on the base: `w3` (the hash moves when `handleListItems` changes, and the anchor is `handleListItems`), `w3c` (two routes, two anchors, two hashes), `w4` (`spa(pages, notFound)` anchors at `spa`; `routesPage(a.mux)` gives `GET /debug/routes`). These shapes from `k1` and `w5` read right today and must stay right: a middleware chain `chain(http.HandlerFunc(s.show), logged)` anchors at `show`; `root.Handle("/api/", logged(api))` stays a mount; a promoted method value; generic method values and a generic factory; `apiFunc(s.orders)`; `http.TimeoutHandler(s.slow(), ...)`.

Update `limitations.md:46`, `:77` and `:78`, because middleware of the module is now inside the fingerprint.

## E4. Small ones

- **N2:** add `"GOPACKAGESDRIVER": "off"` to `FIXED_VARIABLES` in `go_environment.py`, with a test that a stub `gopackagesdriver` first on `PATH` never runs.
- **N5:** `visible_text` in `child_text.py` also escapes U+2028, U+2029, U+202A to U+202E and U+2066 to U+2069, as it does C0 and C1. Make `limitations.md:26` true.
- **Bugs M1:** `go_test_events.py` keeps a failing test's output per run, not per name, so a `TestX` that fails in `pkg_test` keeps its text when a `TestX` in `pkg` passed (`k2d`).
- **Docs:** `limitations.md:40` says a route from another folder is listed in `dropped` only when a route pattern matched it (M3). `:70` lists a library as `main_package`, which `built_service` refuses, and two package clauses in one folder or a broken `//go:build` line, which read as profile problems while the baseline build fails first (M2, M4). `:81` says any benchmark also puts `tests_run` below `tests_listed` (M5). `glossary.md:13`'s `dropped` entry names "outside main_package" and "a file this build leaves out".
- **Closure 1:** `tests/adapters/go/test_service_scope.py:58-92` runs services with its own `free_port`, `wait_for` and `serving`, and with `{**os.environ}`. Use `local_service_runner()`.
- **Closure 2:** add `TestEachConstNameKeepsItsOwnLine`. RED is the mutant `pos: valueSpec.Pos()` at `declarations.go:179`.

## Required at the end

- Compare against the base on `shop`, a `cp -R` copy of `catalog-poc` (remove its `.git` from the copy), `parcels` and `library`: inventory, route hashes and reasons, declaration hashes, `changed_declarations` after a one-token edit, and `run_checks`. Every difference must be one this brief asks for, and say each. Run a control that shows the comparison sees a change.
- `make check` five times in a row with `make integration` looping alongside. Keep full logs.

## Rules

- `.env` holds a real API key. Never read, print, copy, source or mount it. Nothing here calls the API.
- `catalog-poc` is read-only, and no git command runs in it. Use a `cp -R` copy.
- No clones, fetches, installs or third-party code. `GOPROXY=off` is fine for your probes.
- Stage by explicit path, never `git add .` or `-A`. Don't push. Don't edit the plan or the design. Never spawn a subagent.
- Test first, RED before GREEN, for every behaviour change, and show each RED.
- One commit per item or sub-item. Before each, run on the exact tree: `make check`, `make integration`, and `cd tools/goinspect && go test -count=1 ./...`. Commit only after all three pass. Keep full logs in `/private/tmp/claude-501/-Users-ionel-work-personal-emag/0f37fc47-1c0a-42ed-bd77-f0a6f96a2ead/scratchpad/fixE`, never only the last line.
- Comments explain why only. Names read as prose. Past two positional arguments, keywords or a structure.
- Commit messages end with exactly:

```
Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01VB5t5JjLH1p2Fxw9cdNURk
```

## Report

Return it as text, under 2,000 words. Status, commits, test counts before and after for all three gates with the breakdown that sums to each, each RED, the base comparison, what you found the brief or the reports got wrong, and what you didn't verify. Check every figure against its breakdown before you send it.

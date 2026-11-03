# Phase 1 fix, Group F: the Group E re-review's findings

Group E (`e775f77..722902e`) fixed the re-review's three Criticals. Two scoped re-reviews of it found three more Criticals in the route rules, two Importants and some Minors. Their reports are in this folder: `phase-1-rereview-e-routes.md` and `phase-1-rereview-e-boundary.md`. The probe services are in `/private/tmp/claude-501/-Users-ionel-work-personal-emag/0f37fc47-1c0a-42ed-bd77-f0a6f96a2ead/scratchpad/p1e/routes/` (each finding names its folder) and the boundary probes in `.../p1e/boundary/`. The rulings R117 to R123 at the end of `progress.md` here are requirements, not suggestions. Group E's report, `phase-1-fix-e-report.md`, says what the current rules are and where it went beyond its brief.

Branch `phase-1`, work in place, base `722902e`. Treat every claim in the reports as a claim to check: reproduce each failure on the base before you fix it. I reproduced C-1 on `r10` and C-3 on `h2` myself.

The aim of this round is to close classes, not instances. Each rule below is written so that a shape the reviewers didn't try falls into a stop or into the hash, never into a silent wrong answer. When you meet a shape no rule covers, stop on it and say so in the report.

## F1. A wrapper's parameters are read only when nothing writes them (C-1, R117)

A wrapper form is trusted only when the wrapper's body, closures included, never assigns, op-assigns, increments or takes the address of its path or method parameter. Otherwise the run stops with "builds its path", naming the write's file and line.
- Tests, each RED on the base: `r10` (group prefix in a field), `r1` (`path = "/v1" + path`), `r1b` (conditional `path += "/"`), `r2` (`method = "PUT"`) all stop.
- Rewrite `limitations.md:42` so it's true: a prefix held in a field stops the run, in every form.

## F2. A wrapper is used only through calls the pattern reads (C-2, R118)

Every use of a function a route pattern matched as a wrapper must be a direct call that the pattern reads. A method value, a function value, an argument, an assignment, a store into a field or slice, or a call through an interface the wrapper's type satisfies stops the run with exit 3 and names the use.
- Tests, RED on the base: `r3` (passed as `registerShop(a.get)`, and `g := a.get; g(...)`) stops.
- Measure the interface case on a probe of your own: a registrar interface with a `get` method, implemented by the wrapper's type, called through the interface. If the pattern already reads that call from its arguments, say so and test it. If its routes are lost, it must stop.

## F3. Handler types are inside the hash, and a struct handler has an anchor (C-3, R119)

- When the fingerprint walk reaches a value of a module type converted to an interface (SSA `MakeInterface`), every method of that type, on value and pointer receivers, joins the walk.
- A handler expression that is just a value of a module type with a `ServeHTTP` method anchors at that method and roots there. That covers a composite literal, its address, and a variable of that type. Today `mux.Handle("GET /plain", &itemsHandler{...})` gives roots `[]` and no anchor.
- Tests, RED on the base, on `h2`:
  - Editing `(*itemsHandler).ServeHTTP` moves `GET /items`.
  - Editing `(*headerMiddleware).ServeHTTP` moves `GET /orders`.
  - `GET /plain` anchors at `(*itemsHandler).ServeHTTP` and its hash moves on the same edit.
- Fix `limitations.md:78` and `:79` to match.

## F4. A route wrapper's own code is inside the hash (I-2, R120)

A route registered through a wrapper also roots at three things: the wrapper itself, every module function named in the wrapper's inner site, and every module function named in the wrapper call's other arguments.
- Tests, RED on the base:
  - `h4`: editing `writeJSON` moves `GET /items`.
  - `r8`: editing `requireAdmin`'s role check moves `GET /admin/items`.

## F5. The wraps test reads what a function does, and two routes never share an anchor by accident (I-1, R121)

- A module function wraps its handler parameter when it does any of these:
  - calls `ServeHTTP` on it
  - calls it directly
  - takes its `ServeHTTP` method value
  - passes it to a call whose result has handler type
  - stores it in a field of a module type that has a `ServeHTTP` method, and returns that value
- The guard: when two or more routes resolve to one anchor and their handler expressions differ, each of them gets `handler_id` None. Compare the expressions by the functions they name and the constants they hold, not by text. The reason names the other routes. Routes whose expressions are the same, such as one handler at two paths, keep the anchor.
- Tests, RED on the base:
  - `h1`: `logged(listItems)` and `logged(listOrders)` anchor at `listItems` and `listOrders`, in both the `next(w, r)` and the `next.ServeHTTP` spelling.
  - `h2`: `secured(http.HandlerFunc(listOrders))` anchors at `listOrders`.
  - `h3`: `GET /` and `GET /admin/` get None with a reason naming each other.
  - A single route `spa(pages, notFound)` anchors at the fallback. Write that in `limitations.md`, with why the spec stays right: swag reads only the comment.
  - Group E's `w3`, `w3c` and `w4` tests, and the `k1`/`w5` shapes the E brief listed, stay right.

## F6. Route minors (R122)

- **Phantom mount rows** (`mt`):
  - A mux's `ServeHTTP` method value counts as the mux, and a handler parameter's `ServeHTTP` method value counts as the parameter.
  - With F5's store rule, `secured(a)`, `passed(d)`, `logged(b.ServeHTTP)` and `root.HandleFunc("/c/", c.ServeHTTP)` give only their real sub-routes.
- **Typed method:** `string(m)+" "+path`, with `m` of a named string type, is form 3 (`r7`).
- **A form-1 wrapper handed a mux** (`mw1`) is a mount at its path. If that needs more than the existing mount reading, its stop instead says the wrapper mounts a mux, and you report which you did.
- **An empty method** stops with a message that says the method is empty (`m_empty`).
- **Constants in the hash:** when a route's handler expression is more than a name, its hash also covers the constants in that expression (`paged(5, …)` against `paged(9, …)`). A route whose handler expression is a bare name keeps its root's fingerprint exactly.
- **`limitations.md`:** one line saying a middleware slice held in a package var is outside the hash.

## F7. Boundary minors (R123)

- **F1, parallel runs:** in `go_test_events.py`, a test name's text is kept whenever any run of that name in that package failed. The interleaved parallel case from the boundary report (`repos/m1/par`) is the RED.
- **F2, one character set:**
  - Make one definition in `domain/base.py` of the characters made visible in messages: every code point of Unicode categories Cc, Cf, Zl and Zp.
  - `one_line`, the plain-text types that use `_CONTROL_CHARACTERS`, and `adapters/child_text.visible_text` all use it, and `child_text.py` keeps no list of its own.
  - RED: a route path holding U+202E and U+2066 reaches a `ProfileProblem` message raw, and U+200E, U+200F, U+061C and U+200B are escaped nowhere.
  - Say in the report what the wider set changes in the plain-text types: which inputs they now refuse.
- **F3, lstat:** the links gate calls `os.lstat` itself and turns any error into `RepoNotReadableError`. RED: a link inside a folder with mode 0444 passes today.
- **F4, docs:** the N3 paragraph in `limitations.md` names the in-call case: a test plants a link that a later Go command in the same `run_checks` reads. Correct the hard-link sentence: a clone from a URL makes none, while a clone from a local path hard-links `.git/objects`, which Go doesn't read.

## Required at the end

- **Base comparison.** Compare against `722902e` on `shop`, a `cp -R` copy of `catalog-poc` with its `.git` removed, `parcels` and `library`. Compare the inventory, route hashes and reasons, anchors, declaration hashes, `changed_declarations` after a one-token edit, and `run_checks`.
  - Every difference must be one this brief asks for. Name each.
  - Expect hash changes only for routes registered through a wrapper (F4), or whose handler expression is more than a name (F6), or that reach a module type through an interface (F3).
  - Run a control that shows the comparison sees a change.
- **Load test.** Run `make check` five times in a row with `make integration` looping alongside. Keep full logs.

## Rules

- `.env` holds a real API key. Never read, print, copy, source or mount it. Nothing here calls the API.
- `catalog-poc` is read-only, and no git command runs in it. Use a `cp -R` copy.
- No clones, fetches, installs or third-party code. `GOPROXY=off` is fine for your probes.
- Stage by explicit path, never `git add .` or `-A`. Don't push. Don't edit the plan or the design. Never spawn a subagent.
- Test first, RED before GREEN, for every behaviour change, and show each RED.
- One commit per item or sub-item. Before each commit, run all three on the exact tree: `make check`, `make integration`, and `cd tools/goinspect && go test -count=1 ./...`. Commit only after all three pass. Keep full logs in `/private/tmp/claude-501/-Users-ionel-work-personal-emag/0f37fc47-1c0a-42ed-bd77-f0a6f96a2ead/scratchpad/fixF`, never only the last line.
- Comments explain why only. Names read as prose. Past two positional arguments, use keywords or a structure, in Go too.
- Commit messages end with exactly:

```
Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01VB5t5JjLH1p2Fxw9cdNURk
```

## Report

Return it as text, under 2,000 words. Include:
- the status and the commits
- test counts before and after for all three gates, with the breakdown that sums to each
- each RED
- the base comparison
- what you found the brief or the reports got wrong
- the shapes you stopped on that no rule covered
- what you didn't verify

Check every figure against its breakdown before you send it.

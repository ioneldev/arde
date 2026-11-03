# Phase 1 fix, Group G: the Group F re-review's findings

Group F (`722902e..9a82826`) closed the route classes it set out to close. Its re-review found no wrong path, no silent loss and no hash that failed to move, but four Importants about anchors, the guard and speed, plus Minors. The boundary check found three Minors. The reports are in this folder, `phase-1-rereview-f-routes.md` and `phase-1-rereview-f-boundary.md`, and Group F's own report is `phase-1-fix-f-report.md`. The probe services are under `/private/tmp/claude-501/-Users-ionel-work-personal-emag/0f37fc47-1c0a-42ed-bd77-f0a6f96a2ead/scratchpad/p1f/routes/` (each finding names its folder), and the boundary probes under `.../p1f/boundary/`. Rulings R124 to R132 at the end of `progress.md` here are requirements.

Branch `phase-1`, work in place, base `9a82826`. Reproduce each failure on the base before you fix it. I reproduced I-1 on `x1` and I-2 on `c1` myself.

## G1. A wrapper's fixed site reads its own handler (I-1, R127)

An inner site of a wrapper reads the call's handler only when its handler expression is the wrapper's handler parameter. Any other site reads its own handler expression: its anchor, roots and identity, plus the wrapper as a root.
- Test, RED on the base: `x1`. `OPTIONS /items` and `OPTIONS /orders` anchor at `preflight` and keep it, because their identities are equal. `GET /items` anchors at `items` and `GET /orders` at `orders`.

## G2. Curried middleware is peeled (I-2, R128)

A call whose function is itself a call to a module function, `f(args)(h)`, is peeled when the function `f` returns wraps its parameter by R121's rules. `f` joins the roots.

A handler called through a local variable holding middleware still gets `handler_id` None. Its roots depend on how the variable is assigned:
- **Assigned once:** the roots are the module functions named in the call's arguments and in the variable's initialiser.
- **Otherwise:** the route stays unhashed.

Tests, RED on the base:
- `c1`: `requireRole("admin")(http.HandlerFunc(listItems))` anchors at `listItems`, roots `listItems` and `requireRole`, and the `EDIT-MW` edit moves the hash.
- `c2`: two such routes anchor at their own handlers.
- `admin := requireRole("admin"); admin(h)`: None, roots `h` and `requireRole`, and hashed.

## G3. The guard keeps honest sharing (I-3, R129)

Split each anchor group by identity:
- Routes whose handler expression is the anchor alone keep the anchor. "Alone" means a bare name or method value, through conversions only, with no other roots and no constants.
- Every other identity in the group gets None, and its `shares_anchor_with` names only the routes outside its own identity.
- A method value's receiver counts by its type, not by its declaration.

Tests, RED on the base:
- `g1`: `GET /items` and `GET /v1/items` keep `listItems`. `GET /admin/items` gets None, and its `shares_anchor_with` names the other two.
- `g3`: `s.items` registered from `publicRoutes()` and from `v2Routes()` keeps its anchor at both paths, and so does `s.health`.

Rewrite `limitations.md:78` so it says what is now true.

## G4. The interface scan stays cheap (I-4, R130)

The scan for registrars called through an interface tests only interface methods whose name is a registrar's name.
- Test: count the `LookupFieldOrMethod` calls, or whatever the scan's unit of work is, on a generated service with many module types and many ordinary interface calls such as `err.Error()`. Assert that the count doesn't grow with the ordinary calls. Don't assert on time.
- Measure `big/main.go` from the report before and after, and give both times.

## G5. Fewer roots from interfaces (Q4, R131)

When a module type is converted to an interface, its exported methods join the roots. An unexported method joins only when an interface type in the type's own package declares that name, including an interface literal.
- Test, RED on the base: in `o1`, editing `audit`, which only the unexported `login` calls, no longer moves `GET /items` or `GET /orders`. Editing `Items` still moves `GET /items`.
- `o2`, where `slog.Info("…", "server", s)` hands `s` to `any`, follows the same rule.

## G6. Route minors (R132)

- `e1`: a struct that embeds a module interface field gives no anchor. Make `serveMethodOfAValue` check for an interface method, as `functionNamedBy` does.
- `q3a`: the stop for a registrar reached through an interface names the interface and the concrete type behind it, says a route registered that way can't be read, and points to `limitations.md`.
- `limitations.md:79` covers a local or a package variable holding middleware (`l1`).
- Correct the `routeSite` comment in `routes.go` that says roots are empty when there is no anchor, because roots are kept when the anchor is shared.

## G7. Boundary minors (R124 to R126)

**R124: two character sets, not one.**
- **The display set:** messages show every Cc, Cf, Zl and Zp code point escaped, as now.
- **The refusal set:** the plain-text types that reach Go source refuse only Cc, U+2028, U+2029, U+FEFF, and the bidi embeddings, overrides and isolates (U+202A to U+202E, U+2066 to U+2069). The refusal names the code point and its offset.
- **Tests, RED on the base:**
  - A route path `/كتاب‌ها` (with U+200C), an emoji joined with U+200D, a path holding U+200F, and a soft hyphen are all read as routes.
  - A json tag holding U+200C is read as a field name.
  - A route path holding U+202E stops, and the message names `U+202E` and its offset.

**R125: escape at the printer.** `CheckResult.ignored_files` and `changed_declarations` stay raw, because they are matched against other ids. Add one line to each docstring, or to the port, saying whoever prints them escapes them. Nothing else changes.

**R126: the links gate's messages.**
- A file gone between listing and `lstat`: the message says the repo changed while ARDE read it, and to run again when nothing is writing to it.
- A name too long: the message shows the end of the path and says to move the repo to a shorter path.
- Any refusal names the folder it asks to fix, not a file.
- Tests for all three, including the race simulated by patching `os.lstat`.

## Required at the end

- **Base comparison:** compare against `9a82826` on `shop`, a `cp -R` copy of `catalog-poc` with its `.git` removed, `parcels` and `library`. Cover the inventory, route hashes and reasons, anchors, `shares_anchor_with`, declaration hashes, `changed_declarations` after a one-token edit, and `run_checks`. Every difference must be one this brief asks for; name each. Run a control that shows the comparison sees a change.
- **Load test:** run `make check` five times in a row with `make integration` looping alongside. Keep full logs.

## Rules

- `.env` holds a real API key. Never read, print, copy, source or mount it. Nothing here calls the API.
- `catalog-poc` is read-only, and no git command runs in it. Use a `cp -R` copy.
- No clones, fetches, installs or third-party code. `GOPROXY=off` is fine for your probes.
- Stage by explicit path, never `git add .` or `-A`. Don't push. Don't edit the plan or the design. Never spawn a subagent.
- Test first, RED before GREEN, for every behaviour change, and show each RED.
- Make one commit per item or sub-item. Before each commit, run on the exact tree: `make check`, `make integration`, and `cd tools/goinspect && go test -count=1 ./...`. Commit only after all three pass. Keep full logs in `/private/tmp/claude-501/-Users-ionel-work-personal-emag/0f37fc47-1c0a-42ed-bd77-f0a6f96a2ead/scratchpad/fixG`, never only the last line.
- Comments explain why only. Names read as prose. Past two positional arguments, use keywords or a structure, in Go too.
- Commit messages end with exactly:

```
Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01VB5t5JjLH1p2Fxw9cdNURk
```

## Report

Return it as text, under 2,000 words. Cover:
- status and commits
- test counts before and after for all three gates, with the breakdown that sums to each
- each RED
- the base comparison
- the `big/main.go` times
- what you found the brief or the reports got wrong
- the shapes you stopped on that no rule covered
- what you didn't verify

Check every figure against its breakdown before you send it.

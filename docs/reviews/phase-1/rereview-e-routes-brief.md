# Scoped re-review of Group E: wrapper sites and handler identity

You are an adversarial reviewer. Assume a defect exists in the new route rules and find it. You review; you don't fix.

## What changed

ARDE reads a Go service's HTTP routes with a Go helper, `tools/goinspect`, built on `golang.org/x/tools/go/packages` type information. The re-review of Phase 1 found two Criticals in how routes are read, and Group E fixed them in 11 commits, `e775f77..722902e`, on branch `phase-1` of `/Users/ionel/work/personal/emag/arde`.

- The diff, commit list and stat are in one file: `/private/tmp/claude-501/-Users-ionel-work-personal-emag/0f37fc47-1c0a-42ed-bd77-f0a6f96a2ead/scratchpad/p1e/fix-e-diff.txt`.
- The brief the implementer worked from: `/Users/ionel/work/personal/emag/arde/.superpowers/sdd/2026-11-01-arde/phase-1-fix-e-brief.md` (sections E2 and E3).
- The rules it had to meet are rulings R114 and R115 at the end of `/Users/ionel/work/personal/emag/arde/.superpowers/sdd/2026-11-01-arde/progress.md`.
- The bugs that started it, with exact inputs: `/Users/ionel/work/personal/emag/arde/.superpowers/sdd/2026-11-01-arde/phase-1-rereview-bugs.md` (C1, C2). The bug hunter's services are in `/private/tmp/claude-501/-Users-ionel-work-personal-emag/0f37fc47-1c0a-42ed-bd77-f0a6f96a2ead/scratchpad/p1r/bugs/svc/` (`w1` to `w7`, `t1`, `k1`, `k2`, `k2d`).
- The implementer's report, including where it went beyond the brief: `/Users/ionel/work/personal/emag/arde/docs/reviews/phase-1/fix-e-report.md`.

Read the whole of `tools/goinspect/routes.go`, `route_handlers.go` and `fingerprints.go` at HEAD, and the Python that consumes their reply (`src/arde/adapters/language/go/`, `src/arde/application/inventory_builder.py`).

## What to hunt for

The danger is a success path that lies: a route that comes out with the wrong path, the wrong method, the wrong anchor, or a fingerprint that doesn't move when the code a route runs changes, and no stop or warning says so. A stop that refuses a common, honest shape is also a finding, because it makes ARDE useless on real services.

1. **Wrapper forms (R114).** Write small services that wrap registration in ways real code does: a wrapper calling another wrapper, a wrapper that takes the method as a typed string or a named constant, a method built with `http.MethodPost`, a wrapper on a struct method, a generic wrapper, a wrapper whose path parameter is reassigned before use, a closure inside the wrapper that registers, a method value of a wrapper passed around, a variadic wrapper, a `"GET "+path` where `path` shadows the parameter. For each, does goinspect give the right route, or stop with a clear message? Is there any form it trusts and gets wrong?
2. **The `method` key.** A route pattern can now name a method argument. What happens when the argument is a non-constant, a constant of a named string type, an empty string, a lowercase verb, something that isn't a verb, or `"GET /x"`? At another module's call such as a chi-style `r.Method(m, p, h)`, the implementer made it prefix the path. Is that right, and is it tested?
3. **Handler identity (R115).** The fingerprint must cover every module function in the handler expression, at any depth, and a route with one root must keep the hash it had before. Find a shape where an edit to code the route runs moves no hash: a factory's helper, a method on the receiver the factory captures, a function value stored in a package var, a middleware's own callee, an interface method, a generic instantiation. Find a shape where two different routes get the same anchor or the same hash when they shouldn't.
4. **The anchor.** `handler_id` is the innermost handler when every call only wraps it, else the outermost module function. Check the "wraps" test against real middleware shapes: `next.ServeHTTP(w, r)`, `next(w, r)`, `http.HandlerFunc(next).ServeHTTP`, middleware that stores `next` in a struct, `alice`-style chains written by hand, middleware that calls `next` in a goroutine or a deferred func. The implementer says `next(w, r)` called directly makes it a factory, so docs go on the middleware. Is that what a reader of the docs would want, and does `limitations.md` say it?
5. **Mounts.** A mux reached only through wrapping calls is a mount; a mux handed to a non-wrapping module function becomes a route row. Look for a mux that slips through as neither, or a mount reported twice. Check the regression `8437de5` fixed and whether its neighbours are covered.
6. **Base comparison.** The implementer reports 12 differences against base on `shop`, a catalog-poc copy, `parcels` and `library`, all `roots` added. Rerun that yourself on at least `shop` and a catalog-poc copy and say whether you agree.

## How to work

- Build goinspect from HEAD into your own folder: `cd /Users/ionel/work/personal/emag/arde/tools/goinspect && go build -o <your folder>/goinspect .`. Write probe services under `/private/tmp/claude-501/-Users-ionel-work-personal-emag/0f37fc47-1c0a-42ed-bd77-f0a6f96a2ead/scratchpad/p1e/routes/`. Use only the standard library in probe services; set `GOPROXY=off GOFLAGS=-mod=mod` if a probe needs it.
- `routes` needs `--patterns FILE --main PKG DIR`. Look at `docs/research/spike/data/stdlib.json` and the tests for the pattern format.
- For any catalog-poc check, work on a copy: `cp -R /Users/ionel/work/personal/emag/catalog-poc <scratch>/catalog-copy && rm -rf <scratch>/catalog-copy/.git`.
- Reproduce every finding with a real run, and give the exact input and output. Mark anything you only reasoned about as reasoned.

## Rules

- `/Users/ionel/work/personal/emag/arde/.env` holds a real API key. Never read, print, copy, source or mount it. Nothing here calls the API.
- `/Users/ionel/work/personal/emag/catalog-poc` is read-only, and no git command runs in it.
- Don't edit anything in the arde repo, don't commit, don't push. Write only under the scratchpad folder above.
- No clones, fetches, installs or third-party code.
- Never spawn a subagent.

## Report

Return it as text, under 1,500 words; you can't write report files. For each finding: severity (Critical, Important, Minor), the rule it breaks, the exact input, what goinspect printed, and what it should have printed. Then what you checked and found right, and what you didn't check.

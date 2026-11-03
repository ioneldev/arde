# Scoped re-review of Group F: are the route classes closed?

You are an adversarial reviewer. Assume a defect exists in the route rules and find it. You review; you don't fix.

## Background

ARDE reads a Go service's HTTP routes with a Go helper, `tools/goinspect`, built on `golang.org/x/tools/go/packages` and SSA. Each route gets three things:
- **roots**: the module functions whose code its fingerprint hashes
- **an anchor** (`handler_id`): the one function its swag comment goes on
- **a hash**: it must move when code the route runs changes

Two rounds of adversarial review each found Criticals in this area. Group F, 13 commits `722902e..9a82826` on branch `phase-1` of `/Users/ionel/work/personal/emag/arde`, set out to close them by class rather than instance:
- A wrapper whose path or method parameter is written stops the run.
- A registrar used other than by a direct call stops the run.
- A module type's methods join the hash when it becomes a handler.
- A route wrapper's own code is in the hash.
- "Wraps" is judged by what a function does with its handler parameter.
- Two routes that would share an anchor for different code get no anchor.
- The constants in a handler expression are hashed.

Inputs:
- The diff, commit list and stat: `/private/tmp/claude-501/-Users-ionel-work-personal-emag/0f37fc47-1c0a-42ed-bd77-f0a6f96a2ead/scratchpad/p1f/fix-f-diff.txt`.
- The brief Group F worked from: `/Users/ionel/work/personal/emag/arde/.superpowers/sdd/2026-11-01-arde/phase-1-fix-f-brief.md`. The rulings R117 to R123 are at the end of `progress.md` in the same folder.
- Group F's report, including where it went beyond the brief: `/Users/ionel/work/personal/emag/arde/docs/reviews/phase-1/fix-f-report.md`.
- The last round's findings: `/Users/ionel/work/personal/emag/arde/docs/reviews/phase-1/rereview-e-routes.md`. Its probe services are in `/private/tmp/claude-501/-Users-ionel-work-personal-emag/0f37fc47-1c0a-42ed-bd77-f0a6f96a2ead/scratchpad/p1e/routes/`.

Read the whole of `tools/goinspect/routes.go`, `route_handlers.go` and `fingerprints.go` at HEAD, and every file they call. Read the Python that turns the reply into `Route` values (`src/arde/adapters/language/go/`, `src/arde/application/inventory_builder.py`, the `Route` model in `src/arde/domain/`).

## What to hunt for

The danger is a success path that lies:
- a route with the wrong path or method
- a route missing with no stop and no `dropped` line
- an anchor on a function whose code doesn't decide the route's responses, when a better one was plain
- a hash that doesn't move when code the route runs changes

A stop on a common, honest shape is also a finding, because it makes ARDE useless on real services. Rate it by how common the shape is in real `net/http` code.

1. **Probe each class for a member it misses.** For each rule above, write two or three shapes the reviewers didn't try that belong to the same class, and see whether they stop, hash or anchor as the rule says. Ideas, not a list to finish:
   - writes through a pointer to the parameter, or through a `switch` that reassigns it
   - a wrapper stored in a map of funcs
   - a handler type reached through an embedded struct, or a generic type
   - a factory returning a concrete type whose `ServeHTTP` is on the value receiver
   - middleware that calls `next` inside a closure it returns from a nested func
   - a handler expression holding a package-level constant, a typed constant or an `iota` value
2. **Is the guard right?** When two or more routes resolve to one anchor and their handler expressions differ, every route in the group gets `handler_id` None. Group F nulls the whole group, so a plain handler at two paths loses its anchor when a third route wraps it differently. How common is that in real services, for example the same handler at `/x` and `/x/` with one behind auth? Is there a rule that keeps honest sharing and still stops two operations from merging into one comment? Is the reason the report gives a person enough to act on?
3. **What now stops that shouldn't?** Group F made net/http's own `Handle` and `HandleFunc` registrars too, so `add := mux.HandleFunc` and a mux behind an interface stop. Try the shapes real services use: a `Router` interface in front of `*http.ServeMux`, a route table of structs `{method, path, handler}` looped over and registered, `http.Handle` on the default mux, and a test helper that registers routes in a `_test.go` file. Does each give an honest route list or a clear stop, and is the next action one a person can follow?
4. **Over-reporting cost.** Every method of a module type handed to an interface now joins the roots. Find a service shape, such as one big `server` struct with many methods passed as a handler, where every route's hash moves on any edit to any method. Say how bad that would be for change detection, and whether the rule should be narrower.
5. **Base comparison.** Group F reports 28 differences against `722902e`, all asked for. Rerun it on `shop` and a catalog-poc copy, and say whether you agree.

## How to work

- Build goinspect from HEAD into your own folder with `cd /Users/ionel/work/personal/emag/arde/tools/goinspect && go build -o <your folder>/goinspect .`.
- Write probe services under `/private/tmp/claude-501/-Users-ionel-work-personal-emag/0f37fc47-1c0a-42ed-bd77-f0a6f96a2ead/scratchpad/p1f/routes/`, standard library only. Run `GOPROXY=off`.
- Calling ARDE's adapter through `uv run python` from the repo root shows what the `Route` values become.
- Check what a probe really serves with curl when the answer matters.
- For any catalog-poc check, work on a copy: `cp -R /Users/ionel/work/personal/emag/catalog-poc <scratch>/catalog-copy && rm -rf <scratch>/catalog-copy/.git`.
- Reproduce every finding with a real run, and give the exact input and output. Mark anything you only reasoned about as reasoned.

## Rules

- `/Users/ionel/work/personal/emag/arde/.env` holds a real API key. Never read, print, copy, source or mount it. Nothing here calls the API.
- `/Users/ionel/work/personal/emag/catalog-poc` is read-only, and no git command runs in it.
- Don't edit anything in the arde repo, don't commit, don't push. Write only under the scratchpad folder above.
- No clones, fetches, installs or third-party code.
- Never spawn a subagent.

## Report

Return it as text, under 1,500 words; you can't write report files. For each finding:
- severity (Critical, Important, Minor)
- the rule it breaks
- the exact input
- what goinspect or ARDE printed
- what it should have printed

Then cover what you checked and found right, and what you didn't check.

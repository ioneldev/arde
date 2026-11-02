# Phase 1: Reading Go

**Status:** in progress

This spec says what this phase delivers and how we know it's done. The reasoning lives in the [design](../superpowers/specs/2026-11-01-arde-design.md), and the step-by-step tasks live in the [plan](../superpowers/plans/2026-11-01-arde.md). The plan's "Amendments from the Phase 1 scan" section overrides its task text.

## Progress

| Task | What | Status |
|---|---|---|
| 6 | `goinspect`: compare and declarations | Done |
| 7 | `goinspect`: patterns and call graph | Done |
| 8 | `GoAdapter` and the `shop` fixture | Done |
| 9 | Service runner | Not started |
| Review | Architecture, code, bug hunters, security | Not started |
| Gate | Ionel reviews, understands and tests | Not started |

## Purpose

Give ARDE reliable eyes on a Go repo before any agent uses them. At the end, code can list a service's routes, params, errors and response fields, say which declarations changed, and start the service on a free port. None of this calls the API, so the phase costs nothing to run.

## What it delivers

- **`tools/goinspect`:** a small Go helper with four commands, all printing JSON.
  - `compare` says which files changed, ignoring comments, formatting and the two struct tags ARDE adds.
  - `declarations` hashes every top-level declaration and lists struct fields.
  - `match` applies the repo profile's patterns to find routes, params, errors and env vars.
  - `fingerprints` hashes everything each route's handler can reach.
- **`GoAdapter`:** the Python side, behind the `LanguageAdapter` interface. It holds every Go idiom, including the directive check and the build, vet and test commands.
- **`tests/fixtures/shop`:** a small `net/http` service that tests can build and run.
- **Service runner:** starts a binary on a free port, waits for the port, and always stops it, even on Ctrl-C or an error.

## Rules for this phase

- Only `adapters/language/go/` and `tools/goinspect/` know about Go. The architecture tests enforce this.
- Child processes get an allow-listed environment, so the API key can't reach them.
- `catalog-poc` is read, never changed.
- ARDE knows nothing about any target repo. Repo-specific facts come only from Discovery's profile, from code reading the repo, or from the wiki. An architecture test fails if product code or a prompt names `catalog-poc` or one of its identifiers.
- A silent skip is a defect. If a file can't be read or a route can't be understood, the tool says so or stops. Only `compare` may over-report, and it never misses a change.
- `LanguageAdapter` holds only methods that have an implementation. Four more arrive with Tasks 14, 15 and 18.

## How we know it's done

1. `make check` passes.
2. `make integration` passes, including `gofmt`, `go vet` and `go test` on `goinspect`.
3. On `catalog-poc`, `goinspect match` finds the 3 routes, 3 params and all the error calls the spike found. The same code works on `tests/fixtures/shop`, which shares no names or layout with it, using only a different profile.
4. A comment-only edit changes no hash, and a one-token edit changes the right one.
5. Editing one function changes the fingerprint of the routes that reach it and no others.
6. A service that ignores SIGTERM is still stopped, and nothing is left running after Ctrl-C.
7. All four phase reviews are finished, with findings resolved or brought to Ionel.
8. Ionel can explain why each `goinspect` command exists, and why comments alone can't be trusted.

## How to test it yourself

```sh
make check
make integration
cd tools/goinspect
go run . match --patterns ../../docs/research/spike/data/stdlib.json ../../../catalog-poc
go run . declarations ../../../catalog-poc
go run . fingerprints --roots index,product,healthz ../../../catalog-poc
```

The walkthrough will add a worked example: edit a copy of `catalog-poc`, then watch which hashes change.

## Not in this phase

Agents, the workspace and anything that writes to a repo. Those start in Phase 2. Comment rendering and `swag` are Tasks 14 and 15.

## Known limits

- The call graph is static, so it misses calls through interfaces and function values. Declaration hashes and stored requests cover that gap.
- Only `net/http` is tested. A `gin` pattern can match and keeps its method, but paths stay in `gin`'s `:id` form and router group prefixes aren't resolved.
- Embedded struct fields aren't listed as response fields.

## Open items

None.

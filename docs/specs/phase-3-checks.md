# Phase 3: Checks that prove the run

**Status:** built (cut scope); waiting for Ionel's gate

This spec says what this phase delivers and how we know it's done. The reasoning lives in the [design](../superpowers/specs/2026-11-01-arde-design.md), and the step-by-step tasks live in the [plan](../superpowers/plans/2026-11-01-arde.md). The plan's "Scope for finishing on 2026-11-03" section cuts this phase to three tasks.

## Progress

| Task | What | Status |
|---|---|---|
| 18 | Agent-edit and behaviour checks | Done |
| 19 | Contract checks: spec, wiki and the live service, with the grounding rule | Done |
| 21 | Repair loop, one round | Done |
| 20, 22, 23 | Docs checks, blind judge, write-back | Moved: 20 folds into 19 where cheap; 22 and 23 go to DESIGN.md as "one more week" |

## Purpose

Prove that a run changed no behaviour and invented nothing, and give the agents one chance to fix what a check finds. These are the checks the brief asks about: what is checked deterministically, and what happens when a check fails.

## What it delivers

- **Agent-edit check:** every line the run added to Go source is a rendered line, or an allowed tag. Anything else stops the run, because no agent can repair it.
- **Behaviour check:** `go build`, `go vet` and `go test` pass on the output, and the same requests get the same responses before and after.
- **Contract check:**
  - Every route, param and field in the spec exists in the inventory, and every inventory item is in the spec.
  - Each documented enum value, boundary and default is requested from the running service.
- **Repair loop:** findings an agent owns go back to that agent once. Code findings stop at once.

## How we know it's done

1. `make check` and `make integration` pass.
2. Each check has a test that proves it fires, on a fixture change built for that purpose.
3. An invented route, param or field fails the run.
4. The first real run passes every check, or the report names each failure.

## Not in this phase

The blind judge, write-back to a local repo, and the docs checks beyond what the contract check covers. These are in DESIGN.md's "one more week".

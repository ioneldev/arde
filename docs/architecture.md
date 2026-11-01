# Architecture

This is the short map. The reasoning and the measurements are in the [design](superpowers/specs/2026-11-01-arde-design.md).

**Status:** Phase 0. Only the quality gate and the docs wiki exist. The rest is the design that later phases build, so read it as intent until the code lands.

## The orchestrator is code

Python code runs a fixed sequence of steps. Agents work inside it only where the next action depends on what they find. Every agent is the same generic loop, configured with a system prompt, a tool list, a submit tool with a validator, and limits. Validation errors from the submit tool go back to the model as tool results, which is how an agent corrects itself. See [decision 0001](decisions/0001-orchestrator-is-code.md).

| Step | Kind | Sees | Tools |
|---|---|---|---|
| Discovery | Agent | Repo files | list files, read file, grep, submit profile |
| Explore | Agent | Repo files, profile, inventory, change report, the running "before" service | list files, read file, grep, HTTP probe, submit facts |
| Docs | Agent | Wiki facts only, never code | submit section |
| Classifier | One model call | The diff plus code measurements | None, structured output |
| Judge | Agent | Docs, spec and the running "after" service only | predict and call, finish |

## A run

```
First run:
  resolve source -> copy into workspace -> baseline build, vet, test
  -> discovery writes profile -> code validates it
  -> explore builds wiki -> render comments and tags -> swag
  -> docs agent writes sections -> code checks (fail: back to the owner, 2 repair rounds)
  -> blind judge -> write back -> save snapshot -> commit + trace

Later runs:
  resolve source -> copy -> compare with snapshot
    no changes     -> stop, no LLM calls
    no real change -> no LLM calls; re-render, regenerate spec, run code checks
    real change    -> change report -> classifier -> stale facts -> explore updates them
                   -> render -> swag -> docs for stale sections -> checks -> judge
                   -> write back -> save snapshot -> commit + trace
```

A run stops on any of these:

- the turn limit of an agent (defaults: discovery 15, explore 30, docs 8 per section, judge 30)
- the cost cap (default $10 per run)
- two repair rounds used, or the same findings repeating

A failed run exits non-zero, leaves the user's repo untouched, keeps the old snapshot and writes a report.

## Layers

Each layer is a folder under `src/arde/`, created by the task that first needs it. Until then this page names the layers and does not point at paths.

| Layer | Holds |
|---|---|
| domain | Frozen Pydantic models and pure rules: facts, inventory, findings, the stale rule |
| application | The pipeline, the agent roles, and the ports (interfaces) they need |
| checks | One file per verification check |
| adapters | The LLM client, the Go language adapter, the workspace, the service runner, the tracer |
| cli | The command line. It wires adapters into the application |

Imports go inward. Two rules are enforced by `tests/test_architecture.py`:

- The domain imports neither adapters nor application code.
- The application imports no adapter. It reaches the outside world through ports.

The same test file keeps Go-specific code out of everything except the Go language adapter. A small Go helper, `goinspect`, sits next to it for AST work and the call graph. The orchestrator talks to Go only through the `LanguageAdapter` interface, so another language could be added later.

## What the LLM may and may not do

It may discover how a repo is built, explore what a service does, write documentation prose from facts, label a change, and judge usability.

It may not write a comment or a spec line, pick a shell command, or touch a file. Code renders every annotation from facts, `swag` generates the spec, and code checks every rule. See [decision 0003](decisions/0003-code-enforces-rules.md).

## Where run state lives

Each target repo gets one workspace holding its profile, wiki, last snapshot and a folder per run with the trace and logs. Deleting the workspace means the next run is a full first run. The terms are in the [glossary](glossary.md).

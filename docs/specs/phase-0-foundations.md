# Phase 0: Foundations

**Status:** in progress

This spec says what this phase delivers and how we know it's done. The reasoning lives in the [design](../superpowers/specs/2026-11-01-arde-design.md), and the step-by-step tasks live in the [plan](../superpowers/plans/2026-11-01-arde.md).

## Progress

| Task | What | Status |
|---|---|---|
| 1 | Repo skeleton, `make check`, docs wiki | Done |
| 2 | Domain models | Done |
| 3 | Tracer, budget, model config | Done |
| 4 | LLM port, fake client, agent loop | Not started |
| 5 | Anthropic client | Not started |
| Review | Architecture, code, bug hunters, security | Not started |
| Gate | Ionel reviews, understands and tests | Not started |

## Purpose

Build everything the later phases stand on, with no Go and no real repo involved yet. At the end, an agent can run in a loop against the real Claude API, with its cost and every step recorded.

## What it delivers

- **Quality gate:** `make check` runs `ruff`, `mypy` and `pytest`, the same for people, agents and CI. `make integration` and `make eval` run the slower tests.
- **Docs wiki:** `AGENTS.md`, `docs/index.md`, architecture, glossary, limitations and decisions, with tests that fail on a broken link or a path that doesn't exist.
- **Architecture tests:** they fail if the domain or application layers import an adapter, or if Go-specific code appears outside the Go adapter.
- **Domain models:** frozen Pydantic types for findings, the repo profile, inventory, facts, changes and the run report.
- **Errors:** every error the pipeline reports carries a next action and an exit code.
- **Trace and logs:** `trace.jsonl` and `debug.log`, with the API key redacted.
- **Budget and models:** a cost cap, totals per role, and a model setting per role.
- **Agent loop:** one generic loop of about 40 lines that every agent reuses.
- **Anthropic client:** the real API behind an interface, with a fake for tests.

## Rules for this phase

- Dependencies point inward: adapters may use application and domain code, and never the reverse. Pricing and `Usage` live in the domain, and `Tracer` is a protocol in `application/ports.py`.
- Models: Opus 5.5 for discovery, explore, docs and judge, at `high` effort. Haiku 5.5 for the classifier, at `low` effort.
- Limits: discovery 15 turns, explore 30, docs 8 per section, judge 30, and a $10 cap per run.
- A description can't contain a line break, so a fact can never smuggle in a directive.
- A model that ends its turn without submitting is re-prompted, never treated as finished.

## How we know it's done

1. `make check` passes.
2. `make eval` passes one real Haiku call with a tool.
3. Each architecture test is shown failing on a real violation.
4. A trace from a test run has no API key in it.
5. All four phase reviews are finished, with findings resolved or brought to Ionel.
6. Ionel can explain `run_agent` line by line.

## How to test it yourself

```sh
make check
make eval
```

Then open the newest `trace.jsonl` a test produced, and read each event.

## Not in this phase

Anything that reads Go, runs a service or touches a real repo. Those start in Phase 1.

## Open items

- A writable repo mount could let repo code tamper with the host repo. This must be resolved in Task 28, before the container ships.

# Phase 2: Thin first run

**Status:** built; waiting for Ionel's gate

This spec says what this phase delivers and how we know it's done. The reasoning lives in the [design](../superpowers/specs/2026-11-01-arde-design.md), and the step-by-step tasks live in the [plan](../superpowers/plans/2026-11-01-arde.md). The plan's amendment sections override its task text, and its "Scope for finishing on 2026-11-03" section says what is built today.

## Progress

| Task | What | Status |
|---|---|---|
| 10 | Source resolution and workspace | Done |
| 11 | Agent tools: read-only repo tools and a local HTTP probe | Done |
| 12 | Discovery agent and profile validation | Done |
| 13 | Explore agent, fact validation and wiki store | Done |
| 14 | Comment and tag renderer | Done |
| 15 | Spec generation with `swag` | Done |
| 16 | Docs agent, reference tables and README link | Done |
| 17 | First-run pipeline, CLI and report, then the first real run | Done |
| Review | One scoped pass over Phases 2 and 3 (R133, R134) | Done: 3 findings fixed (R138) |
| Gate | Ionel reviews, understands and tests | Guide written: [walkthrough](../decisions/phase-2-walkthrough.md) |

## Purpose

Run ARDE end to end once, on a repo it has never seen, and get a documented copy back. One command resolves the source, learns the repo, explores the running service, annotates the handlers, generates the OpenAPI file, writes the docs and checks the result. Everything stays in ARDE's workspace: nothing touches a user's repo until Task 23.

## What it delivers

- **`uv run arde --repo <url or path> [--branch B]`:** the CLI, with `--json`, `--max-usd` (default 10) and exit codes 0 to 4.
- **The workspace** under `ARDE_HOME` (default `~/.arde`): an input copy and an output copy per run, the profile, the wiki, the docs sections and a snapshot saved only after success.
- **Discovery:** an agent that writes the repo profile, validated by building and running the service.
- **Explore:** an agent that reads the code and probes the running service, and submits facts. Code accepts a fact only with evidence: a quote that exists in the file, or a probe that was really recorded.
- **Output:** swag annotations rendered by code from facts, `docs/swagger.json` and `swagger.yaml` from `swag`, and project docs written from facts only.
- **`report.json` and `trace.jsonl`** for every run, with cost, time and turns per role.
- **`docs/decisions/first-real-run.md`:** what the first real run on `catalog-poc` cost and found.

## Rules for this phase

- Agents never get shell or file-write tools. Their tools read the repo copy and send GET or HEAD to the local service only.
- Every model-chosen path goes through one guard, and a link out of the repo stops the run.
- Repo text reaches a prompt only through `clean_repo_text()`, bounded.
- Code writes every comment and tag. The model supplies facts, never source.
- ARDE knows nothing about any target repo. Prompts ask about classes of behaviour. A miss on the first real run changes the classes, and never adds that repo's fact to a prompt.
- `.env` is never read by an agent. The key loads into the CLI as a `SecretStr` and is redacted from every file ARDE writes.
- The pipeline catches `ArdeError` alone, and every error names the next action.

## How we know it's done

1. `make check` and `make integration` pass.
2. The end-to-end test on `tests/fixtures/shop` passes with scripted model replies: the output builds, its tests pass, the spec is valid, and a branch exists in the workspace clone.
3. Bad input exits 2 before any paid call: no key, no repo, no `go.mod`, an unknown branch.
4. A baseline that fails its own checks stops before any paid call.
5. One real run on `catalog-poc` completes under $10, and `first-real-run.md` records its cost, time and turns per role.
6. That run's docs are checked by hand against the design's Section 11 list, and every miss is recorded.
7. The scoped review pass is finished, with findings resolved or brought to Ionel.

## How to test it yourself

```sh
make check
make integration
uv run arde --repo https://github.com/mihai-iorga-emag/catalog-poc --branch main
```

The run prints its run folder. Read `report.json`, then the wiki, the annotated handlers, `docs/swagger.json` and the docs in the output copy.

## Not in this phase

Agent-edit, contract and docs checks, the repair loop and the blind judge (Phase 3). Write-back to a user's repo (Task 23). Later runs and change detection (Phase 4). The container (Phase 5).

## Known limits

- Routes, params and fields come from Phase 1's inventory, with its limits in [limitations](../limitations.md).
- A route with no function gets facts and docs, but no swag block. The report names each one.

## Open items

None.

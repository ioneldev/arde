# Phase 2 and 3 walkthrough

This guide covers what was built on 3 November to finish the project in one day: Phase 2 (the first run) and the parts of Phase 3 that check it. It says what changed from the plan you approved, how to run it, and what was measured. The reasoning behind each piece is in `docs/architecture.md`, and the measured runs are in [the first real runs](first-real-run.md).

## What was built

`uv run --env-file .env arde --repo <url or path> [--branch B] [--json] [--max-usd N]` runs end to end.

| Step | Where | What it does |
|---|---|---|
| Source and workspace | `adapters/source.py`, `workspace.py` | Clones a URL or copies a local repo into `~/.arde`, never touching the source. A local copy is hardened against links that leave the repo, git's fsmonitor and case-colliding names. |
| Baseline | `GoAdapter.run_checks` | The repo's own build, vet and tests. A red baseline stops before any paid call. |
| Discovery | `application/agents/discovery.py` | Reads the repo with read-only tools and submits the profile. Code checks it by extracting the inventory. Saved and re-validated on later runs. |
| Inventory | Phase 1's `goinspect` | Routes, params, errors, response fields and env vars, by code. `net/http`'s own param readers need no pattern. |
| Explore | `application/agents/explore.py` | Reads code and probes the running service with GET and HEAD, then submits facts in batches. Code accepts a fact only with a quote that holds code in a build file, or a recorded probe, and every response status needs its own evidence. |
| Render and spec | `adapters/language/go/renderer.py`, `swag.py` | Code writes every annotation and tag from facts, then swag v2.0.0-rc6 writes swagger.json and swagger.yaml into the service's docs folder. |
| Docs | `application/agents/docs_writer.py` | One agent call per section, from facts only. Code renders the API reference and the error table, and adds one README link. |
| Checks | `src/arde/checks/` | Agent edits are only rendered lines, build, vet and tests pass, responses are the same before and after, routes are unchanged, the spec, wiki and inventory match both ways, the live service is tested at every enum, boundary and default, and the docs' URLs and commands are grounded. |
| Repair | `application/repair.py` | Findings go back to the agent that owns them, one round. Code findings stop at once. |
| Commit and report | `pipeline.py`, `cli.py` | A URL run commits on `arde/run-<id>` in the workspace clone and never pushes. `report.json` and `trace.jsonl` go in the run folder. |

## How to test it yourself

```sh
make check
make integration
uv run --env-file .env arde --repo https://github.com/mihai-iorga-emag/catalog-poc --branch main
```

Then read the printed run folder's `report.json`, and look at the commit with the `git show` command the run prints. To rehearse the interview, copy `catalog-poc`, add a query param or a response field, commit it in the copy, and run `arde --repo <the copy>`. A local run commits nothing; its output copy is the result.

## What was measured

| Check | Result |
|---|---|
| `make check` | 3019 passed |
| `make integration` | 416 passed, with goinspect's Go tests |
| First run on `catalog-poc` | $1.96 in 409 s, every check passed, no repair |
| Re-run with no change | $0.48 in 74 s, no Discovery call |
| Re-run after a new param and field | $1.17 in 206 s |
| Hostile fixture, real models | Nothing planted reached a fact, annotation, spec line or page, and all four plants were reported |
| The docs against the design's Section 11 list | Every behaviour found except the worked 7041.98 example, whose rule is stated |

## Decisions taken without you

1. **Finish today (R134).** Built: Phases 2's tasks, Task 18, Task 19 with Task 20's core, Task 21 with one round, and the deliverables. Moved to DESIGN.md's "one more week": the blind judge, write-back to a local repo, the skip rule and classifier of Phase 4, and the container. A re-run still works incrementally, because the fingerprints and declaration hashes from Phase 1 feed the stale rule.
2. **The container was cut, then built (R134, R140).** It was cut to finish in one day, and built the next night when you asked why. `docker compose run --rm arde --repo <url>` is the one command, and it was the first run of ARDE on Linux.
11. **The blind judge was added (R139).** At your request, as a gate after the 7 code checks. Its first real run predicted 20 replies from the docs alone, all right, for $0.29.
3. **Review policy (R133).** One review pass and one fix round per phase. Phases 2 and 3 got one scoped review, whose three serious findings were fixed (R138).
4. **Prompt injection (R135).** Your request, built as four layers: prompts that treat repo text as data and report it, evidence that must hold code from the service's build, docs grounding for URLs and commands, and a hostile fixture with a real-model eval.
5. **Parser hardening stopped at a heuristic (R136).** The docs grounding check reads Markdown by hand. Further gaps the automated security review found are written in `docs/limitations.md` instead of being chased.
6. **Overview and change guide rest on every fact.** The third real run had neither, because Explore tagged no fact with their topics.
7. **Default param patterns (R138).** A param read in `net/http`'s own spellings needs no pattern from Discovery. The demo rehearsal showed a new `category` param missed without them.
8. **Each response status needs its own evidence (R138).** One quote no longer backs every status.
9. **Real runs past three (R137).** The plan allowed three runs within $30. There were six real runs and one eval, $9.29 in all.
10. **Parallel worktrees.** Tasks ran in parallel worktrees with no per-task review, and the controller checked each merged tree's gates. Every merge conflict was resolved by hand and checked.

## Open gaps

- `Header.Get` on a response the service reads as a client is listed as a param, and a request held in a struct field is not read.
- One edited line in a busy handler stales most facts linked to it, so a re-run can cost more than the change needs.
- A first run's freshly discovered profile is not started before Explore, so a wrong port variable exits 1 at Explore rather than 3.
- A re-run always makes one Explore call, even when nothing changed.
- Nothing has run on Linux, and there is no container.

## Process slips

- Commit `b96930f` went in before `make check` had passed, because a `;` instead of `&&` let the commit run. `74eaedf` fixed the comment the architecture test caught.
- The first hostile eval failed on a test bug: it counted the service's recorded replies as ARDE's output. The test was fixed in `1b0e246`, and the run itself had passed every check.

## Decisions for you

1. **Merging and pushing.** `main` is still at Phase 0. `phase-1` and `phase-2` are stacked branches, and `catalog-poc-docs` holds the deliverable commit. Nothing is pushed.
2. **The container.** Build it, or leave the one command as `uv run`.
3. **The demo workspace.** `~/.arde` holds the state of the rehearsal, whose snapshot includes the rehearsal's `category` change. Delete `~/.arde/workspaces/catalog-poc-*` before the interview for a clean first run, or keep it for a fast re-run.

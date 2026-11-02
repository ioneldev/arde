# Phase 0 walkthrough

Phase 0 is built, reviewed and ready for your gate. This page says what exists, why it's written that way, how to test it, and what's left.

## What was built

| Piece | Where | What it does |
|---|---|---|
| Quality gate | `Makefile` | `make check` runs `ruff`, `mypy` and `pytest`. `make eval` runs the live tests |
| Docs wiki | `AGENTS.md`, `docs/index.md` | Rules, architecture, glossary, limitations, decisions, phase specs |
| Domain models | `src/arde/domain/` | Frozen types for facts, profile, inventory, findings, the run report, errors and pricing |
| Agent loop | `src/arde/application/agent_loop.py` | The one loop every agent reuses |
| Budget | `src/arde/application/budget.py` | The cost cap and totals per role |
| Ports | `src/arde/application/ports.py` | The interfaces application code depends on |
| Tracer | `src/arde/adapters/tracer.py` | `trace.jsonl` and `debug.log`, with the key redacted |
| Anthropic client | `src/arde/adapters/llm/anthropic_client.py` | The real API behind the `LLMClient` port |
| Test guard | `tests/conftest.py` | Scrubs the API key from pytest reports |

Size: about 1,900 lines of source and 5,400 lines of tests.

## How `run_agent` works

This is the function to know line by line. It takes an `AgentSpec` and returns the submitted value, or raises `AgentStopped`. There's no third outcome, so a caller can't mistake an unfinished agent for a finished one.

1. Each turn starts by asking the budget whether another call is allowed.
2. It asks the model, and every paid call is traced and charged.
3. If the model refused or the context window is full, the agent stops with a named reason.
4. If the model called tools, they run and their results go back in one message. A submit that passes validation returns its value, and the loop ends. A rejected submit returns to the model as a tool error, which is how an agent corrects itself.
5. If the model called no tools, or its reply was cut off, the loop nudges it. Three such turns in a row stop the agent as `no_progress`.
6. If the turn limit runs out, the agent stops as `turn_limit`.

## Why it's written this way

- **An agent returns a value or raises** ([0010](0010-agent-contract.md)). Reviewers showed the first version could treat an agent as finished by accident, because acceptance was the default state.
- **Code bugs stop the run.** Only a validation error goes back to the model. Anything else is `ToolCrashed`, so a bug can't burn 30 paid turns while the model works around it.
- **Every request streams** ([0011](0011-stream-every-request.md)). Non-streaming calls stop at about 21,333 `max_tokens`.
- **Validated data, not trusted data.** Names that reach generated source have strict shapes, and paths must stay inside the repo, so text from a repo can't become a directive or a file read outside it.
- **Dependencies point inward.** The domain imports nothing from the other layers. `tests/test_architecture.py` enforces this and other rules.

## How to test it yourself

```sh
make check
make eval
```

`make eval` costs about $0.05 and prints the folder holding each run's `trace.jsonl` and `debug.log`. Open one and read the events: each model call shows tokens, cost, latency and stop reason.

Then read `src/arde/application/agent_loop.py` from `run_agent` down.

## What was measured

| Check | Result |
|---|---|
| `make check` | Passes: 836 tests, plus `ruff` and strict `mypy` |
| `make eval` | 8 of 8 live tests pass, for about $0.044 |
| Thinking echo | Opus 5.5 accepts it on a second turn |
| Prompt caching | Real hit on Opus 5.5: turn 2 read 2,204 cached tokens |
| Strict tools | The API refuses them for `DiscoveredProfile` and `Wiki`, so those tools use `strict=False` |
| API key | Not in the repo, the git history or any run file |

## What the reviews found

| Review | Critical | Important | Minor |
|---|---|---|---|
| Architecture | 0 | 6 | 5 |
| Code | 0 | 5 | 8 |
| Silent failures | 2 | 5 | 5 |
| Bug hunt | 0 | 8 | 4 |
| Security | 0 | 6 | 4 |

Many findings overlapped. They became one fix list of 41 items. A re-review verified 40 and found two new Important issues, which a short follow-up fixed. A final re-review passed all three. Everything is in `docs/reviews/phase-0/`.

The worst findings were a code bug being fed to the model as if it were a correction, and a paid call that failed being recorded nowhere. Both are fixed and were re-measured.

## The key leak

During Task 5, a failing live test printed about 87 of the key's 106 characters into one agent's transcript. It isn't in the repo or git history. The cause is fixed, `tests/conftest.py` now scrubs pytest reports, and the architecture tests restrict who may read the key.

You chose to rotate the key later. It's an open item in the [phase spec](../specs/phase-0-foundations.md), and I'll remind you before publishing.

## What's still untested

- No refusal has been seen live, so that path is proven offline only.
- A broken stream's usage is proven offline. What the live API sends in a broken stream is inferred.
- Nothing has run on a real repo yet. That starts in Phase 2.
- `make integration` has nothing to run until Phase 1.

## Left open on purpose

The remaining minor findings are in `docs/reviews/phase-0/deferred.md`. None blocks Phase 1. The most useful are N6 (warnings and test titles bypass the key guard) and N7 (the architecture test misses some ways of reading the key). Both need a further mistake before they matter.

Later-phase risks went into the plan's "Amendments from the Phase 0 review" section, including the writable container mount.

## Decisions for you

1. **Refusal fallback:** left out, because the Python form needs the beta client, and a refusal already stops with a named reason. The refusal category is now recorded, so the first real run will show whether Opus refuses source code. If it does, we add the fallback.
2. **Exit codes:** a bad key exits 2, and a mid-run outage exits 1.
3. **Cost cap:** set with `--max-usd`, default 10, added in Task 17.
4. **Moving on:** the work is on branch `phase-0`, not pushed. When you're happy, tell me to continue. Merging to `main` and pushing are your call.

## Every ruling made during Phase 0

Each says what was decided, why, and what it costs if wrong.

- R1. `Usage`, `ModelCall` and `RoleTotals` live in `domain/usage.py`; `cost_usd` and the price table live in `domain/pricing.py`; `Tracer` protocol lives in `application/ports.py`, with `JsonlTracer` in adapters — the plan's locations would fail its own architecture test, and the spec puts pure rules in the domain — if wrong, a few imports move.
- R2. Add `domain/errors.py` with `class ArdeError(Exception)` carrying `next_action: str` and `exit_code: ExitCode`; `ProviderError`, `BudgetExceeded`, `SourceError`, `ServiceStartError`, `DiscoveryFailed` subclass it, and the pipeline catches `ArdeError` only — application can't import adapter exceptions, and this gives one place for "errors name the next action" — if wrong, small refactor.
- R3. ruff has `extend-exclude = ["docs/research"]`; pytest `testpaths = ["tests"]`; wiki tests cover AGENTS.md, README.md, docs/*.md and docs/decisions/*.md only, excluding docs/superpowers and docs/research. Docs may only name paths that already exist — the plan and spec describe future paths on purpose — if wrong, broken links in those two files go unchecked.
- R4. `make eval` runs `uv run --env-file .env pytest -m eval -q`. No subagent may print or read `.env` — the key must never enter a transcript — if wrong, the eval needs one flag changed.
- R5. Anything application needs from adapters is a Protocol or plain type in `application/ports.py`, wired only in `cli.py`; applies from Phase 1 onward and to be restated in each dispatch — this keeps the plan's own architecture test true — cost if wrong: more interfaces than strictly needed.
- R6. Task 1 skips `git init` and the docs commit, already done in db74103 with `.gitignore`, `.gitattributes` and origin set; `.gitattributes` forces LF, because the user's global `core.autocrlf=true` would otherwise check out CRLF source — if wrong, `.gitattributes` is removed.
- R7. Effort defaults are `high` for discovery, explore, docs and judge, `low` for the classifier; `max_tokens` 16000 for agents and 1024 for the classifier — the spec requires explicit effort but sets no value, and 16000 is the skill's non-streaming default — if wrong, one YAML edit after the first real run.
- R8. Work happens on branch `phase-0` in the main checkout rather than a linked worktree — a worktree would need a copy of `.env`, and Ionel tests in this folder at each gate — if wrong, `git worktree` can still be added.
- R9. The SDD scripts are CRLF on this machine and fail in bash, so LF copies run from the scratchpad; the plugin is left untouched — if wrong, nothing is affected.
- R10. Security review of `docs/research/spike/container/` flagged (a) the API key sharing a container with repo code and (b) a writable `${REPO_PATH}:/repo` mount that repo code could use to tamper with the host repo, including `.git/hooks`, plus a GOCACHE volume shared across repos. Those files are throwaway and stay unchanged. (a) is the spec's accepted non-goal for trusted repos. (b) is new: carry it into Task 28, which must not leave repo code able to write the host mount, for example by running repo code only on the copy under a user or mount that can't reach `/repo`, or by applying write-back through a validated patch. Add both to `docs/limitations.md` and raise with Ionel at Review gate 0 — if wrong, a malicious repo could plant a hook that runs on the host.
- R11. docs/specs/*.md stay in the wiki's link and index checks, but skip the "backticked repo path exists" check — Ionel wants phase specs in the repo, written before a phase starts, so they must name future paths, while broken links in them should still fail — if wrong, a stale path in a spec goes unnoticed until the phase review.
- R12. QualifiedTypeName may stack `[]` and `*` (`[]*Product`) — real handlers return that shape and the characters can't inject anything — if wrong, one character changes in `_TYPE_PREFIX`.
- R13. `model_context_window_exceeded` stops as `max_tokens`, with the raw value kept in the model_call trace — re-prompting would resend a full context window at real cost, and the stop is still a named reason — if wrong, the stop reason is misleading about the remedy, since raising max_tokens won't help.
- R14. Refusal fallback is left out for now — the Python shape needs the beta client, Haiku 5.5 has none, and a refusal already stops the agent with a named reason — if wrong, a refused request ends a run that a fallback could have finished. Tell Ionel at the gate.
- R15. The --showlocals minor at anthropic_client.py:136 joins fix round 2, against the rule that minors stay out of the loop — it's the same key exposure in the same lines, and costs nothing extra in a round already open — if wrong, one extra line in the fix diff.
- R16. The adapter streams every request and builds structured output on the same path through `output_config.format`, with validation done after the reply — this removes the 21,333 ceiling, the only string match on an SDK message, and the paid-but-unrecorded parse failure — if wrong, the adapter needs a live re-check and stub rewrite.
- R17. The run_agent contract changes now: Accepted/Rejected, SubmitTool, AgentStopped, config on the spec, and only pydantic ValidationError returns to the model — acceptance must not be the default state, and code bugs must stop the run — if wrong, Phase 0 tests are rewritten once more.
- R18. A bad key, 401, 403 and 404 exit 2. A mid-run outage stays exit 1, as the plan's test says — Review Focus 4 calls bad input usage, and an outage isn't — if wrong, one mapping table changes.
- R19. The cap is set by `--max-usd`, default 10, added at Task 17; the BudgetExceeded next action names it and the amount spent — the design set no way to raise the cap — if wrong, one flag is renamed.
- R20. Explore submits through add_facts plus finish_facts, and max_tokens becomes a re-prompt; model_context_window_exceeded stays terminal with its own stop value — one all-or-nothing submit risks losing a paid run — supersedes R13 — if wrong, Task 13 changes shape.
- R21. load_models moves to an adapter, removing the deviation deferred 26 would have recorded — AGENTS.md says application reaches the world through ports — if wrong, one import moves.
- R22. Live tests are allowed in this fix round only after the test guard exists — streaming needs real proof, and Ionel chose to rotate the key later — if wrong, a further leak is possible, which the guard is there to stop.
- R23. Unknown value types in the tracer keep a str() fallback rather than raising, against silent-failure finding 9 — tracing must not crash a run — if wrong, a generator is logged as its repr.
- R24. One narrow follow-up fix for the re-review's open item, N1, N2 and the dropped refusal category, beyond the plan's "one fix round" — they're small, Phase 2 depends on them, and leaving money unrecorded and bugs disguised as outages would break the design's rules — if wrong, one extra commit and one small re-review.

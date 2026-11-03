# Phase 1 review brief

ARDE is an orchestrator that documents any Go service, with no knowledge of that service. It's an interview take-home: its author must be able to explain every line and decision live, and the interviewers will re-run it after changing the service.

Phase 1 built ARDE's eyes on a Go repo. Nothing in it calls the API. Read `AGENTS.md` first, then `docs/specs/phase-1-reading-go.md`. The design is `docs/superpowers/specs/2026-11-01-arde-design.md`, and the plan is `docs/superpowers/plans/2026-11-01-arde.md`, whose amendment sections override its task text. Known limits are in `docs/limitations.md`.

## What to review

The repo is `/Users/ionel/work/personal/emag/arde`, branch `phase-1`, at `f145fe7` plus nothing. Phase base is `2fbd052`.

- `tools/goinspect/`: a Go helper with four JSON commands
- `src/arde/adapters/language/go/`: `GoAdapter` and its parts
- `src/arde/adapters/child_process.py`, `child_env.py`, `service_runner.py`
- `src/arde/application/ports.py`, `src/arde/domain/`
- `tests/fixtures/shop/`, `tests/adapters/`, `tests/test_architecture.py`

## What has already happened

Each task had its own adversarial review and fix rounds. The reports are in `/Users/ionel/work/personal/emag/arde/.superpowers/sdd/2026-11-01-arde/` as `task-N-review.md` and `task-N-rereview*.md`, and the rulings are in `progress.md` there, R25 to R80. Don't re-raise what they settled. Your value is what a task-sized view can't see: how the parts fit, what's true of the whole, and what the next phases will trip on.

Settled, so don't reopen:
- `is_comment_only` is an allow-list by ruling R67, and open-ended attacks on it are closed by R74.
- The call graph is static.
- SIGTERM and SIGHUP to ARDE belong to the CLI at Task 17.
- `FieldFact` and same-named structs belong to Task 13.
- Repos must be trusted for now, and containers come at Task 28.

## Rules

- Read-only on the checkout. Don't mutate the working tree, index, HEAD or branches. Scratch work goes in `/private/tmp/claude-501/-Users-ionel-work-personal-emag/0f37fc47-1c0a-42ed-bd77-f0a6f96a2ead/scratchpad/p1/<your-name>`.
- Never read, print, copy or mount `/Users/ionel/work/personal/emag/arde/.env`, or any API key.
- `/Users/ionel/work/personal/emag/catalog-poc` is read-only, and no git commands run inside it. Work on a copy made with `cp`.
- Never spawn a subagent.
- Back each finding with file:line, and with input and output when you ran something. State what you measured and what you inferred. Don't report a finding you can't back.

## Severity

- **Critical:** ARDE gives a wrong result without saying so, or a secret or the host is at risk.
- **Important:** can't be trusted until fixed, or Phase 2 will be built on something wrong.
- **Minor:** polish, or coverage that could be broader.

## Output

Begin directly with the findings, most severe first, then what you checked that held. Finish with a one-line verdict. Also say which plan tasks need changing, with the exact change.

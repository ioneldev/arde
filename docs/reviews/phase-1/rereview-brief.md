# Phase 1 bounded re-review brief

ARDE is an orchestrator that documents any Go service, with no knowledge of that service. It's an interview take-home: its author must explain every line and decision live, and the interviewers will re-run it after changing the service.

Phase 1 built ARDE's eyes on a Go repo. Five reviews read it at `f145fe7`. Four fix groups then changed it. This re-review checks those fixes. It's bounded: you check what the fixes claim and what they newly built, not the whole phase again.

Read `AGENTS.md` first, then `docs/specs/phase-1-reading-go.md`. The design is `docs/superpowers/specs/2026-11-01-arde-design.md` and the plan is `docs/superpowers/plans/2026-11-01-arde.md`, whose amendment sections override its task text. Known limits are in `docs/limitations.md`, deviations in `docs/deviations.md`.

## Inputs

All in `/Users/ionel/work/personal/emag/arde/.superpowers/sdd/2026-11-01-arde/`:
- The five reviews: `phase-1-review-architecture.md`, `-code.md`, `-silent.md`, `-bugs.md`, `-security.md`, and `task-9-rereview.md`.
- `phase-1-fix-list.md`: what each group was asked to do.
- `phase-1-fix-a-report.md` to `phase-1-fix-d-report.md`, plus the follow-up's `phase-1-fix-d2-brief.md` and `phase-1-fix-d2-report.md`: what each group says it did. Treat every claim in them as a claim to check.
- `progress.md`: rulings R81 to R106. A finding a ruling settled is settled. Say so and cite the ruling, but do check that the code does what the ruling says.

The repo is `/Users/ionel/work/personal/emag/arde`, branch `phase-1`, at `5298e7b`. The fixes are `f145fe7..5298e7b`.

## Settled, so don't reopen

- `is_comment_only` is an allow-list (R67), and open-ended attacks on it are closed (R74).
- The call graph is static.
- SIGTERM and SIGHUP to ARDE belong to the CLI at Task 17.
- Same-named structs belong to Task 13.
- Repos must be trusted for now, and containers come at Task 28.
- Third-party code is not compiled or run here (R95). Don't clone, fetch or install anything.

## Rules

- Read-only on the checkout. Don't mutate the working tree, index, HEAD or branches. Scratch work goes in `/private/tmp/claude-501/-Users-ionel-work-personal-emag/0f37fc47-1c0a-42ed-bd77-f0a6f96a2ead/scratchpad/p1r/<your-role>`. Build fixtures there, or use an export of the tree (`git archive 5298e7b | tar -x -C <dir>`).
- Never read, print, copy or mount `/Users/ionel/work/personal/emag/arde/.env`, or any API key. Nothing here calls the API.
- `/Users/ionel/work/personal/emag/catalog-poc` is read-only, and no git command runs inside it. Work on a copy made with `cp -R`.
- Never spawn a subagent.
- Back each finding with file:line, and with input and output when you ran something. State what you measured and what you inferred. Don't report what you can't back.

## Severity

- **Critical:** ARDE gives a wrong result without saying so, or a secret or the host is at risk.
- **Important:** can't be trusted until fixed, or Phase 2 will be built on something wrong.
- **Minor:** polish, or coverage that could be broader.

## Output

Return the report as text (the harness refuses report files from subagents). Begin with new findings, most severe first. Then your role's table. Finish with a one-line verdict, and which plan tasks need changing, with the exact change.

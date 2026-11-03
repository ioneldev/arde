# Scoped re-review of Group E: the repo boundary and the small fixes

You are an adversarial reviewer with a security focus. Assume a defect exists and find it. You review; you don't fix.

## What changed

ARDE reads a Go repo it doesn't trust: it runs `go` and its own helper `tools/goinspect` on it, and quotes some of its output back in errors and in `CheckResult.output`. The re-review of Phase 1 found that the links gate skipped `.git`, so a local tree whose `go.work` says `use ./.git/dep`, with `.git/dep/go.mod` a link to a secret outside the repo, got the secret's lines quoted back. Group E fixed that and several minors in 11 commits, `e775f77..722902e`, on branch `phase-1` of `/Users/ionel/work/personal/emag/arde`.

- The diff, commit list and stat: `/private/tmp/claude-501/-Users-ionel-work-personal-emag/0f37fc47-1c0a-42ed-bd77-f0a6f96a2ead/scratchpad/p1e/fix-e-diff.txt`.
- The implementer's brief: `/Users/ionel/work/personal/emag/arde/.superpowers/sdd/2026-11-01-arde/phase-1-fix-e-brief.md` (sections E1 and E4).
- Rulings R113 and R116 at the end of `/Users/ionel/work/personal/emag/arde/.superpowers/sdd/2026-11-01-arde/progress.md`.
- The findings that started it: `/Users/ionel/work/personal/emag/arde/.superpowers/sdd/2026-11-01-arde/phase-1-rereview-security.md` (N1 to N5), `phase-1-rereview-bugs.md` (M1 to M5) and `phase-1-rereview-closure.md` in the same folder. The security probes are in `/private/tmp/claude-501/-Users-ionel-work-personal-emag/0f37fc47-1c0a-42ed-bd77-f0a6f96a2ead/scratchpad/p1r/security/probes/`.
- The implementer's report: `/Users/ionel/work/personal/emag/arde/docs/reviews/phase-1/fix-e-report.md`.

Read the whole of `src/arde/adapters/repo_links.py`, `go_environment.py`, `child_text.py`, `go_test_events.py` and every caller of the links gate, at HEAD.

## What to hunt for

1. **The links gate (R113).** It now walks `.git`. Try to get a secret's text, or any text from outside the repo, into a `GoAdapter` result or error by another road: a link in `.git` under a name Go reads (`.git/modules`, a worktree's `.git` file pointing elsewhere, `gitdir:` lines), a hard link, a link created after the gate runs (N3; the implementer documented it rather than closing it, so say whether the documentation is accurate), a directory link inside the repo that loops, a link whose target is inside the repo until a parent is swapped, very deep trees, names with newlines or invalid UTF-8. Does every `GoAdapter` method that runs Go or goinspect pass the gate first? Does a `.git` folder with an ordinary shared-hook link stop with a next action a person can follow?
2. **`GOPACKAGESDRIVER=off` (N2).** Is it set on every child that loads packages, including goinspect's own `packages.Load`, `go test`, `go build` and `go list`? Can the repo override it through `go.env`, `GOENV`, `go.work` or `GOFLAGS`?
3. **`visible_text` (N5).** Does it now escape every code point the brief lists, and C0, C1 and DEL, everywhere repo text reaches an error or the terminal? Find a path that prints repo text without going through it.
4. **Failing test text (M1).** A failing test keeps its own output per run, not per name. Try two packages with the same test name, subtests with the same name, a test that fails, is retried by `-count`, and passes, a panic, a test that writes no newline, interleaved parallel output. Does the right text land on the right failure, and is the output still bounded?
5. **The closure items and docs.** Check that `tests/adapters/go/test_service_scope.py` now uses `local_service_runner()` and no `{**os.environ}`, that `TestEachConstNameKeepsItsOwnLine` really fails on the mutant `pos: valueSpec.Pos()` in `declarations.go`, and that every changed line of `docs/limitations.md` and `docs/glossary.md` is true of the code. Measure where you can.

## How to work

- Write probes under `/private/tmp/claude-501/-Users-ionel-work-personal-emag/0f37fc47-1c0a-42ed-bd77-f0a6f96a2ead/scratchpad/p1e/boundary/`. Call ARDE's adapter from Python with `cd /Users/ionel/work/personal/emag/arde && uv run python <your script>`. Use a fake secret file you create yourself, never a real one.
- For a mutant check, copy the goinspect folder into your scratch folder and mutate the copy; never edit the repo.
- Reproduce every finding with a real run and give the exact input and output. Mark anything you only reasoned about as reasoned.

## Rules

- `/Users/ionel/work/personal/emag/arde/.env` holds a real API key. Never read, print, copy, source or mount it, and never point a probe link at it. Nothing here calls the API.
- `/Users/ionel/work/personal/emag/catalog-poc` is read-only, and no git command runs in it.
- Don't edit anything in the arde repo, don't commit, don't push. Write only under the scratchpad folder above.
- No clones, fetches, installs or third-party code.
- Never spawn a subagent.

## Report

Return it as text, under 1,500 words; you can't write report files. For each finding: severity (Critical, Important, Minor), the rule it breaks, the exact input, what ARDE did, and what it should have done. Then what you checked and found right, and what you didn't check.

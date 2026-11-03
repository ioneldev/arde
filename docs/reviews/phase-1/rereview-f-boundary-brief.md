# Scoped check of Group F's boundary fixes

You are a reviewer checking four small fixes. Assume each might be incomplete, and try to show it. You review; you don't fix.

ARDE runs Go tools on repos it doesn't trust and quotes some of their output in messages. Group F, commits `28cfb43`, `a79c547`, `c16b715` and `aeb87dd` on branch `phase-1` of `/Users/ionel/work/personal/emag/arde`, fixed four Minors that a security re-review found. That review is at `/Users/ionel/work/personal/emag/arde/docs/reviews/phase-1/rereview-e-boundary.md` (F1 to F4), and its probes are in `/private/tmp/claude-501/-Users-ionel-work-personal-emag/0f37fc47-1c0a-42ed-bd77-f0a6f96a2ead/scratchpad/p1e/boundary/probes/`. Ruling R123 at the end of `/Users/ionel/work/personal/emag/arde/.superpowers/sdd/2026-11-01-arde/progress.md` says what each fix must do. See each commit with `git show <commit>`.

Check each:
1. **`28cfb43`, failing test text.** A test name's text is kept unless every run of it passed. Rerun the interleaved parallel case (`repos/m1/par` in the probes folder, copied into your own scratch folder first). Then try one name failing in one package and passing in another, a subtest failure under a passing parent, and `-count` reruns. Is the right text kept, and is output still bounded?
2. **`a79c547`, one character set.** There must be one definition, in `domain/base.py`, covering every Cc, Cf, Zl and Zp code point. `one_line`, the plain-text types and `adapters/child_text.visible_text` must all use it. Grep `src/` for any other list of control characters, or any path from repo or child text to a message that bypasses them. Check the hardcoded ranges against `unicodedata` yourself. Also check what the plain-text types now refuse that they accepted before, and whether any of that is text a real repo would hold, such as a route path or a struct name.
3. **`c16b715`, `os.lstat` in the gate.** A link in a folder with mode 0444 must stop with `RepoNotReadableError`. Try mode 0000, a file that vanishes between listing and `lstat`, and a very long path. Does each one fail closed, with a message a person can act on?
4. **`aeb87dd`, docs.** Is every changed line in `docs/limitations.md` true of the code?

## Rules

- `/Users/ionel/work/personal/emag/arde/.env` holds a real API key. Never read, print, copy, source or mount it, and never point a probe link at it. Use a fake secret file you create.
- `/Users/ionel/work/personal/emag/catalog-poc` is read-only, and no git command runs in it.
- Don't edit anything in the arde repo, don't commit, don't push. Write only under `/private/tmp/claude-501/-Users-ionel-work-personal-emag/0f37fc47-1c0a-42ed-bd77-f0a6f96a2ead/scratchpad/p1f/boundary/`.
- To call ARDE, run `cd /Users/ionel/work/personal/emag/arde && uv run python <your script>`.
- No clones, fetches, installs or third-party code.
- Never spawn a subagent.

## Report

Return it as text, under 800 words. For each finding give its severity, the exact input, what ARDE did, and what it should have done. Then say what you checked and found right.

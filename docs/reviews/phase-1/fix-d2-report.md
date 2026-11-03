# Phase 1 fix report: Group D follow-up

I've done all three items, one commit each on `phase-1`, and nothing is pushed. All three gates passed on each commit's exact tree, and I matched each commit's tree hash to the tree that was gated. The brief got one thing wrong: there are three expected walk differences, not two. The third is a folder holding only a file behind `//go:build ignore`, and I added it to the test as a named difference (details under item 1).

## Commits and test counts

| Commit | Item | `make check` | `make integration` | Go (top-level `=== RUN`) |
|---|---|---|---|---|
| base `1988f50` | | 1930 | 240 | 207 |
| `8b73d1a` | 1, the two walks agree (R102) | 1930 | 241 | 207 |
| `9cbdb17` | 2, two line tests (R104) | 1930 | 241 | 209 |
| `5298e7b` | 3, keyword-only (R105) | 1930 | 241 | 209 |

How the figures add up:
- **`make check`:** 1930 on every commit. It deselected 248 (240 integration + 8 eval) at base, and 249 (241 + 8) from `8b73d1a` on.
- **`make integration`:** 240 + 1 (item 1's test) = 241. It deselected 1938 (1930 + 8) every time.
- **Go:** 207 + 2 (item 2) = 209.
- **Item 3:** no count changed.
- **Diff `1988f50..HEAD`:** 8 files, +237 −134.

The full logs are in `/private/tmp/claude-501/-Users-ionel-work-personal-emag/0f37fc47-1c0a-42ed-bd77-f0a6f96a2ead/scratchpad/fixD2`: `gate-*.txt`, `check-*.txt`, `int-*.txt`, `go-*.txt`, the RED logs and the probe scripts.

## Item 1: the two folder walks agree

The test is `test_the_checks_walk_passes_over_the_folders_goinspect_passes_over`, in `tests/adapters/go/test_checks_measured.py`. I put it there so it could reuse `repo_of`.
- **The repo:** the root, `orders`, `orders/lines/tax`, `vendor/x`, `testdata/x`, `_skip`, `.hidden`, `nested` (its own `go.mod`) and `nested/sub`. Each holds a file with a declaration.
- **The extra shapes:** `underscored_only/_x.go`, `broken_link_only/x.go` (a link to `missing.go`) and `ignore_tag_only/gen.go`.
- **It first runs `require_no_link_leaves(repo)`,** to show this repo would get past the links gate in a real run.
- **The Go side's folders** are the folders of the files named in the `declarations` reply.
- **It asserts** that goinspect reads `{".", "orders", "orders/lines/tax"}`, which guards against a test that passes because nothing was read. It then asserts that the checks walk lists exactly that plus the three named differences, each with its measured reason in a comment.

It calls `toolchain._go_folders` directly. This is the first ARDE test to reach a private function. The other way in would be to record `go list`'s arguments through `run_checks`.

**RED:** I dropped `"testdata"` from `_is_passed_over`. The test failed (1 failed), with "Extra items in the left set: 'testdata/x'". After `git restore` it passed again.

**What I measured** (Go 1.27.1, in a scratch repo):
- **Links gate:** it admits the broken-link repo, because a link that stays inside is kept (`links_leaving` returns `[]`). So both walks see that shape.
- **`_x.go` only:** Python lists the folder and Go skips it. `go list -e` says "no Go files" and names no left-out file, so `ignored_files` gains nothing.
- **`.x.go` only:** the same result, by the same rule. I measured it but didn't add it to the test.
- **Broken link only:** Python lists the folder and Go skips it. `go list -e` fails to stat the file and names no left-out file. `go build ./...` exits 1 on the link, and exits 0 once the link is removed.
- **Only a `//go:build ignore` file (the third difference):** Python lists the folder and Go skips it. This one is visible, not harmless:
  - `go list -e` names `IgnoredGoFiles: ["gen.go"]`.
  - `toolchain._ignored_files` returned `('ignore_tag_only/gen.go',)`.
  - It is also intended. Showing a file the build leaves out is exactly why `_go_folders` lists folders one by one, as its own comment says.

## Item 2: two line-number tests

`TestEachVarNameKeepsItsOwnLine` and `TestEachFieldNameKeepsItsOwnLine` are in `tools/goinspect/declarations_test.go`. The existing `TestDeclarationLinesAreTheLinesInTheFile` now shares the new `declarationLines` helper. I applied each mutant with Group D's exact text and ran the whole Go suite uncached:
- **Var line taken from `valueSpec.Pos()`:** only `TestEachVarNameKeepsItsOwnLine` failed, with "got map[first:4 second:4], want map[first:4 second:5]".
- **Field line taken from `field.Type.Pos()`:** only `TestEachFieldNameKeepsItsOwnLine` failed, with "got map[High:5 Low:5], want map[High:5 Low:4]".

Both pass without their mutant, and `declarations.go` was restored byte for byte.

## Item 3: keyword-only past two positional arguments

**RED first:** I made the nine parameters keyword-only before touching any caller.
- mypy reported 1 error, at `anthropic_client.py:186`.
- The five affected test files gave 309 failed and 210 passed. All 309 failures were one of the helpers' "takes 2 positional arguments but 3 were given" TypeErrors.

**Then the callers:** a script working from the syntax tree named the third argument at all 122 calls. That is 1, 13, 21, 29, 5, 3, 24, 4 and 22, in the brief's order.
- `ruff format` rewrapped the lines that went too long.
- One string in `test_adapter.py:258` was too long to wrap, so I split it with implicit concatenation. The value is unchanged.

**Proof that nothing else changed:** each file's syntax tree equals HEAD's once the keyword parameters and arguments are turned back into positions. A control run without that step reports a difference.

**The list is complete:** an AST scan of `src` and `tests` (leaving out pytest tests, fixtures and dunder methods) found exactly the brief's nine before the change, and none after.

## Rules

I never read `.env`, and nothing called the API. `catalog-poc`, the plan and the design are untouched. I staged only by explicit path, didn't push, and spawned no subagent.

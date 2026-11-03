# Phase 1 fix, Group D follow-up

Three small items from Group D's hand-back (`phase-1-fix-d-report.md` in this folder; read its "Code review 10" and "Mutants" sections first). Branch `phase-1`, work in place. Rulings R102, R104 and R105 are in `progress.md` here.

## 1. The two folder walks agree (R102)

`run_checks` finds folders with its own walk, `_go_folders` in `src/arde/adapters/language/go/toolchain.py`, because it must give a red result on a repo that doesn't parse, and `goinspect declarations` exits 1 there. `goinspect` decides which files it reads in `tools/goinspect/walk.go`. Both are kept. Add one integration test that builds a repo in `tmp_path` and drives both walks over it, then asserts they choose the same folders.

- Shapes, each holding at least one ordinary `.go` file with a declaration in it unless the shape says otherwise: the root, a sub-package, a deep sub-package, `vendor/x`, `testdata/x`, `_skip/`, `.hidden/`, a folder holding a nested module (its own `go.mod`), and a sub-package of that nested module.
- Expected differences, named in the test as such, each with the measured reason in a comment: a folder holding only `_x.go`, and a folder holding only a broken link, which Group D measured as listed by Python and skipped by Go. Measure both again; if the links gate refuses the broken-link repo first, say so and keep only what can be driven.
- The Go side's folder set comes from what goinspect already replies. For example, the folders of the files named in the `declarations` reply, when each file holds a declaration. No new goinspect command, no new reply key.
- Show the test can fail: change one skip rule in `_is_passed_over` (for example drop `testdata`), see the test fail, restore it. Put that RED in the report.

## 2. Two line-number tests (R104)

Group D's mutant run left two survivors that predate it: the line goinspect gives a `var` declaration, and a struct field, when the names sit on several lines. The mutants are in `/private/tmp/claude-501/-Users-ionel-work-personal-emag/0f37fc47-1c0a-42ed-bd77-f0a6f96a2ead/scratchpad/fixD` (search for the mutant scripts or list). Add the tests in Go, in the existing test files for declarations. RED is the mutant applied: each new test fails with its mutant and passes without it. Report both.

## 3. Past two positional arguments, keywords (R105)

Make every parameter past the second keyword-only (`*,`), and update every caller:

- `src/arde/adapters/llm/anthropic_client.py:431` `_reply_broke_the_schema(schema, error, response)`
- `tests/test_architecture.py:387` `import_violations(source_root, layer, forbidden)`
- `tests/test_architecture.py:499` `write(root, relative, text)`
- `tests/test_docs_wiki.py:275` `write(root, relative, text)`
- `tests/adapters/test_service_runner.py:994` `assert_the_log_is_refused(runner, config, started)`
- `tests/domain/test_inventory.py:170` `model_with(model, field, value)`
- `tests/adapters/go/test_adapter.py:46` `edit(path, old, new)`
- `tests/adapters/go/test_adapter.py:366` `response_fields_of(adapter, shop, source)`
- `tests/adapters/go/test_adapter.py:1188` `hashes_of(adapter, repo, routes)`

Pytest test functions and fixtures are left alone, because pytest passes those by name. `__exit__` keeps its protocol shape. No test count may change from this item.

## Rules

- `.env` holds a real API key. Never read, print, copy or source it. Nothing here calls the API.
- `catalog-poc` is not needed. Don't touch it.
- Stage by explicit path, never `git add .` or `-A`. Don't push. Don't spawn subagents.
- One commit per item. Before each commit, run on the exact tree: `make check`, `make integration`, and `cd tools/goinspect && go test -count=1 ./...`. Keep full logs in `/private/tmp/claude-501/-Users-ionel-work-personal-emag/0f37fc47-1c0a-42ed-bd77-f0a6f96a2ead/scratchpad/fixD2`, never only the last line. Commit only after all three pass.
- Comments explain why only. Names read as prose.
- Commit messages end with exactly:

```
Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01VB5t5JjLH1p2Fxw9cdNURk
```

## Report

Return it as text (the harness refuses report files from subagents). Status, commits, test counts before and after for all three gates with the breakdown that sums to each, the two REDs from items 1 and 2, what you measured about the expected differences, and anything you found that the brief got wrong. Check every figure against its breakdown before you send it.

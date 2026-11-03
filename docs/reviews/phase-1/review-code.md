# Phase 1 code quality review

No Critical findings. 5 Important, 10 Minor. Every finding scored 80 or above. Each one says what was measured and what was inferred.

## Important

### 1. `port_env` is checked on the way out of the profile, not on the way in (confidence 92)

- **Where:** `src/arde/domain/profile.py:40` has `port_env: str`. `src/arde/application/ports.py:204` has `port_env: CodeIdentifier`, under the comment "It comes from a model, so it is checked before it names an environment variable". `main_package` and `main_file` are typed on the profile, and `port_env` is the only model-chosen field that isn't.
- **Measured:** `RepoProfile` accepts `"APP PORT"`, `"PORT=1"`, `"$(rm -rf /)"` and `""`. Each one then fails in `ServiceConfig(...)` with a pydantic `ValidationError`, which is not an `ArdeError` and has no `next_action`.
- **Inferred:** the pipeline catches `ArdeError` alone (plan line 59), so a bad stored or overridden profile crashes Task 17 with a traceback. That breaks "every user-facing error names the next action". Discovery gets a correction only if `ServiceConfig` happens to be built inside its submit handler (`agent_loop.py:314`).
- **Fix:** `port_env: CodeIdentifier` on `DiscoveredProfile`. Keep the check on `ServiceConfig`.

### 2. A bad pattern can't be told from a broken repo, and the error names the wrong pattern (confidence 88)

- **Where:** `src/arde/adapters/language/go/goinspect_client.py:154-162`, `src/arde/adapters/language/go/adapter.py:58`, `tools/goinspect/match.go:89`.
- **Measured, bad regex:** a profile with one pattern, `^app\.handle(`, gives `GoinspectError`, exit 1, with the text `patterns file /var/folders/.../patterns.json: pattern 2 (kind "route")`. It says pattern 2 because `adapter.py:58` puts `DEFAULT_ENV_PATTERN` first. The file it names is already deleted. The next action reads "usually a Go file that does not parse".
- **Measured, broken Go file:** gives the same class, the same exit code and the same next action.
- **Inferred:** Task 12's `test_submit_profile_rejects_invalid_regex` needs a tool error back to the model. `validate_profile` can see only `ArdeError`, so it must either match on message text or send a repo syntax error to the model as if it were the model's mistake. `NoRoutesError` and `RouteNotUnderstoodError` already exit 3, so this is the one profile problem on the wrong exit code.
- **Fix:** goinspect exits 2 on a pattern it refuses. `GoinspectClient.matches` maps that to a `BadPatternError` with `ExitCode.DISCOVERY_NEEDS_INPUT`. `extract_inventory` puts the default pattern last, which is safe because the inventory is sorted (read in `inventory_builder.py`, not run).

### 3. Python re-implements `reflect.StructTag`, and gets it wrong (confidence 90)

- **Where:** `src/arde/adapters/language/go/inventory_builder.py:25` and `:233-241`, with the comment "Reads the value of the json key the way Go's reflect does". Go has its own parser at `tools/goinspect/normalise.go:154` and has `reflect` available.
- **Measured on exact bytes:** for the tag `json:"café,omitempty"`, `json_name` comes out as `café` with a literal backslash, and `reflect` gives `café`. For `json:"a\"b"`, Python keeps the backslash and `reflect` gives `a"b`.
- **Severity:** this is strictly a silent wrong result, feeding the docs tables at Task 16. It is Important and not Critical because only a tag with a backslash escape reaches it. It is the same bug class as R62, which moved literal decoding into Go, and tags were left behind.
- **Fix:** `structField` gains `json_tag`, read with `reflect.StructTag(text).Lookup("json")` and null when absent. `_TAG_ENTRY` and `_json_tag` go.

### 4. The route key is private to the adapter, and three later tasks need it from layers that can't import it (confidence 85)

- **Where:** `src/arde/adapters/language/go/adapter.py:98-99`, `_route_key`. The port documents the format in `ports.py:179` as text only.
- **Measured:** this is the only place in `src` that builds the key.
- **Inferred:** Task 13's enum, Task 24's "missing from `route_fingerprints` counts as changed" and Task 25's removal rule all need it, in application or domain code, where the architecture test forbids importing an adapter. Each would retype the f-string. If they drift, a lookup miss reads as every route changed, and a link miss means a fact doesn't go stale.
- **Fix:** add a `key` property to `Route` in `src/arde/domain/inventory.py`, and have the adapter call it. Its text is the `RoutePattern` form, so it reuses that name in its comment.

### 5. Nothing can list or check a declaration id (confidence 80, inferred)

- **Where:** the format is built only at `adapter.py:55`, inside the private `_declaration_hashes`. `LanguageAdapter` exposes only the diff, `changed_declarations`. `Inventory` holds no ids, and `Links.declarations` in `domain/facts.py` is a free `tuple[str, ...]`.
- **Measured:** the plan calls this "the declaration id used across ARDE" (line 397), and Task 13 lists tests for invented routes, params and fields but none for declarations (lines 724-726). `FactContext` is named at line 709 and defined nowhere.
- **Inferred:** the model must type text in the `file::kind name` form with no list to choose from, and a wrong id marks no fact stale. Design line 233 still sends the changed declaration to Explore as uncovered, so the change is seen, but nothing forces that fact to be revisited. That is the case declarations exist for (design line 250).
- **Fix:** `Inventory.declarations: tuple[str, ...]`, filled in `extract_inventory` from the reply it already reads at `adapter.py:61`.

## Minor

### 6. Two Go error classes with one meaning (confidence 88)

`errors.py:7` and `goinspect_client.py:25` have the same body and the same exit code, and differ only in a parameter named `message` or `problem`. Measured: no `except` in `src` names either one, so nothing depends on the split. The `errors.py:1` docstring, "The error every Go command raises", is untrue twice: `go build` of the helper raises `GoinspectError` (`goinspect_client.py:100`), and a Go command that times out raises `ChildTimeoutError`.

Fix: move it to `errors.py` as `class GoinspectError(GoCommandError)` with no body, which keeps plan line 92 true, and reword the docstring.

### 7. `ChildStartError` blames the program for a missing folder (confidence 85)

`child_process.py:129-130` says it names the file so "a missing folder would [not] be blamed on the program", and the next action on the same error does exactly that. Measured with `cwd=/no/such/folder`: "Cannot start `/bin/echo`: No such file or directory: /no/such/folder." then "Install /bin/echo or put it on PATH". `service_runner.py:115-118` handles the same case correctly.

Fix: name both causes there too, using `command.cwd`.

### 8. "Fingerprint" and "hash" are the wrong way round in Go (confidence 85)

The glossary (`docs/glossary.md:25`) defines a fingerprint as the route-level hash. In the code:
- A declaration's `Hash` comes from a function named `fingerprint` (`normalise.go:96`, `declarations.go:106`).
- A route's `Fingerprints[root]` comes from `hashFunctions` (`fingerprints.go:76`).
- `normalisedFingerprint`, `syntaxFingerprint` and `constMeaning.fingerprint` follow the first sense.

The author has to explain this live.

Fix: all are private, so rename mechanically to `hashOfDump`, `normalisedHash`, `constMeaning.hash`, `normalisedSyntaxHash` and `fingerprintOf`.

### 9. One idea, several names; one name, several ideas (confidence 82)

- **"pattern" means three things.** `ExtractionPattern` (Go: `patternSpec`, `callPattern`), `RoutePattern` (`"METHOD /path"`, `domain/base.py:88`), and the raw registered text (`inventory_builder.py:105`, `_split_pattern`). `USE_A_PATH_PATTERN` at `inventory_builder.py:43-46` uses "route pattern" in both senses in one message. Fix: call the text a registered path.
- **`call` means a regex and a callee.** `ExtractionPattern.call` and `callPattern.call` are regexes, while `CallMatch.call` and `callMatch.Call` are callee text, and `inventory_builder.py:114` shows the rename with `callee=match.call`. Fix: `CallMatch.callee`, and JSON `callee`.
- **`root` and `roots` are unrelated in one signature.** `GoinspectClient.fingerprints(self, root, roots)` at `goinspect_client.py:138`: the first is a folder and the second is function names. Go uses `dir, roots`. Fix: `repo, roots`.
- **The same wire types pair badly.** `compareReport`/`CompareReply`, `fingerprintReport`/`FingerprintsReply`, `structField`/`FieldReply`, `declaration`/`DeclarationReply`. The last two aren't replies. Fix: use Go's names on the Python side (`StructField`, `Declaration`, `*Report`).

### 10. What `./...` skips is written in both languages (confidence 80)

`toolchain.py:188-193` repeats `walk.go:41-48` and `:116-119`: `vendor`, `testdata`, `_` and `.` prefixes, and nested `go.mod`. They agree today (read, not fuzzed). Each is tested against its own copy (`test_adapter.py:380`, `walk_test.go`), so a change to one fails no test on the other, and that policy changed under R43, R44 and R58.

Fix: add a `folders` list to the `declarations` reply, walked with the same policy minus the ignore-tag flag, and delete `_folders_with_go_files` and `_is_passed_over`.

### 11. Constants defined twice (confidence 85)

- `go_environment.py:22-34` lists the three names of `USER_SETTINGS` (`:42`), and `:59` filters them back out. The comment at `:20`, "Passed on when exported", is therefore not how they travel. Fix: drop them from `TOOLCHAIN_VARIABLES` and drop the filter. The test at `test_go_environment.py:104` parametrizes over `(*TOOLCHAIN_VARIABLES, *USER_SETTINGS)`.
- `timeouts.py:12`, `EXTRA_TEST_COMMAND_TIMEOUT_S = 600`, says it "gets as long as the tests do" but retypes the number from `:6`. Fix: `= GO_TEST_TIMEOUT_S`.
- The comprehension `{name: os.environ[name] for name in NAMES if name in os.environ}` appears at `child_env.py:14`, `go_environment.py:56-60` and `:88`. Fix: one `exported(names)` helper in `child_env.py`.

### 12. Comments and docs that later fixes left untrue (confidence 85 or above each)

| Line | Says | True state |
|---|---|---|
| `src/arde/adapters/child_process.py:1`, `tests/adapters/test_child_process.py:1` | "with the allow-listed environment" | The caller has supplied it since `8510654` (`ChildCommand.env`, `:39-41`). The first version did call `child_environment()` |
| `tools/goinspect/walk.go:16-17` | "judged by name alone" | Two fields below read a `go.mod` and file contents, added in `fae7c2d` and `27bb9bf` |
| `tools/goinspect/normalise.go:41-43` | The "one normaliser" that compare and declaration hashes share | Only `compare.go:68-69` calls `normalisedFingerprint`. The shared step is `normaliseTree`, so move the sentence there |
| `tools/goinspect/compare.go:19-21` | "Two kinds of comment are code", and "Task 8 treats... a file that imports "C"" | There are now three, with example output, and the rule is any file holding the text `"C"` (`directives.py:60-64`). It is also the only plan-task reference in product code |
| `src/arde/domain/inventory.py:44-45`, `docs/glossary.md:13` | Dropped "because an argument was not a string literal" | Measured dropped lines also read "is an empty string" and "`status` is missing" |
| `tools/goinspect/match.go:123` | "write {} for a call whose arguments do not matter" | `profile.py:26` refuses `{}` for all four kinds since R61 |
| `docs/glossary.md:5` | No space "is how every directive is written" | The same entry names `// Output:`, and `directives.py:15-17` lists the spaced forms |
| `docs/specs/phase-1-reading-go.md:39` | "Only `compare` may over-report" | `docs/limitations.md:39` and `:41-43` list fingerprints over-reporting, and `:48` the comment-only rule |

### 13. Duplicated test helpers (confidence 85)

- `pair` (`test_comment_only.py:32`) and `written_as_files` (`test_directives.py:135`) are the same five lines. Fix: a method on `Edit` in `doubtful_edits.py`.
- `SHOP` is defined at `shop_support.py:7` and `test_service_runner.py:45`, and the shop profile is rebuilt by hand at `test_service_runner.py:200`. Fix: move `shop_support.py` to `tests/` beside `process_support.py`. It sits below `pythonpath = ["tests"]` today, which is why the runner test couldn't import it.
- `free_port`, `wait_for_port` and `started` (`test_adapter.py:479-510`) are a hand-rolled runner from before Task 9, with one caller at `:525`. `test_service_runner.py:210` proves the same thing with the real runner. Fix: use `local_service_runner()`, which removes about 30 lines.
- `GO_ENVIRONMENT` and `a_known_go_environment` appear at `test_goinspect_client.py:60-66` and `test_toolchain.py:51-57`, with the same comment, differing only in `PATH` and the patched module.
- `test_child_env.py:19` defines `TOOLCHAIN_VARIABLES` with the same name as the product constant and two extra entries. `test_go_environment.py:31` retypes `ALLOWED_VARIABLES` by hand.

### 14. The `go env` cache leaks stand-in values into later tests (confidence 92)

`read_the_user_file_afresh` (`test_go_environment.py:86`) clears the process-wide cache before each test and not after the last one.

Measured: a probe test run after that file sees `GOPRIVATE`, `GONOPROXY` and `GONOSUMDB` all `corp.example/*`. The same probe alone sees them empty. Collection order puts the two `shop` tests in `test_service_runner.py` after that file in `make integration`, so `build_service` runs with a test's values. It passes only because `shop` has no dependencies.

Fix: make the fixture `yield`, and clear on both sides.

### 15. Past two positional arguments, in Go (confidence 85)

Phase 1's Python product code holds the rule. Go doesn't: `matchesInFile(root, path, patterns)` at `match.go:140` and `skipsDirectoryAt(path, name, policy)` at `walk.go:110`. Test helpers are worse, with four same-typed strings at `fingerprints_hash_test.go:19,27,35` and `fingerprints_test.go:56`, and four strings at `test_adapter.py:48` and `test_inventory_builder.py:46`.

`fingerprints.go:149` is forced by `packages.Config` and is exempt. `run(args, stdout, stderr)` at `main.go:35` is the usual testable-main shape, so record it in `docs/deviations.md`.

Fix: a method on `walkPolicy`, and on `fileMatcher` or a small struct.

## What held

Measured on a clean `git archive` of `f145fe7`, with Go 1.27.1 and the repo's `.venv`. I confirmed `arde` loaded from the export.

- **Fast suite:** 1374 passed in 46 s with `go` off `PATH`, so AGENTS.md's "need no Go" is true. Two fork-race tests take 14 s of that, which R78 accepted.
- **Go style:** `gofmt -l` is empty and `go vet` is clean.
- **Boundaries:** `subprocess` appears only in `child_process.py` and `service_runner.py`. `golang.org/x/tools` appears only in `fingerprints.go`. `service_runner.py` shares its group signalling with `child_process.py` and doesn't copy it.
- **Size:** the longest Python function is 24 lines and the longest Go function is `listGoFiles` at 41. Only `run_checks` and `listGoFiles` come near doing several things, and neither reached the threshold.
- **Docs against code:** `LanguageAdapter` has seven methods. The 1 MB, 64 MB, 2000 byte and 5 s limits match their constants, and the R55 timeouts match `timeouts.py`.
- **Allowed tags:** they sit in both `normalise.go` and `directives.py`. That looks like a duplicate, but `test_a_whole_repo_annotated_the_way_arde_does...` drives both sides through a real edit, so I dropped it.
- **Comment rot:** only one plan-task reference and no ruling numbers in product code.

Not run: `go test` and the full `make integration`. Only `test_go_environment.py` ran its integration tests. Settled items were not reopened.

## Plan tasks to change

- **Phase 1 fix round, before Task 10:** findings 1 to 4 are small and belong here.
- **Plan line 168:** `port_env: CodeIdentifier`.
- **Plan line 86, Task 7 patterns:** "a bad regex, an over-long pattern or a negative `args` value exits 2. Other failures exit 1."
- **Plan line 92, Task 8:** add "`GoinspectClient.matches` raises `BadPatternError(ArdeError)` with exit 3 on goinspect exit 2, and the default env pattern goes last."
- **Task 12:** add `test_submit_profile_rejects_a_port_env_that_is_not_an_identifier`, and extend `test_submit_profile_rejects_invalid_regex` to assert that the correction names the model's own pattern number. Also add a test that a repo file that doesn't parse stops the run and isn't sent to the model.
- **Task 13:** define `FactContext`. Build route enums from `Route.key` and declaration enums from `Inventory.declarations`. Add `test_invented_declaration_rejected`.
- **Task 24:** look up `route_fingerprints` with `Route.key`.
- **Task 25:** match removed routes with `Route.key`.

**Verdict:** no Critical findings, but fix Important 1 to 4 before Phase 2 builds on them, and settle 5 in Task 13.

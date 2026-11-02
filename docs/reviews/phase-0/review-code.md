# Phase 0 code review

Scope: `src/arde`, `tests`, `Makefile`, `pyproject.toml`, `AGENTS.md`, `docs/*.md`, branch `phase-0`, 1cb26b0..dc6239c. I read every source and test file in full, and the design and plan sections they implement.

Baseline: 379 fast tests pass, and ruff, ruff format and strict mypy are clean. No code was changed: all experiments ran on a `git archive` copy in the scratchpad, with no `.env`. `make eval` and the live tests were not run.

**Result: no Critical, 5 Important, 8 Minor. Deferred list: 15 FIX BEFORE GATE, 30 LEAVE.**

The code itself is careful: I found no bug in `run_agent`, the budget or the redaction. The Important findings are mostly about what a run leaves behind, and the design's trace contract is where Phase 0 falls short.

## Important

### 1. A run's record cannot show what an agent did

`src/arde/application/agent_loop.py:62`, `:176-183`; `src/arde/adapters/llm/anthropic_client.py:194-196`

Design section 14 says `debug.log` holds "full prompts and responses". I ran `run_agent` with the real `JsonlTracer` and a scripted model. What was written:

| Content | In trace.jsonl or debug.log? |
|---|---|
| First system and user prompt | Yes |
| Tool result | First 200 characters only |
| Model's reply text | No |
| Tool input (which file, which probe, what was submitted) | No |
| Re-prompt reminder | No |
| Why the agent stopped (`no_progress`, `turn_limit`, `refused`) | No |
| A failed API status call | No: `_log_failure` skips every `APIStatusError` |

`LLMResponse.text` is computed by the adapter and read by no production code, which is the visible symptom.

This bites at Task 17 step 5, "Fix prompts until it completes". When an agent stops with `no_progress`, there will be nothing to read. The brief also grades trace quality, and a `tool_call` event that doesn't say what was called is weak evidence.

Fix, all in `agent_loop.py` except the last item:
- `debug()` the response text and each tool's input and full output.
- Add the input to the `tool_call` event, shortened through `_summary`.
- Add an `agent_end` event carrying `stop` and `turns`, and add that kind to the `Tracer` docstring.
- In the adapter, drop the `isinstance` guard in `_log_failure`, so every failure logs its kind. No test pins that guard, and no comment says why it exists.

### 2. Gate criterion 4 cannot be met, and the manual test cannot be followed

`tests/adapters/test_anthropic_live.py:75`, `:96-101`; `docs/specs/phase-0-foundations.md:48`, `:59`

The spec says "A trace from a test run has no API key in it" and "open the newest `trace.jsonl` a test produced". The live tests use `RecordingTracer`, so `make eval` writes no `trace.jsonl`. `JsonlTracer` appears in exactly one test file, `test_tracer.py`, and there it only receives hand-written events and a fake secret. No test ever joins `run_agent` to the real tracer.

`assert_key_never_logged` reads `debug_lines` only, so it never looks at the events, and it runs only after a test has succeeded (deferred 35).

Fix:
- Give `Live` a real `JsonlTracer` with `secrets=(key,)`, in a folder that survives the run, and print its path.
- Make the assertion read both files.
- Add one fast test: `run_agent` + `FakeLLM` + `JsonlTracer`, with a fake secret in a tool result, then read both files. That also pins finding 1 and the wiring that nothing covers today.

### 3. `model_call` events carry no cost

`src/arde/application/agent_loop.py:130-137`; `tests/application/test_agent_loop.py:504`

Design section 14 lists "tokens, cost, latency, stop reason", and the phase spec's purpose says "with its cost and every step recorded". The event has no cost, and `test_every_call_is_traced_with_usage_and_latency` pins that incomplete shape with an exact match. A reader of the trace would have to re-derive cost from a price table the trace doesn't contain.

Fix: compute `cost_usd(model, response.usage)` at the top of `_record_model_call` and add it to the event. An unpriced model then raises before the event, which is acceptable once Task 17 checks prices at start-up.

### 4. `max_tokens` has a hard ceiling, and the two errors around it give opposite advice

`src/arde/adapters/llm/anthropic_client.py:28-31`, `:181-185`, `:302-306`, `:331-335`; `src/arde/config/models.yaml:12`

Measured from the SDK: non-streaming calls are refused above 21,333 tokens (`3600 × n / 128000 > 600`). The skill states that `max_tokens` is a hard cap on thinking plus response text, and Opus 5.5 always thinks. `explore` sits at 16,000 at `high` effort. On a first run it must fit every fact, with its evidence, into one `submit_facts` call, because `run_agent` ends at the first accepted submit and Task 13 rejects a batch with coverage gaps.

If that overruns, the agent stops with `max_tokens` and the paid work is lost. The obvious response is to raise the limit, but above 21,333 the error says "Lower max_tokens". `_unparsable_reply` says "Raise max_tokens". No value satisfies both.

That the real run overruns 16,000 is my inference and has not been measured. The ceiling and the contradiction are verified.

`client.messages.stream(...).get_final_message()` would remove this. `inspect` confirms `stream` accepts `cache_control`, `output_config` and `output_format`. The switch would:
- delete the only string match on an SDK message (`_STREAMING_REQUIRED`, the `ValueError` branch and `_max_tokens_too_high`)
- lift the limit to 64,000 or more
- remove deferred 36, because `stream` takes `cache_control` where `parse` doesn't

I checked the signature only, not the behaviour, so `parsed_output` on a streamed reply needs a test. The stub in `test_anthropic_client.py` changes too. If you'd rather stay non-streaming, record the ceiling in `docs/limitations.md` and make the "Lower max_tokens" message state it.

### 5. The next action for a blown budget names a setting that doesn't exist

`src/arde/application/budget.py:25`, `:34`

"Raise max_usd above $10.0000 and run again" is what a user sees on exit 4. No flag, variable or file sets `max_usd` anywhere in the design or plan. The CLI flags are `--repo`, `--branch`, `--commit`, `--dry-run` and `--json`, and `RunRequest` has no such field. The only `--max-usd` in the repo is sample text in `tests/domain/test_errors.py:12`. This breaks the rule that every user-facing error names the next action, because the action can't be taken.

The wording also compounds deferred 25: raising the cap just past the old value fails again on the re-run, because a re-run spends at least as much.

`[NEEDS CLARIFICATION: how does a user raise the cap: a --max-usd flag, ARDE_MAX_USD, or the models file?]`

Fix: decide that now, add it to Task 17, and have the message name the real setting and the amount spent.

## Minor

### 6. A misspelt `effort` passes validation and then blames ARDE

`src/arde/application/models_config.py:20`, `:25-26`

Measured: `effort: hgih` loads without complaint. The call then gets a 400, and the next action says "ARDE built a request the API refuses". The comment at `:25` promises that a misspelt setting is refused before any model is called, which is true of keys but not of values. R7 expects effort to be tuned by YAML edit after the first real run, so this will be typed by hand. Fix: `effort: Literal["low", "medium", "high", "xhigh", "max"]`.

### 7. The adapter refuses to log a message, then shows it to the user

`src/arde/adapters/llm/anthropic_client.py:192-196`, `:280-282`

The comment says a transport error's message can quote the request headers, so only kinds are logged. `_detail` then puts `str(error)` for exactly those errors into the `ProviderError`. It is safe today, because `APIConnectionError`'s own message is the constant "Connection error." and the header text lives on the httpx error chained under it. The comment is imprecise, though, and the safety rests on an SDK detail. An interviewer reading both places will ask. Fix: say "the error chained under it" in the comment, and keep the loopback test as the guard.

### 8. A malformed API key exits 1, not 2

`src/arde/adapters/llm/anthropic_client.py:294-299`; `src/arde/application/ports.py:63`

Review Focus 4 says bad input before any spend exits 2. `_malformed_key` returns a `ProviderError`, which hardcodes `CHECKS_FAILED`, so a key with a trailing newline reports as failed checks. `BAD_KEYS` includes "empty", and no test looks at the exit code. Task 17's missing-key test covers only an absent key. Fix: raise an `ArdeError` subclass with `BAD_USAGE`.

### 9. The `pause_turn` branch cannot run

`src/arde/application/agent_loop.py:91-93`; `tests/application/test_agent_loop.py:291`, `:305`

The API returns `pause_turn` only from its server-side tool loop, and ARDE declares no server tools. That's a conditional, a comment and two tests in the one function you must explain line by line, for a state that can't occur. Without it, the `if` goes and the reminder is always appended. Deferred 32 goes too.

### 10. Sixteen lines rewrite every request for one case

`src/arde/adapters/llm/anthropic_client.py:212-229`

`_with_error_text` and `_error_result_with_text` walk every block of every message on every turn to patch an empty error result. `_tool_result` in the agent loop is the only producer of those blocks, so `content = outcome.content or _NO_ERROR_MESSAGE` when `is_error` is set would do it in one line. A tool that fails silently is useless to any model, so this isn't provider knowledge. The live test at `test_anthropic_live.py:208` also asserts `""` on a value the wire never carries, which goes away as well.

### 11. Mutations no test catches

I applied 63 hand-picked mutations, and 15 survived. Worth closing:

- **Budget cache totals:** `tests/application/test_budget.py:103` charges one call, so replacing the running total with the latest call passes for `cache_read` and `cache_write`. These numbers feed `RunReport.per_role`, and they are how the $3 estimate will get checked. Charge twice.
- **Models file encoding:** removing `UnicodeDecodeError` from `models_config.py:69` passes. Add a Latin-1 file to the tests.

The other survivors need no separate action. Three are handled elsewhere in this report: the failed-tool sibling (item 31), the `debug.log` timestamp (item 19) and the `_log_failure` guard (finding 1). Four are harmless: `timespec`, `ensure_ascii`, a dropped exception type name, and tool order, which the test's alphabetical tools can't distinguish. Two are unreachable `or "unknown"` defaults, needed only for the type. `type(error) is not ValueError` is nearly equivalent, but keep it, because a `ValidationError` quotes the model's text.

Two survivors would mislead a reader:
- `break_long_words=True` is an equivalent mutant, not a weak test. `textwrap` drops the fragment to make room for the placeholder, and I confirmed that at 107 and 300 characters. `break_on_hyphens=False` is the setting doing the work, and its mutant was killed.
- The list branch of `_closed_value` (`src/arde/domain/base.py:108-109`) is unreachable today: `RepoProfile` and `Wiki` schemas put no inline object inside a list. Test it or delete it.

### 12. Tests that cannot fail on their own

These pass, but they add no check beyond a neighbouring test:

- `tests/adapters/test_anthropic_client.py:197`, `:205`, `:213`, `:265`: `test_request_shape` and `test_parse_uses_messages_parse...` compare the whole request dict, so these four can only fail when those do.
- `tests/application/test_budget.py:154`: `Budget(float("nan"))` raises first, so `.charge(...)` never runs, and the ten-million-token call in the name never happens. It's the `nan` case of the parametrized test above it.
- `tests/domain/test_models.py:400`: sets a field and reads it back, which tests Pydantic.
- `tests/domain/test_pricing.py:24`: already covered by the parametrized test below it.

Delete them. Fewer tests here means less to defend.

### 13. `make integration` fails today

`Makefile:10`; `docs/specs/phase-0-foundations.md:25`

The spec lists it as delivered, but no test carries the marker, so pytest exits 5 and make stops. `tools/goinspect` doesn't exist either. Phase 1 resolves it. Say so in the spec, so nobody hits it at the gate.

## Deferred list triage

Numbers are line numbers in `phase-0-deferred.md`.

| # | Item | Verdict | Why |
|---|---|---|---|
| 1 | No `--strict-markers` | **FIX** | One line. A typo'd `eval` marker runs paid tests in `make check` |
| 2 | Per-layer "exists" check | LEAVE | A renamed layer breaks every import first |
| 3 | `label`, `write` duplicated | LEAVE | Two tiny test helpers |
| 4 | Layer and limit lists repeated | LEAVE | All accurate today |
| 5 | `cli` called a folder | LEAVE | Reword when `cli.py` lands in Task 17 |
| 6 | Markdown parser gaps | LEAVE | Checked: the wiki uses none of those forms |
| 7 | Ruff exclusion not in Deviations | LEAVE | `pyproject.toml` already explains it |
| 8 | go-build matches prose | LEAVE | Fails safe |
| 9 | Go-leak scan gaps | LEAVE | Reviews cover the rest |
| 10 | Dict models not deeply frozen | LEAVE | Plan-mandated, nothing hashes them |
| 11 | Other Unicode line separators | LEAVE | Go breaks lines at `\n` only. Recheck at Task 14 |
| 12 | Frozen test on empty list | LEAVE | The domain can't be empty while other tests import it |
| 13 | Test name, dead type-ignore | LEAVE | Cosmetic, test-only |
| 14 | Duplicate-id error has no action | **FIX** | Explore reads this as a correction in Task 13. Every sibling validator gives an instruction |
| 15 | `ExitCode` import order | LEAVE | No cycle exists |
| 16 | `ArdeError` not picklable | LEAVE | Nothing copies errors, and there are no threads |
| 17 | `architecture.md:5` stale | **FIX** | First page read, and false. `README.md:5` too |
| 18 | `RoleTotals` repeats fields | LEAVE | Nesting reshapes the report for no gain |
| 19 | `debug.log` timestamp untested | **FIX** | One assertion. Mutation confirms the test name overclaims |
| 20 | No test that models have prices | LEAVE | Task 17 checks at start-up |
| 21 | Tracer crashes on lone surrogates | **FIX** | Measured: raw `UnicodeEncodeError` ends the run. A non-UTF-8 file name in tool output would cause it in Phase 2. Use `errors="backslashreplace"` in `_append_line`, which I confirmed writes a parseable line. The NaN half can stay |
| 22 | Pydantic models flatten to `str` | **FIX** | Measured: `per_role=budget.totals()` writes strings, and Task 17's `run_end` does exactly that |
| 23 | Event kinds not enforced | LEAVE | Plan-mandated, listed in the docstring |
| 24 | Comment at `models_config.py:56` | LEAVE | Harmless one line |
| 25 | `BudgetExceeded` names the old cap | **FIX** | Same edit as finding 5 |
| 26 | Env and file access in application | **FIX** | One Deviations line. It breaks the AGENTS.md rule with no record, and anyone reading `application/` will ask |
| 27 | `architecture.md` still stale | **FIX** | Same edit as 17 |
| 28 | `OverflowError` on `10**400` | LEAVE | The CLI parses a float, which can't produce it |
| 29 | Accepted submit with no value | LEAVE | Only our own handlers can do it |
| 30 | `submit_tool` not validated | LEAVE | The first `FakeLLM` test of each role catches a typo |
| 31 | Rejected submit with siblings | **FIX** | Mutation: breaking after a failed tool leaves siblings unanswered, the API would 400, and all tests pass. This is the loop's key invariant |
| 32 | Pause test shape | LEAVE | Gone if finding 9 is taken |
| 33 | `ArdeError` leaves no `tool_call` | LEAVE | The run-level report covers it |
| 34 | Dead type-ignore in live test | LEAVE | Already fixed: no longer in the file |
| 35 | `assert_key_never_logged` too narrow | **FIX** | Same change as finding 2 |
| 36 | `parse` sends no `cache_control` | LEAVE | One-shot call. Gone if finding 4 is taken |
| 37 | Failed paid parse not charged | LEAVE | Cents, and the classifier is Phase 4 |
| 38 | `BadRequestError` blames ARDE | **FIX** | One string. The first 400 in Phase 2 is likely a YAML edit or a schema |
| 39 | Weak tests | LEAVE | Now at lines 494, 501, 838. 501 goes with item 43 |
| 40 | `httpx2` undeclared | LEAVE | The lockfile pins it through `anthropic` |
| 41 | Reply dict, `RecordingTracer` copy | **FIX** | Tracer only. Measured: `recording_tracer` won't import when `tests/application` runs alone, so Phase 2 agent tests would add a third copy. Move it and set `pythonpath`. Reply dict can stay |
| 42 | "Not billed" stated as fact | **FIX** | Delete the clause. It's inference beside the word "Observed", and it can't be defended |
| 43 | Comment overclaims on the kept cause | **FIX** | Confirmed: the key is in `cause.request.headers`, under `authorization`, masked in repr. "A server's answer holds no credential" (`anthropic_client.py:309`) is false, and so is the test name at `test_anthropic_client.py:788`. Nothing reads the cause, so prefer `raise failure from None`, which deletes `_cause_that_is_safe_to_keep` and the "for the debug log" test at `:501`. Rewording is the minimum |
| 44 | `parse` cut-off says "Run again" | LEAVE | Fix at Task 26, the first caller |
| 45 | Credit balance as a 400 | LEAVE | The skill's error table says `billing_error` is 402. Item 38 covers the rest |

## Checked and fine

- **Prices:** Opus and Sonnet rates, the 1.25× cache write and the Haiku rate match the `claude-api` skill. Opus cache reads at $0.20 look like a copy of Sonnet's rate, but they are correct. One gap: Haiku 5.5 costs 5× above 100K prompt tokens and the table is flat. That's cents on a classifier prompt, so it only needs a line in `docs/limitations.md` at Task 26.
- **Packaging:** `models.yaml` is in the built wheel.
- **Preserved thinking:** the stand-in text is deterministic, so history stays append-only from the API's view.
- **`run_agent`:** no ordering or message-shape defect found. Refusals and cut-offs stop before any tool runs, every `tool_use` gets a result, and the tripping call is traced before the budget raises.
- **Rules:** no function in `src` takes more than two positional arguments. Comments are "why" apart from deferred 24.

## Verified and inferred

- **Measured:** finding 1's table except its last row, which comes from reading `_log_failure`; the SDK ceiling, `effort: hgih` loading, the surrogate crash, the `RoleTotals` flattening, the key's location on the cause, the `recording_tracer` import failure, the mutation results and the wheel contents.
- **Read from the skill:** `pause_turn` needing server tools, `max_tokens` capping thinking plus text, 402 for billing, and all prices.
- **Inferred, not measured:** that explore will overrun 16,000 tokens (finding 4), and that a Task 17 `run_end` will pass `budget.totals()` directly (item 22, though its test name says per-role totals).
- **Not covered:** the mutation set was hand-picked, not exhaustive, and I didn't call the live API.

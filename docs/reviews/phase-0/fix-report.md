# Phase 0 fix report

Branch `phase-0`, from `98e828f` to `9a68670`. Six commits, one per group, not pushed.

| Group | Commit | Fast tests after |
|---|---|---|
| 1 Test guard | `00d68d5` | 410 |
| 2 Agent loop contract | `9c9eeaa` | 437 |
| 3 Anthropic adapter | `2c2b74f` | 496 |
| 4 Tracer, budget, pricing, models file | `09c0417` | 546 |
| 5 Domain | `db06ec2` | 750 |
| 6 Docs and hygiene | `9a68670` | 752 |

Baseline was 379. Final state: `make check` passes with 752 fast tests, and `make eval` passes 8 of 8. Every item on the fix list is done. Nothing in `docs/superpowers` changed (`git diff 98e828f HEAD -- docs/superpowers` is empty).

RED, GREEN and probe logs are in `/private/tmp/claude-501/-Users-ionel-work-personal-emag/0f37fc47-1c0a-42ed-bd77-f0a6f96a2ead/scratchpad/fix/`.

Beyond the listed tests, I mutation-checked each group, because a test written by the implementer can pass for the wrong reason. In total, 47 hand-picked mutants were applied and 46 killed. The survivor is an equivalent mutant, noted under Group 3. The mutants are hand-picked, not exhaustive.

## Group 1: test guard

**Changed.**
- New `tests/conftest.py`: `KeyGuard` pops the key in `pytest_configure`, scrubs any 16-character window of it from failure reports and captured output, and gives tests the `api_key` fixture (`SecretStr`) and the `holds_api_key` fixture (a yes/no answer about any text).
- `JsonlTracer` takes `tuple[SecretStr, ...]` and keeps secrets only in a compiled pattern, so no frame holds a plain one.
- `--strict-markers`, `pythonpath = ["tests"]`, and `RecordingTracer` is one shared helper at `tests/recording_tracer.py`.
- The architecture test `test_the_plain_key_is_read_only_by_the_adapter_and_conftest` reads the AST of `src` and `tests`.
- The live key check moved into the fixture's teardown, so it runs even when a test fails (item 35).

**Tests.** `tests/test_key_guard.py` runs real failing inner pytest runs through `pytester` with a fake key. Five leak shapes run under six report modes: default, `-l`, `--tb=long`, `--full-trace`, `-vv` and `-rA`. A clean failure keeps its message, and skips and xfails still report.

**RED.** 12 failed, 11 errors, 64 passed. The guard tests errored because `conftest.py` did not exist.

**GREEN.** 13 guard tests pass, 410 overall.

**Proof the guard works.**
- Scrubber disabled: 7 failed.
- `pop` changed to `get`: 1 failed.
- A real `os.environ.get` planted in a test file: the architecture test failed.

**Not covered.** `pytest -s` prints past the reports. The docstring says so. Collection errors are not scrubbed, but the key is popped before collection, so a test module cannot reach it through the environment.

## Group 2: agent loop contract

**Changed.** `src/arde/application/agent_loop.py` is rewritten. `run_agent[T](spec, deps) -> T` has 36 non-blank lines.
- `Accepted[T]`, `Rejected`, `SubmitTool[T]` and `AgentSpec[T]` are frozen dataclasses. A strict mypy probe confirmed `T` flows from `validate` to the return value, and a deliberately wrong assignment was caught.
- `AgentStopped` carries `stop`, `turns` and `last_rejection`, with a different next action per stop.
- `ToolCrashed` wraps everything except `ValidationError` and `ArdeError`, with the traceback in `debug.log`.
- `max_tokens` drops the partial turn, tells the model to send less, and counts toward the idle limit. `model_context_window_exceeded` is terminal, as `context_window_exceeded`.
- The `pause_turn` branch is gone. The empty error result is handled in the loop, and the 16-line rewrite left the adapter.
- `_valid_utf8` cleans lone surrogates from tool text. The tracer writes with `errors="backslashreplace"`.
- `record_model_call` is public, and charges in a `finally`. `model_call` carries `cost_usd`.
- New events: `model_error` and `agent_end`, both in the `Tracer` docstring. A handler's `ArdeError` leaves a `tool_call` with `ok=False`.
- `debug.log` holds model text, tool inputs, full results and re-prompts.
- `trace.jsonl` and `debug.log` exist from the start of a run. The first attempt used `touch()`, which succeeds on a directory, and a test caught that. It now opens the file for append.

**Tests.** `tests/application/test_agent_loop.py` is rewritten against the new contract. It includes the rejected submit with sibling calls (item 31).

**RED.** Collection failed with `ImportError: cannot import name 'Accepted'`, and 4 tracer tests failed.

**GREEN.** 437 pass. Ten of ten mutants killed. One came from my own review: replacing `tool_turn.rejection or last_rejection` with `tool_turn.rejection` passed until I added `test_a_later_tool_turn_does_not_forget_the_last_rejection`.

## Group 3: Anthropic adapter

**Changed.**
- Every request uses `messages.stream(...)` and `get_final_message()`. `_STREAMING_REQUIRED`, the `ValueError` branch and `_max_tokens_too_high` are gone.
- `parse` sends `output_config.format` built with `anthropic.transform_schema`, sends `cache_control`, checks `stop_reason`, validates after the reply, and raises `ParseFailed` carrying `usage`.
- `ProviderError` takes `exit_code`. A malformed key, 401, 403 and 404 use `BAD_USAGE`, and an outage stays exit 1.
- Every raise uses `from None`, and `_cause_that_is_safe_to_keep` is deleted.
- The comment at `:192`, the `BadRequestError` action and the "not billed" clause are fixed.
- `stop_reason` is the Literal `ModelStop`. `ToolDef.strict` has no default.
- `strict_tool_schema` moved to `src/arde/application/tool_schema.py` and now leaves `default`, `const`, `enum` and `examples` alone. The list branch is tested.
- `Live` uses a real `JsonlTracer` under `runs/eval/<stamp>/<test>`, prints the folder, and asserts on both files.
- `make eval` gained `-rP`, because without it a passing test's printed path is never shown, and `-rP` output goes through the guard. `runs/` is gitignored.

**What the probes found.** I ran the real SDK over a mock transport before writing this group, and a straight switch to streaming would have broken in ways no review listed.

| Case | Measured | Effect on a straight switch |
|---|---|---|
| Any streamed reply | The final message has no `_request_id`: `AttributeError` | Every call would crash. `stream.request_id` replaces it |
| HTML, JSON or empty 200 | Bare `AssertionError` | Escapes as a non-`ArdeError` |
| Connection lost mid-stream | Raw `httpx2.ReadError`, `RemoteProtocolError`, `ReadTimeout`, not wrapped | Escapes as a non-`ArdeError` |
| Bad JSON in an event | Raw `JSONDecodeError` | Escapes as a non-`ArdeError` |
| Stream ends early | A message with `stop_reason=None` | Would pass as a reply |

Streamed blocks echo cleanly: the thinking signature, the text and the tool inputs were checked on the wire, and `parsed_output` is not sent.

**Tests.** `tests/adapters/test_anthropic_client.py` now uses real SSE bodies through the real SDK. Thirteen unusable answers run through both `complete` and `parse`. `tests/application/test_tool_schema.py` is new. The fast end-to-end test `test_a_run_through_the_real_tracer_leaves_both_files_with_no_secret` covers item 9.

**RED.** `ImportError: cannot import name 'ParseFailed'`, with 3 collection errors.

**GREEN.** 496 pass. Nine of ten mutants killed. The tenth, `response.text or "{}"`, is equivalent, because `{}` fails the schema anyway, so that was a poorly chosen mutant and not a test gap.

## Group 4: tracer, budget, pricing, models file

**Changed.**
- Tracer: `ensure_ascii=True`, `allow_nan=False`, non-finite floats as text, `BaseModel` through `model_dump(mode="json")`, and summaries cut by character after redaction, in the tracer. An empty secret set is refused. Run folders are 0700 with `exist_ok=False`. The loop's `_summary` is gone.
- Budget: `refuse_if_spent()`, called by the loop before each request. The docstring states the one-call overshoot.
- Pricing: Haiku 5.5's long-prompt rates at $0.50/$2.50, chosen from input plus cached tokens, as the live page says. The cache-read comment is updated.
- `BudgetExceeded` names `--max-usd` and the amount spent.
- `load_models` and `ModelsFileError` moved to `src/arde/adapters/models_file.py`. `RoleConfig` stays in application. `effort` is a Literal, the model must be in `PRICES`, limits are strict integers, duplicate keys are refused, `ValueError` and `RecursionError` are caught, and a set-but-empty variable is an error.

**Tests.** `tests/adapters/test_tracer.py` is rewritten, and `tests/adapters/test_models_file.py` moved from application. Added: charge twice for cache totals, a Latin-1 file, the `debug.log` timestamp. The code 12 tests are deleted.

**RED.** 31 failed in the tracer, budget, pricing and loop files, plus a collection error for the moved module.

**GREEN.** 546 pass. Sixteen of sixteen mutants killed. Three old tests needed changing because they used Haiku at one million prompt tokens, which now correctly lands on the higher rate.

## Group 5: domain

**Changed.**
- `DiscoveredProfile` and `RepoProfile` are split. `main_package` is a `MainPackage` that refuses a leading hyphen.
- `RepoRelativePath` is used for `CodeQuote.file`, `Links.files` and `main_file`.
- `SingleLine` and the name types refuse control characters and U+FEFF.
- `ProbeCitation` goes in `evidence`, and `Wiki.probes` holds `ProbeRecord`, with a validator that every citation resolves.
- `go_type` is renamed to `declared_type`, and `schema_version` is added.
- The duplicate-id error names its next action.
- The architecture test gains a `checks` rule, bans `arde.cli` from the inner layers, and covers `go vet`, `go test` and `go list`.

**RED.** `ImportError: cannot import name 'DiscoveredProfile'`. The architecture tests did not go red, because I changed the rules and their tests together, so that run proved nothing. I then planted seven real violations in `src` and each one failed the right test:
- checks importing an adapter
- checks importing the cli
- domain importing the cli
- application importing the cli
- `go vet` in application
- `go test` in domain
- `go list` in checks

**GREEN.** 750 pass. Nine of nine mutants killed. The first BOM mutant reported "350 passed", but its traceback showed it had never applied, so that line was invalid. Re-run properly, it gave 8 failed. That incident also showed `\uXXXX` being converted on the way to my shell, so I scanned `src` and `tests` for raw U+FEFF, U+2028, U+2029, U+0085 and surrogates: none.

## Group 6: docs and hygiene

**Changed.**
- `docs/limitations.md`: "Prompt injection and data", the `/data` volume, a Cost section with the overshoot, and the streaming requirement.
- `AGENTS.md:32` states what the code does.
- `docs/architecture.md:5` and `README.md:5` are current, and the layer table names real paths.
- `.gitignore`: `.env*` and `!.env.example`.
- Phase spec: `make integration` note, review row done, key rotation as an open item for Ionel.
- `docs/decisions/0010-agent-contract.md` and `docs/decisions/0011-stream-every-request.md`, linked from the index.
- `docs/glossary.md`: the fact entry now says citation.

**RED.** `git check-ignore` showed `.env.local` and `.env.bak` not ignored. Two tests failed: the index check and the new `.gitignore` test.

**GREEN.** 752 pass. `.env`, `.env.local` and `.env.bak` are ignored, `.env.example` is not, and `.env` stayed mode 600.

The docs claimed a dropped stream is not retried, but my probe had used `max_retries=0`, so I had not measured it. `test_a_stream_that_breaks_after_it_starts_is_not_retried` now proves one attempt with default retries.

## Live results

I ran `make eval` twice: after Group 3 and at the end. Output went to a file and was scanned before I read it. Key-shaped text in the output and in the run files: **0 in both runs**. A single fake-key dry run against a dead loopback checked the fixtures first, at no cost.

**Final run: 8 passed.**

| Test | Calls | In | Out | Cache read | Cache write | Cost |
|---|---|---|---|---|---|---|
| Haiku, tool through `run_agent` | 1 | 416 | 50 | 0 | 0 | $0.00007 |
| Opus, thinking echoed on turn 2 | 2 | 6 | 152 | 536 | 646 | $0.00640 |
| Haiku, empty error result | 2 | 497 | 175 | 0 | 563 | $0.00021 |
| Haiku, empty plain result | 2 | 497 | 96 | 0 | 562 | $0.00017 |
| Opus, `DiscoveredProfile` strict off | 1 | 4 | 4 | 0 | 824 | $0.00422 |
| Opus, `Wiki` strict off | 1 | 4 | 4 | 0 | 3,810 | $0.01915 |
| **Opus, cache hit** | 2 | 6 | 100 | 2,204 | 2,262 | $0.01377 |
| Haiku, structured output | 1 | 229 | 11 | 0 | 0 | $0.00003 |
| **Total** | 12 | | | | | **$0.04402** |

The first run cost $0.04237, so the round spent **$0.08639**. Those figures use ARDE's price table, not an invoice. The two calls the API refused in strict mode are not in them, and whether a 400 is billed is unknown.

**The cache hit is proven, on Opus 5.5.** Turn 1 was `cache_write=2204`, and turn 2 was `cache_read=2204`, the same in both runs. Proven live:
- Streaming, on every call.
- The structured path: `Verdict` came back parsed on Haiku.
- The thinking echo on Opus.
- Both strict-mode refusals, still with the same messages.
- The run folder at mode 0700.

The first run had 8 passed and 1 error. The error was my own: the new "nothing was logged" check fired on the direct `parse` test, because the adapter no longer logs on success. That test skipped `record_model_call`, which a real classifier must call, so it now calls it. That also proves that path live.

## Choices I made

Each one says what it costs if wrong.

1. **A broad `except Exception` in `_send`, around SDK calls only.** The measured failures are five unrelated types, and a tuple would break on the next SDK release. Cost: a bug inside the SDK, or a bad keyword from ARDE, reports as "could not be read" with only the kind, no traceback. It also turns a lone surrogate echoed from the model's own tool input into a `ProviderError`, which the bugs review accepted as an answer. Measured by probe: 0 requests sent, exit 1.
2. **`request_id` moved onto `LLMResponse`, logged by `record_model_call`.** Not on the list. The old debug line ran before the charge, so a failing tracer lost a paid call, which item 6 forbids. Cost: a direct `client.complete` that skips `record_model_call` logs nothing on success.
3. **`ParseFailed` carries `response` as well as `usage`.** The list and the plan say `usage`, but `record_model_call` also needs the stop reason and latency. Cost: one extra attribute.
4. **`pause_turn` and unknown stop reasons are a `ProviderError`.** The list says normalise to a Literal and does not say what to do with the rest. Cost: if the API starts sending `pause_turn` without server tools, the run stops rather than re-prompting.
5. **`KEY_READERS` includes the tracer.** The list says "the adapter and `conftest.py`", but the tracer must unwrap `SecretStr` to redact it, and it is an adapter. Cost: one more file allowed to read the key.
6. **`schema_version` is `Literal[1] = 1` on `DiscoveredProfile`, inherited by `RepoProfile`.** The list names `RepoProfile`, but `profile.yaml` holds the base type. Cost: Task 13's "mismatch means a first run" must compare before validating, because a mismatch fails validation.
7. **`Wiki.probes` defaults to `()`, and `ProbeRecord` keeps `kind`.** Both are the smallest change. Cost: `kind` is now redundant on the record.
8. **`SingleLine` refuses tab.** "Control characters" includes it, and Go would accept one. Cost: a description holding a tab goes back to the model as a correction.
9. **`RepoRelativePath` treats a backslash as a separator, and refuses an empty path.** Cost: a real file name holding a backslash cannot be cited.
10. **An empty secret set and an existing run folder raise raw `ValueError` and `FileExistsError`.** Both are wiring bugs, not user errors. Cost: if Task 17 lets one through, the user sees a traceback.
11. **`InvalidBudgetCap` also names `--max-usd`.** The list names only `BudgetExceeded`, but it had the same fault. Cost: none.
12. **`describe_problems` in `domain/base.py` gives the model field and message, never the input.** Shared with the models file. It also closes the first row of bugs 9. Cost: a model correcting itself does not see its value echoed, which it already has.
13. **The cache eval runs on Opus at low effort, not Haiku.** Only agent loops need caching, and those run Opus. Cost: about one cent per `make eval`.
14. **The two reviewers disagree on Sonnet 5.5 cache reads.** The bundled reference and the code review say $0.20, and the bugs review's live page says $0.10. Not on the list, so I left it. Cost if the page is right: Sonnet cache reads count double, which fails safe, and no shipped role uses Sonnet.
15. **`RepoProfile` "built only by `merge_override`" is a docstring for now.** That function arrives in Task 12, and nothing enforces it until then.

## Left undone, and why

**Not on the list, though named in the reports.** I held to "fix what's on the list, nothing more". The first one needs a decision.

- **`refusal_category` and `refusal_explanation` on `LLMResponse` (silent 5).** `stop_details` is still dropped, so R14's plan to measure how often the safety classifier misfires cannot work yet. Refusal text does reach `debug.log`. **This one is worth a decision before Phase 1.**
- **Input summary on the `tool_call` event (code 1, silent 5).** Inputs are in `debug.log`, but a grader reading only the trace cannot see what an agent read.
- **`retries_taken` on `model_call`, and tracing calls skipped after an accepted submit (silent 3).**
- **Shape-based redaction such as `sk-ant-[...]`, and sets in the tracer (bugs 9).** Now documented as a limit.

**Known rough edges.**
- An `overloaded_error` inside a stream reports "HTTP 200" with the generic next action, because the SDK raises a plain `APIStatusError` with the stream's status. It still stops the run with a next action.
- That surrogate echo reports "the reply broke off or could not be read", although the request never left. It is the right exit and costs nothing, but the message blames the wrong side.
- Under `make eval`, a scrubbed failure report becomes plain text, so pytest's short summary line loses its message. The full report above it is intact.

**Deferred on purpose.** Bugs 12 and the deferred items the list leaves alone.

**For Ionel.**
- **The key is live and unrotated.** `make eval` authenticated, and `.env`'s modification time (2026-11-01 22:49:42) predates the Task 5 leak. That settles the question security I1 left open, and it matches the ruling in `progress.md:146` to rotate later. It is an open item in the phase spec. I read only metadata.
- `make integration` still fails until Phase 1. The spec now says so.
- The gate walkthrough at `docs/decisions/phase-0-walkthrough.md` is not part of this round.

## Follow-up

Three narrow fixes from `phase-0-rereview.md`, in one commit: `db45f04`. `make check` passes with **836 fast tests**, up from 752. `make eval` was not run: every change is proven offline through the real SDK over a mock transport, and good replies take the same path as before. One thing stays unmeasured, under "Inferred" below.

Logs are in the scratchpad `fix/` folder: `followup-probe.txt`, `fu-red.txt`, `fu-remeasure.txt` and `fu-mutants.txt`. This addresses the re-review's point that earlier green and mutation results had no logs.

### Corrections to the sections above

- **Choice 1 was wrong about scope.** It said the broad `except` wrapped SDK calls only. `_to_response` sat inside the same `try`, so a bug in ARDE read as a network fault. Decision 0011 said the same. Both are fixed by item 2 below.
- **Choice 4 understated its cost.** It gave the cost as "the run stops" and left out that the paid reply went uncharged, which was a regression. Fixed by item 1 below.
- **The surrogate probe under choice 1 was hand-built.** It proved the echo path for text blocks only. A lone surrogate in streamed tool JSON raises `ValueError` while the stream is read, so it never reaches that echo. That route is now charged.
- **"A stream that breaks is not counted" was false.** The snapshot still held the usage. The limitations page and decision 0011 now say so.

### 1. Money lost on a bad reply

**Changed.**
- `PaidCall` in `ports.py` holds what the API reported: usage, latency, stop reason, text, request id and refusal. `LLMResponse` extends it, narrowing `stop_reason` to `ModelStop`.
- `ProviderError.paid_call` carries it. It is `None` only when the API reported nothing chargeable.
- A stop reason outside `ModelStop`, an unreadable tool call and every broken stream now carry a `PaidCall`.
- `record_failed_call` in `agent_loop.py` writes `model_error` and charges through `record_model_call`. The loop calls it before re-raising, and the classifier can too.
- `ParseFailed` passes its response as `paid_call`. `ParseFailed.response` is gone, and `ParseFailed.usage` stays.
- `docs/limitations.md` and decision 0011 are corrected. `docs/architecture.md` names the new function.

**Measured, on the real client, SDK, `run_agent`, `JsonlTracer` and `Budget`, with 50,000 input tokens on the message.**

| Reply | Before (re-review) | Now |
|---|---|---|
| `pause_turn` | `kinds=[]`, `totals={}` | `model_error`, `model_call`; 1 call, 50,000 in, 5 out, $0.2001 |
| New stop reason | `kinds=[]`, `totals={}` | same |
| No stop reason, clean EOF | `kinds=[]`, `totals={}` | `model_error`, `model_call`; 50,000 in, 1 out, $0.2000 |
| `ReadError` mid-stream | nothing charged | same as the row above |
| HTML, nothing reported | | `model_error` only, `totals={}` |

**Broken streams undercount output.** The first event carries the prompt's tokens, and `output_tokens` stays at its first value until the last event. A broken stream therefore charges its input and cache tokens, and only that first output count. The limitations page states this.

### 2. The broad `except`

**Narrowing was not practical, so it stays, as your fallback allowed.** I measured 24 stream shapes. Malformed 200 streams raise `AttributeError` and `IndexError` inside SDK code, on top of `AssertionError`, `ValueError` and raw `httpx2` errors. Those are the kinds a bug raises, so a list would be no narrower. It would also add `httpx2` as a direct dependency, and the next unmeasured type would crash raw.

**Changed.**
- `_to_response` moved out of the `try`, into the `else`.
- The `stream(**params)` call moved out too, so a bad keyword stays a `TypeError`.
- `traceback.format_tb` output goes to `debug.log`. A test plants a secret in the transport error and confirms the frames are logged and the secret is not.
- Data faults in a good stream get their own messages, so they no longer point at the network: `_no_readable_usage` and `_unreadable_tool_call`.

**Measured.** The planted `_usage` typo on a good reply now raises `AttributeError` with its own frames, on both `complete` and `parse`.

### 3. The refusal's category and explanation

**Changed.** `Refusal` holds both. `model_call` gains a `refusal` field on a refused turn only, `debug.log` carries both on the reply's line, and `AgentStopped.refusal_category` exposes the category. A refusal with no detail stops with `None`, and a structured refusal carries it through `paid_call`.

**Tests.** The `stop_details` path runs through real SSE and the real SDK. No live refusal has been seen, and the limitations page says so.

### Evidence

**RED.** I added the types first, so the tests failed on behaviour rather than on an import: 44 failed, 209 passed. The planted typo gave the re-review's exact message, `broke off or could not be read (AttributeError)`.

**GREEN.** 836 pass, with ruff, format and strict mypy clean. `run_agent` is 43 non-blank lines.

**Mutants.** All 21 valid mutants on the final code are killed. Getting there found two real problems and one bad mutant:
- `getattr(reply, "usage", None)` survived, because the guard was doing nothing and my comment about it was false. The SDK leaves `usage` as `None`, not absent. I replaced it with a plain `None` check, whose own mutant is killed.
- "Request built inside try" first deleted a line and failed on `NameError`, so its 89 failures proved nothing. The faithful version really moves the call inside the `try`, and one test kills it.
- While checking what the move out of the `try` exposed, I measured a text block with no `text`. It raised a raw `TypeError` that lost the usage and could replace a real failure. That is requirement 1, so I fixed it, test first. A tool block with no `id` was already handled.

### Choices I made

1. **An unknown stop reason on `parse` stays a plain `ProviderError` carrying `paid_call`, not a `ParseFailed`.** The re-review reads Group 3 item 2 as `ParseFailed`, but that holds an `LLMResponse`, whose `stop_reason` must be valid. The behaviour asked for is met. Cost if wrong: a caller catching only `ParseFailed` misses it, so **the classifier must catch `ProviderError`**, which `plan:1241` already does.
2. **Task 26 should call `record_failed_call`.** The plan's amendment says to record `ParseFailed.usage`, which would miss the plain `ProviderError` cases. I may not edit the plan, so this needs carrying into that task's dispatch.
3. **A failed call that crosses the cap raises `BudgetExceeded`, not the `ProviderError`.** Both stop the run, and the failure stays in the trace. Cost: exit 4, not exit 1, for that run.
4. **`failure.paid_call` is set after the handlers,** by assigning to a fresh exception. Reading the snapshot inside a handler risks linking the SDK error, which the design forbids. Cost: one mutation of an exception before it is raised.
5. **A usage that is absent or breaks the rules charges nothing.** That covers `{}`, `null` and negative counts, even where part of it looks readable. The real API's types do not allow these. Cost: a small undercount on a reply that should not exist.
6. **A real bug in reading a reply loses that reply's charge.** It crashes with a traceback, as asked. Cost: one uncharged call on a run that is stopping anyway.

### Inferred, not measured

- That the live API's first event carries the prompt's tokens. The SDK's type requires it and the re-review assumed the same, but neither of us has seen that event live. A cheap way to settle it is to log the snapshot on a real run. It does not need its own `make eval`.
- That a broken or failed call is billed at all. The charge is the safe side of that.

### Left alone

N3 to N9 are out of scope as stated. N8's misleading comment and N5's missing record stay open. The re-review suggests fixing N6 and N7 next, because they are cheap and sit in the key guard. The "HTTP 200" wording on an `overloaded_error` inside a stream is unchanged, and a lone surrogate in an echoed request still blames the reply.

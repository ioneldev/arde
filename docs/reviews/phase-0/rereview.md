# Phase 0 re-review of the fix wave

Fix `98e828f..9a68670`, six commits. **40 of 41 items ADDRESSED, 1 NOT ADDRESSED, 2 new Important findings, 7 new Minor.** No Critical. Findings remain open.

**How this was checked.** I read the whole diff once, all five reviews, the deferred list and the current source. Every measured defect was re-run offline: the real `AnthropicClient` and SDK 2.3.0 over `httpx2.MockTransport`, the real `run_agent`, `JsonlTracer` and `Budget`. Planted violations ran in a copy with no `.env`. Nothing reached the network, `.env` was not read, `make eval` was not run, and no file in the repo changed (checked with `find -newer`). Probes are in `/private/tmp/claude-501/-Users-ionel-work-personal-emag/0f37fc47-1c0a-42ed-bd77-f0a6f96a2ead/scratchpad/phase0_rereview`.

**Inferred, not measured.**
- A 200 reply with usage is billed. The reviews made the same assumption.
- My mock's `message_start` carries usage because the SDK type requires it. I did not see that event from the live API.
- Group 3 item 8 rests on reading the code and the implementer's `eval2.txt`, because live tests were out of scope.

## Item Verdicts

### The re-measurements you asked for

| Defect | Result now |
|---|---|
| Fail-open submit | **Fixed.** `validate` returning a plain `ToolOutcome` or `None` gives `ToolCrashed`, 1 request and no value. A complete submit inside a `refusal` or `max_tokens` turn is never accepted. `Accepted(None)` returns correctly. One type-violation route remains (N4). |
| Code bug fed to the model | **Fixed.** `AttributeError`, `OSError(ENOSPC)`, `MemoryError`, `RecursionError`, `UnicodeError` and a `KeyError` in a validator each give `ToolCrashed`, 1 request, the model sees nothing, the traceback is in `debug.log`, `tool_call` has `ok=false`, and there is no `correction`. Silent 1 measured 15 paid turns for the validator case; it is now 1. |
| Paid-but-unrecorded parse failure | **Fixed for the three measured failures, not for the class.** See below. |
| Lone surrogate | **Fixed.** `os.fsdecode(b"handlers/caf\xe9.go")` in tool content, error content, a `Rejected` reason and a handler's exception: no crash, a valid wire body, both files valid UTF-8. |
| Leak guard, planted failure | **Fixed for failures.** 15 failing tests across 14 modes: every traceback, repr, captured-output and log section was clean, including under `-q -rP`, `-l` and `--full-trace`. Titles and non-failure text bypass it (N6). Collection errors cannot reach the key: the environment reads `None` at import. |
| Path-escape values | **Fixed.** 42 values: 21 refused, and the 21 accepted all resolve inside the root. Refused: `/proc/self/environ`, `../../.env`, `..\..\.env`, `//etc/passwd`, `\\server\share\x`, `a\x00b.go`, `a//..//..//b`, `.\..\x` and others. Accepted: `C:\Windows\win.ini`, `~/.ssh/id_rsa`, `%2e%2e/x` and others. Four of the refused values re-run through `CodeQuote.file`, `Links.files` and `main_file` on both profiles: refused every time. |

The parse measurement, at 50,000 input tokens on the message:

| Reply | Raised | Usage carried | Charged by the caller |
|---|---|---|---|
| Cut-off JSON, schema break, no text block, empty text | `ParseFailed` | yes | 50,000 |
| Valid JSON with `refusal` or `model_context_window_exceeded` | `ParseFailed` | yes | 50,000 |
| Valid JSON with `pause_turn`, a new stop reason, or no stop reason | `ProviderError` | **no** | **nothing: `kinds=[]`, `totals={}`** |

### The broad `except Exception` in `_send`

**It hides bugs, and it keeps no traceback.** The catch itself is justified: I reproduced `AssertionError`, `ReadError`, `RemoteProtocolError`, `ReadTimeout` and `JSONDecodeError` reaching it, and no single SDK base class covers them. It lets `KeyboardInterrupt` and `SystemExit` through. Two things are wrong with it:

1. **Its scope is misdescribed.** The comment at `anthropic_client.py:201`, decision 0011 line 9 and report choice 1 all say it wraps SDK calls only. `_to_response`, `_usage`, `ToolCall(...)` and `LLMResponse(...)` are ARDE's code and sit inside the same `try` (`:190-195`). Report choice 1 lists a bad keyword as the cost and omits this.
2. **A bug reads as a network fault.** I planted a typo in `_usage`, on a good reply:

```
ProviderError: The reply from Anthropic broke off or could not be read (AttributeError)
next_action: Check the network connection, and any proxy set in ANTHROPIC_BASE_URL, then run again
debug.log:   The Anthropic call failed: AttributeError
__cause__ None, __context__ None, no frame below _send
```

Nothing records where it failed, and the good reply is dropped uncharged. A bad keyword gives the same result. By contrast, `ToolCrashed` says "read the traceback in debug.log".

A traceback can be kept safely. Measured on a `ReadError` whose message held a planted secret: `traceback.format_tb(error.__traceback__)` gave 30 lines naming the failing SDK frame and no secret, while `format_exception` printed the secret. See N2.

### Whether `run_agent` is still easy to explain

**Yes.** 36 non-blank lines, as the report says, within the spec's "about 40". It has one loop, four `if`s, one `return` and three `raise`s, and each block does one thing. Three small frictions, none blocking:

- The comment at `:149` says "Keyed on the calls, not stop_reason" directly above a condition that also tests `stop_reason`.
- `max_tokens` is decided in two places, `:150` and `_nudge:195`.
- `raise _stopped(...)` hides a trace write inside an expression.

Ionel should expect a question on the second one.

### Whether streaming handles a failure partway through

**Control flow: yes. Money: no.** I broke streams after text, inside tool JSON, after a complete tool block, with a clean EOF, with an `error` event, and with bad JSON in a delta, all through `run_agent` with retries at 2:

- Every case gave one attempt, `tool_ran=0`, an `ArdeError` with a next action, exit 1, a `model_error` event, and no chain back to the SDK error.
- A stop reason with no `message_stop` is correctly treated as complete.
- A complete tool block with no stop reason is refused. It is not run.
- An `overloaded_error` inside a stream reports "HTTP 200" with the generic next action. The report discloses this, and it is acceptable.

The gap: nothing is charged, even though the usage is in hand. On a clean EOF the final message holds `input=50000`, and after a raw `ReadError`, `stream.current_message_snapshot.usage` still holds it. `docs/limitations.md:47` and decision 0011 line 9 say the reply "never said how many tokens it used". That is false for input tokens. See N1.

### Tests and the report's claims

- **Named tests:** the report names covering tests or files for each group, and I confirmed them below.
- **Output shown:** the report gives counts, not raw output. Logs exist for RED in every group, the Group 1 guard, the Group 5 planted violations, Group 6 green and both eval runs. There is no green log for Groups 2 to 5, and of the 47 mutants only the Group 1 scrubber run is logged, so the mutation count is unverified.
- **What I confirmed:** 752 fast tests collected, ruff, format and strict mypy clean, 320 tests passed across the guard, architecture, docs, loop, client and tracer files, and `eval2.txt` shows 8 passed with `cache_read=2204` on turn 2.

### Group 1: test guard

| # | Verdict | Evidence |
|---|---|---|
| 1 | ADDRESSED | `tests/conftest.py:37-42`, `:62-65`. Key popped in `pytest_configure`. Proved by `tests/test_key_guard.py:83`, and by my 15 planted failures. Residual gap: N6. |
| 2 | ADDRESSED | `tests/test_architecture.py:61`, `:13-17`. Planted literal read fails the real test. Tracer in `KEY_READERS` is disclosed and fair. Residual gap: N7. |
| 3 | ADDRESSED | `src/arde/adapters/tracer.py:22`, `:84-98`. `get_secret_value` outside the listed files fails item 2's test, which passes. |
| 4 | ADDRESSED | `pyproject.toml:48`, `:51`. One `class RecordingTracer`, at `tests/recording_tracer.py`. |

### Group 2: agent loop contract

| # | Verdict | Evidence |
|---|---|---|
| 1 | ADDRESSED | `src/arde/application/agent_loop.py:61-124`, `:134`. `ToolOutcome` has no `submitted` (`ports.py:98`). `T` flows through under strict mypy. `BudgetExceeded`, `ProviderError` and `ToolCrashed` pass as themselves, which item 2 requires. |
| 2 | ADDRESSED | `agent_loop.py:269-281`. Measured above. A `ValidationError` from ARDE's own models still reaches the model, which the item allows and silent 1 warned about: I measured `n: Input should be a valid integer...` sent back. Worth knowing when writing handlers. |
| 3 | ADDRESSED | `agent_loop.py:150`, `:194-202`, `:40-43`. Tests at `test_agent_loop.py:421`, `:433`, `:449`, `:457`. |
| 4 | ADDRESSED | No `pause_turn` branch in the loop, `_tool_result:331`, rewrite gone from the adapter. Live empty-result tests in `eval2.txt`. |
| 5 | ADDRESSED | `agent_loop.py:284`, `:309`, `tracer.py:112-114`. Measured above. |
| 6 | ADDRESSED | `agent_loop.py:225-243`. Measured with `debug` failing and with `event` failing: 1 call in the totals both times. Tracer failure plus a tripped cap gives `BudgetExceeded` with the `OSError` as context. `cost_usd` on the event. |
| 7 | ADDRESSED | `agent_loop.py:213-217`, `tracer.py:30-31`. A `ProviderError` leaves `model_error`, measured. `test_agent_loop.py:786`. |
| 8 | ADDRESSED | Model text and request id `:229`, tool input `:268`, full result `:285`, re-prompts `:201`, `agent_end` `:154`, `:188`, `ports.py:19`. Read back from a real `debug.log`. |
| 9 | ADDRESSED | `test_agent_loop.py:251`, three tool_use ids answered in order. |

### Group 3: Anthropic adapter

| # | Verdict | Evidence |
|---|---|---|
| 1 | ADDRESSED | `anthropic_client.py:191-192`. `"stream": true` on the wire for both paths, `test_anthropic_client.py:183`, `:196`. Old constants gone. Echoed thinking, text and tool blocks are clean on the wire: no `parsed_output`, no `__json_buf`. |
| 2 | **NOT ADDRESSED** | The three measured failures are fixed: `:174-181`, `test_anthropic_client.py:570`. But the item says that on failure `parse` raises `ParseFailed` carrying usage, and a stop reason outside `ModelStop` fails first, inside `_send` (`:246-248`), as a plain `ProviderError` with no usage. Silent 2's defect, "paid for, then recorded nowhere, and the cap cannot see it", still reproduces there: 50,000 tokens on the message, `kinds=[]`, `totals={}`. A stream that ends cleanly early is the real route, and the implementer's own table lists it. The plan has the classifier treat a `ProviderError` as a warning (`plan:1241`, cited by silent 2), so that run would go on. `test_anthropic_client.py:833` pins this with `assert not isinstance(raised.value, ParseFailed)`. Same root cause as N1. |
| 3 | ADDRESSED | `:190-206`, 13 unusable answers through both paths (`test_anthropic_client.py:825`), plus my mid-stream cases. All give a `ProviderError`. |
| 4 | ADDRESSED | `ports.py:76`, `anthropic_client.py:69`, `:78`, `:86`, `:309`. `test_anthropic_client.py:481`, `:490`. |
| 5 | ADDRESSED | `:206`, `:181`. Helper deleted, test corrected. `__cause__` and `__context__` are `None` on all 13 answers and my 9 mid-stream failures. Tracer-failure edge: N3. |
| 6 | ADDRESSED | Comment `:208-209`, 400 action `:110-113`, no "not billed" clause in `src`, `tests` or `docs`. |
| 7 | ADDRESSED | `_MODEL_STOPS` `:39`, `ports.py:49`, `:33`. `src/arde/application/tool_schema.py:8`. Both bugs 11 inputs re-run: no crash, defaults left alone. |
| 8 | ADDRESSED | `tests/adapters/test_anthropic_live.py:92`, `:121`, `:144`, `:312`, `Makefile:16`. Not run by me. `eval2.txt` shows `cache_read=2204`. |
| 9 | ADDRESSED | `test_agent_loop.py:891`. |

### Group 4: tracer, budget, pricing, models file

| # | Verdict | Evidence |
|---|---|---|
| 1 | ADDRESSED | `tracer.py:26`, `:39`, `:47-63`, `:71-74`, `:85`. Re-measured: strict JSON, `nan` as text, U+2028 stays one line, a secret across the cut leaves no head, summary capped at 200, mode 0700, `FileExistsError`, empty secret set refused before the folder exists. Edge: N9. |
| 2 | ADDRESSED | `budget.py:55`, `agent_loop.py:141`. At exactly the cap, 0 further requests. |
| 3 | ADDRESSED | `pricing.py:31-38`, `:68`. Matches the bugs 5 table: $0.0105 at 100,000, $0.0525 at 100,001, $0.46 at 900,000. |
| 4 | ADDRESSED | `budget.py:26`. The plan adds `--max-usd` at Task 17 (`plan:67`), so the flag is real. |
| 5 | ADDRESSED | `src/arde/adapters/models_file.py:42`, `:76`, `:88`, `models_config.py:10-13`, `:25`. All listed inputs refused with exit 2, including nested repeats. One bugs 10 row survives because a strict integer cannot see it: see Out-of-Scope. |
| 6 | ADDRESSED | `test_budget.py:134`, `test_models_file.py:233`, `test_tracer.py:195`. All seven tests in code 12 are gone. |

### Group 5: domain

| # | Verdict | Evidence |
|---|---|---|
| 1 | ADDRESSED | `src/arde/domain/profile.py:14`, `:25`, `base.py:107`. `-toolexec=...`, `-o`, `--` and `-` refused. "Built only by `merge_override`" is a docstring until Task 12, as the report says. |
| 2 | ADDRESSED | `base.py:116-127`, `facts.py:33`, `:60`, `profile.py:19`. Measured above. |
| 3 | ADDRESSED | `base.py:27-36`, `:61`. NUL, tab, ESC, DEL, NEL, C1 and BOM refused by all six types. |
| 4 | ADDRESSED | `facts.py:38`, `:131`, `:147`. `test_models.py:550`. |
| 5 | ADDRESSED | `facts.py:73`, `:84`, `:129`, `profile.py:17`, `inventory.py`. No `go_type` left in `src`, `tests` or the wiki. |
| 6 | ADDRESSED | `facts.py:141`, `test_models.py:377`. |
| 7 | ADDRESSED | `test_architecture.py:53`, `:166`, `:26`. Four planted violations in the copy failed the right tests. `from arde import cli` and `from .. import cli` are caught. |

### Group 6: docs and hygiene

| # | Verdict | Evidence |
|---|---|---|
| 1 | ADDRESSED | `docs/limitations.md:9`, `:12`, `:46`, `:53`. Line 47 holds a false claim: N1. |
| 2 | ADDRESSED | `AGENTS.md:32`. |
| 3 | ADDRESSED | `docs/architecture.md:5`, `README.md:5`. |
| 4 | ADDRESSED | `.gitignore:1-2`, `test_docs_wiki.py:56`. Implementer's log shows `.env.example` not ignored. |
| 5 | ADDRESSED | `docs/specs/phase-0-foundations.md:16`, `:25`, `:68`. |
| 6 | ADDRESSED | `docs/decisions/0010-agent-contract.md`, `0011-stream-every-request.md`, both linked from the index. Line 9 of 0011 holds two false claims: N1, N2. |

## New Breakage in the Fix Diff

### Important

**N1. A paid reply with an unusable stop reason is dropped, on both paths.** `src/arde/adapters/llm/anthropic_client.py:246-248`, `src/arde/application/agent_loop.py:213-217`.

This is the `complete` half of Group 3 item 2, counted once with it. `_to_response` raises before the usage is read, so nothing can charge it. Through `run_agent`, `pause_turn`, a new stop reason and no stop reason each give `totals={}` and a `model_error` with no usage.

This is a regression. Before the fix, those replies reached `_record_model_call` and were charged (read from the diff, not run). Report choice 4 gives the cost as "the run stops" and does not mention the lost charge.

Other shapes lose the usage the same way. A lone surrogate in streamed tool JSON raises `ValueError` while the stream is read, so it never reaches the echo the report describes (that path is real for text blocks, and the report's probe hand-built its message). Any broken stream loses it too, although the snapshot still holds the usage. `docs/limitations.md:47` and decision 0011 line 9 present this as unavoidable, and it is not.

Fix direction: have these failures carry whatever usage exists, and have `_ask_model` charge it before re-raising. Correct both docs and `test_anthropic_client.py:833`.

**N2. The broad `except` reports ARDE's own bugs as network faults, with no traceback.** `anthropic_client.py:190-206`, comment `:201`, decision 0011 line 9, report choice 1.

Detail above. It fails closed, so no money is at risk from the catch itself, but a good paid reply can be lost and the next action sends the reader the wrong way.

Fix direction: write `traceback.format_tb(error.__traceback__)` to `debug.log` (measured safe), give `_to_response` failures their own message, and correct the comment and the decision.

### Minor

**N3. A tracer failure inside the handler re-attaches the chain the design removes.** `anthropic_client.py:197`, `:204`. `_log_failure` runs inside `except`, and the comment at `:185-187` explains why that is unsafe. Measured with a failing `debug`: a raw `OSError` escapes with the SDK error as `__context__`, and planted header text appears in the rendered traceback. It needs a disk failure and a transport failure together, and a key the transport refuses, which `from_api_key` rejects. Fix: log after the handlers.

**N4. A handler's return value is not checked.** `agent_loop.py:282-284`. A non-submit handler returning `Accepted` ends the run with that value (measured), although `:103` says only `validate` can. A handler returning `None` raises a raw `AttributeError` outside the `try`, with no `ToolCrashed`, no `tool_call` and no traceback (measured). Both need a type violation that strict mypy catches in `src`.

**N5. A refused call leaves no record.** `agent_loop.py:141`. `refuse_if_spent` is a new raise site, and `BudgetExceeded` from it left `kinds=[]`. The agent stopped and the trace does not say so. `agent_end` fires on the five loop stops only, so `BudgetExceeded`, `ProviderError` and `ToolCrashed` have none. Deferred item 33 relies on the run-level report for the same reason, and that report is not built yet.

**N6. Titles and non-failure text bypass the key guard.** `tests/conftest.py:37-42`, and the docstring at `:3-4` says "every report". The whole key printed through:

| Route | Modes |
|---|---|
| Warnings summary | every mode, including `make eval`'s `-q -rP` |
| Subtest titles | every mode |
| xfail reasons | `-rA`, `-vv` |
| Live logs | `log_cli` |
| Printed output | `--capture=tee-sys` (`-s` is documented) |
| `record_property` values and xfail messages | JUnit XML |

Rated Minor because a test cannot get the plain key without failing the architecture test, so this needs library code to put the key in a warning. Scrub the warnings or document the limit.

**N7. The architecture test misses the repo's own idiom.** `tests/test_architecture.py:334`. `os.environ.get(KEY_VARIABLE)` with a named constant passes. So do `from os import getenv`, `dict(os.environ)` and `getattr(secret, "get_secret_value")()`. The constant form is how `tests/conftest.py:16` and `models_file.py:74` are written. Under pytest the key is already popped, so the exposure is in `src`.

**N8. Two labels drifted.** The comment at `agent_loop.py:149`, above. And `test_every_tool_use_gets_a_result_whatever_the_stop_reason` (`test_agent_loop.py:465`) lost its parametrization and now covers only `end_turn`, so `tool_use` and `stop_sequence` with calls have no such test.

**N9. The tracer can crash on a model.** `tracer.py:50-51`. `model_dump(mode="json")` raised `PydanticSerializationError` on a model holding a callable, where the old `str()` could not fail. Domain models are safe today, so this is latent.

## Out-of-Scope Observations

None of these block.

- **`max_turns: 1:30` still becomes 90**, and `0x10` becomes 16 (bugs 10). YAML has already resolved them to integers, so a strict integer cannot refuse them.
- **A lone surrogate in a prompt.** A file name in `spec.user` gives 0 requests and a `ProviderError` blaming the network. Prompt builders in later phases need `_valid_utf8` too.
- **`SingleLine` and `_PLAIN_CHAR` accept a lone surrogate**, which cannot be written to a UTF-8 source file. Same class as bugs 6.
- **`MainPackage` accepts `../outside` and `/abs/path`**, and `main_file` accepts a leading hyphen such as `--output=x`. Check both against how Tasks 8 and 12 pass them.
- **`RepoRelativePath` accepts `.` and `a/.`**, which are not file paths. They stay inside the root.
- **Refusal detail is still dropped.** The report flags this as needing a decision before Phase 1, and I agree: R14's measurement cannot work until `stop_details` is kept.

## Verdict

**Findings remain open.** The specific defects the reviews measured are gone, with one exception, and the contract, guard and domain work holds up under independent probes. These remain:

1. **Group 3 item 2, NOT ADDRESSED, with N1 (Important):** a paid reply whose stop reason falls outside `ModelStop` loses its usage on both paths, and the same applies to broken streams. The docs call this unavoidable, and it is not.
2. **N2 (Important):** the broad `except` reports ARDE's own bugs as network faults and keeps no traceback, and its stated scope is wrong.

N3 to N9 are Minor and do not block, but N6 and N7 are cheap and sit in the key guard, so I would fix them in the same round.

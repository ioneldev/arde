# Phase 0 review: silent failures

Branch `phase-0` at `dc6239c`. 12 findings: 2 Critical, 5 Important, 5 Minor. No code was changed.

In this report, `design.md` means `docs/superpowers/specs/2026-11-01-arde-design.md`, `plan` means `docs/superpowers/plans/2026-11-01-arde.md`, and `progress.md` and `phase-0-deferred.md` are in `.superpowers/sdd/2026-11-01-arde/`.

**How this was measured.** Offline probes ran the real `AnthropicClient`, the real SDK (2.3.0), `JsonlTracer` and `Budget`, over a mock transport or `FakeLLM`. Nothing reached the network, `.env` was not read, and `make eval` was not run. The baseline before probing was 379 tests passing. Trace and debug results were read back from the real files. Probe scripts are in `/private/tmp/claude-501/-Users-ionel-work-personal-emag/0f37fc47-1c0a-42ed-bd77-f0a6f96a2ead/scratchpad/` (`probe_cost.py`, `probe_trace.py`, `probe_errors.py`).

**Verified or inferred.** "Measured" lines are what the code did. These are inferences:

- A 200 reply with usage is billed.
- Consequences that depend on the unbuilt pipeline come from the plan.
- Haiku's rates come from the bundled `claude-api` reference (`shared/models.md:87`, `shared/model-migration.md:2240`, cached 2026-10-06), not the live pricing page.

**Deferred entries this upgrades.** `phase-0-deferred.md` lines 21, 33 and 37 are listed as minor, but each breaks a stated constraint, so they rank higher here.

**Suggested fix order.** Do 2 first, because it changes the `LLMClient` port that Phase 1 builds on. Then do 1, which changes what handlers may raise, and 6, which changes `Budget`. The rest are local.

---

## Critical

### 1. A code bug in a tool is fed to the model, and the agent can still finish as `submitted`

**Where:** `src/arde/application/agent_loop.py:162-169`. The behaviour is pinned by `tests/application/test_agent_loop.py:456` and `:474`.

**Measured:**

- A `read_file` handler crashing with `AttributeError` on every call still ended `stop=submitted, submitted='made up'`. The model was sent `"AttributeError: 'NoneType' object has no attribute 'splitlines'"`. The trace had `ok: false` on those calls and no `warning` event.
- A `KeyError` in a submit validator, at discovery's shipped limit of 15 turns, gave `stop=turn_limit`, 15 paid calls and 15 `correction` events. The first read `"KeyError: 'profile'"`.
- `OSError(ENOSPC)` and `MemoryError` raised inside a tool both ended `submitted`.

**Hidden errors:** `AttributeError`, `TypeError`, `KeyError`, `NameError`, `ImportError`, `RecursionError`, `MemoryError`, `OSError` (disk full, too many open files, permissions), `UnicodeError`, and a `ValidationError` from ARDE's own models. None of these can be worked around by a model.

**Why it matters:** This is the only broad `except` in `src`, and it turns ARDE's own failures into model input. The result breaks three rules:

- `design.md:393` says code bugs "stop the run rather than being sent to an LLM".
- `design.md:103` defines corrections as validation errors from a submit tool, so a crash recorded as a `correction` is false data in a graded field.
- A run with a dead `http_probe` looks like a clean run in which the model happened not to use it.

Fact validation and the judge may catch invented output later, but neither exists yet. That is inference.

**Fix:** Expected failures already return `ToolOutcome(is_error=True)` in the plan (`plan:595-600`). So give "the input was rejected" its own exception, and stop the run on anything else. It must become an `ArdeError`, because the pipeline catches only those.

```python
try:
    return tool.handler(call.input)
except ArdeError:
    raise
except ToolRejected as rejected:  # a validator's verdict on the model's input
    return ToolOutcome(content=str(rejected), is_error=True)
except Exception as error:        # ARDE's bug or a broken machine: not the model's to fix
    tracer.debug(f"{spec.role} tool {call.name} raised:\n{traceback.format_exc()}")
    raise ToolCrashed(role=spec.role, tool=call.name, error=error) from error
```

Tests `:456` and `:474` then need changing. Submit handlers, and the `calculate` handler around `safe_calculate` (`plan:1047`, which raises on bad input per `plan:1071`), must raise `ToolRejected` or return an error outcome.

### 2. A failed `parse` is paid for, then recorded nowhere, and the cap cannot see it

**Where:** `src/arde/adapters/llm/anthropic_client.py:157-167`, `src/arde/application/ports.py:59-63`, `src/arde/application/agent_loop.py:138` (the only `charge` call in `src`). Deferred as minor at `phase-0-deferred.md:37`.

**Measured:** I ran 1000 failing `parse` calls at 50,000 input and 1,024 output tokens, for each of three failures: cut-off JSON, schema break, no text block.

| What | Result |
|---|---|
| Billed at Haiku rates | $5.51 per failure type (a 200 with usage is assumed billed) |
| Cap in force | $0.01 |
| `budget.totals()` | `{}`, cap never tripped |
| `trace.jsonl` | 0 events |
| `debug.log` | 0 bytes for the first two; one request-id line per call for the third |
| Attributes on the raised error | `exit_code`, `next_action` only, so no usage |

The 1000-call loop is my model of a retrying caller, and no such caller exists yet. A successful `parse` also records nothing (`trace_events=0`), so every caller must remember to charge and trace, while `_record_model_call` is private to the agent loop.

**Why it matters:** `plan:1241` (`test_failure_degrades_to_code_signals`) says a provider error in the classifier "adds a warning, doesn't fail the run". So this failure can happen on a green run, whose report then understates cost, with a paid call missing from the trace. That breaks three of your rules at once: money unrecorded, the cap blind to spend, a trace event lost.

In dollars it is small today: about $0.0055 per failure at 50K tokens, and the design estimates $0.0001 for a typical change. It is Critical because the port cannot carry the usage at all, and Phase 1 builds on that port.

**Fix:** Separate sending from validating, so the usage exists when validation fails. The SDK raises inside its `post_parser`, and `with_raw_response` has no `parse` (checked), so use `create` with `output_config.format` built from the exported `anthropic.transform_schema`. This also lets `parse` send `cache_control` (`phase-0-deferred.md:36`), and it covers the `parse` half of finding 12 and the misreport in finding 11. Not run yet.

```python
_, response = self._send(request, ask)            # usage now exists whatever happens next
if response.stop_reason != "end_turn":
    raise ParseFailed(..., usage=response.usage)
try:
    return schema.model_validate_json(response.text), response
except ValidationError as error:
    raise ParseFailed(..., usage=response.usage) from error
```

Then send every paid call through one shared `record_model_call`, used by the loop and the classifier alike, and record `ParseFailed.usage` before re-raising.

---

## Important

### 3. The call that ended the run is the one call the trace does not have

**Where:** `src/arde/application/agent_loop.py:121`, `:146-151`, `:164-165`; `src/arde/adapters/llm/anthropic_client.py:139`, `:194-196`. Partly deferred at `phase-0-deferred.md:33`.

**Measured:**

| Scenario | `trace.jsonl` | `debug.log` |
|---|---|---|
| 401, 429, 400 or 529 on turn 2 of a real client | ends at turn 1's `model_call`, `tool_call` | nothing; last line is turn 1's request id |
| Connection failure on turn 2 | same | one line of exception kinds |
| Handler raises `ArdeError` | `['model_call']`, tool name absent | tool name absent |
| 529, then 200 | 2 attempts on the wire, 1 `model_call` | no mention of the retry |
| Read timeout, then 200 | 2 attempts on the wire, 1 `model_call` | no mention of the timeout |
| Accepted submit, then another submit and a `read_file` in one turn | 1 of 3 calls traced | no note of the two dropped |

`_log_failure` returns early for every `APIStatusError`, so HTTP failures are absent even from the debug log.

**Why it matters:** Plan Review Focus 2 (`plan:59`) asks the trace to survive an API failure, but the surviving trace ends mid-run with no sign of why. The pipeline's `run_end` can record the message, but not which role, turn or attempt failed, or the request id. SDK retries default to 2, so a timed-out attempt the server may have finished is invisible. Whether it is billed is unknown and could not be measured offline. `latency_s` also includes retries and backoff, which skews the per-role speed the design wants tracked.

**Fix:**

- In `_ask_model`, on `ArdeError`, emit a `model_error` event (role, model, message), then re-raise.
- In `_run_tools`, emit `tool_call` with `ok=False` before re-raising a handler's `ArdeError`.
- In `_log_failure`, log `_detail(error)` for status errors. The comment at `:309` already says a server's answer holds no credential.
- Read `retries_taken` from the raw response (the attribute exists at `anthropic/_response.py:60`, not run yet) and put it on `model_call`.
- Trace calls skipped after an accepted submit as not run.

### 4. One non-UTF-8 file name crashes the run, and fixing only the tracer moves the crash

**Where:** `src/arde/adapters/tracer.py:24`, `:64-66`; `src/arde/application/agent_loop.py:168`, `:181`; `src/arde/adapters/llm/anthropic_client.py:182`. Deferred at `phase-0-deferred.md:21`.

**Measured:** `os.fsdecode(b"handlers/caf\xe9.go")` gives `'handlers/caf\udce9.go'`.

- As tool content: `UnicodeEncodeError` (not an `ArdeError`) after 1 paid request. The trace holds `['model_call']`, so the `tool_call` is lost.
- Inside `ValueError(f"cannot parse {name}")`: the `UnicodeEncodeError` is raised from inside `_run_tool`'s own `except` block, so the safety net itself crashes.
- With a tolerant tracer, the same text kills the next request: raw `UnicodeEncodeError`, 0 attempts sent. It is a `ValueError` subclass, so `_send:182` re-raises it.
- Refuted: an `OSError` naming the file does not crash, because its message repr-escapes the surrogate.

**Not measured:** a real listing of such a file, because APFS refuses the name (`OSError 92`). The container runs Linux, where the name is legal, so reaching it through `os.listdir` is inference.

**Why it matters:** The target repo's contents can kill the run, with a raw exception, no report and no next action. Review Focus 5 (`plan:62`) says awkward content must never crash a tool. `plan:598` covers non-UTF-8 file contents, not file names, and names will reach the loop through `list_files` and `grep`.

**Fix:** Clean the text at the boundary, in `_run_tool`, for both handler content and exception text: `text.encode("utf-8", "backslashreplace").decode("utf-8")`. Also make `_append_line` use `errors="backslashreplace"`, so the trace can never lose an event to encoding.

### 5. The record cannot say why an agent stopped

**Where:** `src/arde/adapters/llm/anthropic_client.py:243-257`, `src/arde/application/ports.py:47-54`, `src/arde/application/agent_loop.py:62`, `:72-73`, `:106-111`, `:181`.

**Measured:**

- A refusal carrying `stop_details` with `category: cyber` and an explanation: the category, the explanation and the reply text appear in neither file.
- Marker strings in the model's text, a tool's input, and the tail of a long tool output: all absent from both files.
- `AgentResult.stop` is written nowhere. `no_progress` and `turn_limit` both appear as a bare list of `model_call` events with `end_turn`. Re-prompts leave no event.

**Why it matters:**

- `progress.md:127` plans to "measure on the first real run" how often the safety classifier misfires, and R14 depends on that. As written it cannot be measured, because the category is dropped in `_to_response`.
- `design.md:400` promises "full prompts and responses" in `debug.log`, but only the first prompt is written.
- A grader cannot see what any agent read, because `tool_call` has no input.
- A pipeline `step_end` could carry `stop`, but not the refusal reason, which the adapter already threw away.
- R13 also means `stop="max_tokens"` when the context window ran out, where raising `max_tokens` does not help. The raw value survives only in `model_call`.

**Fix:**

- Add `refusal_category` and `refusal_explanation` to `LLMResponse`.
- Emit an `agent_stop` event (role, stop, turns, the refusal fields) from `_stopped` and `_submitted`.
- Write each reply and each tool's full input and output to `debug.log`.
- Add a redacted tool input summary to `tool_call`.
- Give the context-window case its own stop name.

### 6. `model_call` has no cost, and a tracer failure comes between the paid reply and the charge

**Where:** `src/arde/application/agent_loop.py:126-140`, `src/arde/adapters/llm/anthropic_client.py:187-189`, `src/arde/adapters/tracer.py:14-18`.

**Measured:**

- `model_call` fields are `kind, latency_s, model, role, stop_reason, ts, usage`. `design.md:399` requires cost.
- `tracer.event` raising `OSError(ENOSPC)` on a $4.00 call against a $0.0001 cap: 1 request sent, `totals={}`, and the cap was never compared. The run stops, so this fails closed, but the spend is gone.
- `tracer.debug` failing at `_send:188` loses the reply and its usage before `_to_response` runs.
- With `trace.jsonl` unusable, the constructor and `debug.log` both work. `IsADirectoryError` appears only after the first paid call, with `totals={}`.

**Why it matters:** The current order exists for a good reason: `charge` raises, and the call that trips the cap belongs in the trace. The cost is that the total depends on the disk, and the trace cannot carry the number the design asks for.

**Fix:** Split recording from enforcing, which fixes both problems.

```python
cost = deps.budget.record(call)                     # cannot raise over the cap
deps.tracer.event("model_call", ..., cost_usd=cost)
deps.budget.enforce()                               # raises BudgetExceeded
```

Also create `trace.jsonl` in `JsonlTracer.__init__`, so an unusable trace fails before any spend, and move the debug line after `_to_response`.

### 7. Configuration failures exit as "checks failed"

**Where:** `src/arde/application/ports.py:63`; `src/arde/adapters/llm/anthropic_client.py:294-306`, `:45-115`.

**Measured:** 401, 403, 404, 402, 429, 529, a malformed key and `max_tokens` too high all give `exit_code=CHECKS_FAILED` (1). An uncaught Python exception also exits 1.

**Why it matters:** `plan:855` sanctions exit 1 for a provider error mid-run, so 429, 529 and network failures are within the plan, though the name still misleads. The rest conflict with the design (`design.md:450`, 2 means bad usage) and Review Focus 4 (`plan:61`):

- A malformed key and `max_tokens` too high are raised before any network call.
- 401 and 404 tell the user to fix the key or the models file, and every other models-file error exits 2.

Today a caller reading exit 1 cannot tell failed checks from a bad key or a crash of the kind in findings 4 and 11.

**Fix:** Give `ProviderError` an `exit_code` argument defaulting to `CHECKS_FAILED`, add `exit_code` to `_Failure`, and set `BAD_USAGE` for 401, 403, 404, the malformed key and `max_tokens`.

**Decision for Ionel:** should a provider outage keep exit 1, or get its own code? That changes the design's table.

---

## Minor

### 8. The cap stops after crossing, and it under-reads long Haiku prompts

**Where:** `src/arde/application/budget.py:49-55`, `src/arde/domain/pricing.py:20-25`.

**Measured:**

- `Budget` exposes only `charge` and `totals`, so nothing checks before sending.
- A script whose first call cost exactly the $4.00 cap sent a second call and ended at $8.32. The scenario is contrived but shows the mechanism.
- The worst single Opus call at shipped settings (1M uncached input, 16,000 output) costs $4.32, or 43% of the $10 cap.
- Haiku 5.5 has two rate cards: $0.10/$0.50 up to 100K prompt tokens, $0.50/$2.50 above that. `pricing.py` has one. Counted against published cost is 5.0× low from 100,001 tokens. At 1M, $0.1005 is counted against $0.5026.

**Why it matters:** The cap does stop the run, so this is under-reading rather than failing open. Neither limit is in `docs/limitations.md`.

**Fix:** Refuse a new call once spent reaches the cap, add the Haiku tier to `ModelPrice`, and document that the cap can be overshot by one call. The comment at `pricing.py:23` also needs updating, because the reference now states 0.1× the input rate. Whether "prompt length" counts cached tokens was not checked; it makes no difference to the classifier, which sends no cache.

### 9. Trace lines can be invalid, split or empty of content

**Where:** `src/arde/adapters/tracer.py:15`, `:24`, `:43`; `src/arde/application/agent_loop.py:187-194`. Partly deferred at `phase-0-deferred.md:21-22`.

**Measured:**

- `tracer.event("warning", value=nan, big=inf)` writes `NaN` and `Infinity`, which a strict JSON parser refuses.
- U+2028 and U+0085 pass through `ensure_ascii=False`. Two events gave 4 lines under `splitlines()`, and the repo's own reader (`tests/adapters/test_tracer.py:16`) raised `JSONDecodeError`.
- A Pydantic model becomes `"route='GET /x' ok=True"`, and a generator becomes `"<generator object ...>"`.
- A 701-character compact JSON result and a 307-character path error both got the summary `'...'`. `http_probe` results and `correction` reasons with no spaces can lose their whole record this way.
- Two tracers on one run directory merge silently: `[1, 2]`.

**Fix:** Use `ensure_ascii=True`, write non-finite floats as strings, dump `BaseModel` values with `model_dump(mode="json")`, and raise on any other unknown type. Cut summaries by character after redaction, inside the tracer. Use `exist_ok=False` for the run directory.

### 10. The models file accepts settings its comments say it refuses

**Where:** `src/arde/application/models_config.py:18-22`, `:25-26`, `:60-62`, `:66-72`.

**Measured:**

| Input | Result |
|---|---|
| `effort: hihg` | accepted; the comment at `:25` says a misspelt setting is refused before any model is called |
| `model: gpt-5`, `model: ""` | accepted |
| An unpriced model at run time | 1 paid request, then `UnknownModelPrice`, `totals={}` |
| `max_turns: true` | accepted as 1 |
| Duplicate `model:` key | last one wins, silently |
| `model: 2001-13-45` | raw `ValueError` |
| `ARDE_MODELS_FILE=""` | built-in models, silently |

`"   "` is refused correctly.

**Fix:** Make `effort` a `Literal`, check `model in PRICES` in `load_models`, use a strict integer, use a YAML loader that rejects duplicate keys, catch `ValueError` in `_read_yaml`, and treat a set-but-empty variable as an error. Task 17's price check would run later than this needs to.

### 11. A 200 that is not a proper Message escapes as a raw error, or is blamed on the schema

**Where:** `src/arde/adapters/llm/anthropic_client.py:161-164`, `:187-189`.

**Measured:**

| 200 response | `complete` | `parse` |
|---|---|---|
| `text/html` body | raw `AttributeError` | raw `AttributeError` |
| `{}` | raw `TypeError` | raw `TypeError` |
| No `usage` | raw `AttributeError` | raw `AttributeError` |
| `usage: {}`, negative usage, or non-dict tool input | raw `ValidationError` | `ProviderError: The model's reply broke a rule of Verdict: input_tokens...` |

The SDK returns plain text for a non-JSON 200 (`anthropic/_response.py:287`). A proxy or captive portal is the likely source, which is inference. The `parse` misreport happens because `except ValidationError` wraps `_send`, which includes `_to_response`.

**Why it matters:** This is not silent, but raw errors skip the pipeline's `ArdeError` catch, so there is no report and no next action, and the exit status is the same 1 as failed checks.

**Fix:** In `_send`, check `isinstance(reply, Message)` and wrap `_to_response` failures in a `ProviderError`. Finding 2's restructure narrows the `parse` handler.

### 12. Two paths report success without a checked result

**Where:** `src/arde/adapters/llm/anthropic_client.py:165-167`, `src/arde/application/agent_loop.py:150-151`. The second is deferred at `phase-0-deferred.md:29`.

**Measured:**

- `parse` returned valid JSON as a success with `stop_reason` set to `refusal`, `max_tokens`, `pause_turn` and `model_context_window_exceeded`. `design.md:95` says to check `stop_reason` before reading content. Cut-off JSON cannot parse, and whether a refusal can carry complete valid JSON is unverified, so this is likely rare.
- A submit handler returning `ToolOutcome(content="looks good")` gave `submitted=None stop='submitted'`. This takes a handler bug.

**Fix:** Check `stop_reason` in `parse`, as sketched in finding 2. In `_run_tools`, raise if an accepted submit has `submitted is None`.

---

## What held

- **Finished means submitted.** All seven SDK stop reasons are handled by the code. Measured: a turn with a complete submit and a stop reason of `refusal`, `max_tokens` or `model_context_window_exceeded` did not run the handler and did not count as submitted. Five scripted stops gave the right `AgentResult`. The only gap is finding 12.
- **Cap inputs.** Measured: `nan`, `inf`, zero and negative caps are refused, and negative token counts are refused. From reading the code and its tests: a call is recorded before the cap is compared, and `BudgetExceeded` passes through the broad `except`.
- **Provider errors.** Every status I tried (400, 401, 402, 403, 404, 408, 409, 422, 429, 500, 503, 504, 529) gave a `ProviderError` with a next action.
- **Exception handling.** `src` has eight `except` clauses and no bare `except`, `pass` or `finally`. Only `agent_loop.py:167` swallows.
- **Trace durability.** Each event is appended and closed. After a crash, every written line still parsed.
- **A red run changes nothing.** Not tested in any meaningful way: the only writes in `src` are the tracer's `mkdir` and append, so nothing can change a repo yet. Task 23 is where this needs real review.

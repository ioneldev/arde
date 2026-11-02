# Phase 0 bug hunt

Branch `phase-0` at `dc6239c`. Baseline: 379 offline tests pass. No live tests, no `make eval`, `.env` not read, and the repo is unchanged.

**Result: 0 Critical, 8 Important, 4 Minor.** All 12 are reproduced against the real code. The default path works: the loop stops correctly, the cap trips, and the full API key stayed out of every file I checked. The defects sit at the edges: failures that lose their next action, records that §14 of the design requires but nothing writes, and text that passes validation and then breaks Go.

Severity used here:

- **Critical:** a stated safety guarantee is defeated in real use. None found.
- **Important:** a stated requirement fails on input that can plausibly occur.
- **Minor:** real, but needs contrived input or has small impact today.

Probes are in `/private/tmp/claude-501/-Users-ionel-work-personal-emag/0f37fc47-1c0a-42ed-bd77-f0a6f96a2ead/scratchpad/phase0_bug_hunt/`. Run one from the repo root with its folder as the argument:

```sh
S=/private/tmp/claude-501/-Users-ionel-work-personal-emag/0f37fc47-1c0a-42ed-bd77-f0a6f96a2ead/scratchpad/phase0_bug_hunt
PYTHONDONTWRITEBYTECODE=1 env -u ANTHROPIC_API_KEY .venv/bin/python $S/probe_client.py $S
```

Client probes use the real `AnthropicClient` and the real SDK over `httpx2.MockTransport`, so they make no network calls. Loop probes use the real `run_agent` with `FakeLLM` and the real `JsonlTracer`.

## Important

### 1. A 200 that isn't a Message crashes with no next action

**File:** `src/arde/adapters/llm/anthropic_client.py:186-189`, and `_to_response` at `:243-266`. `_send` guards only `ask()`. The SDK returns a bare `str` for a non-JSON body (`anthropic/_response.py:287`), and the `else` branch trusts the reply.

**Input and result**, through `AnthropicClient.complete`, and through `run_agent` for the first and fourth rows:

| HTTP 200 body | Raised |
|---|---|
| `<html>Sign in to the proxy</html>`, `text/html` | `AttributeError: 'str' object has no attribute 'model'` at `:188` |
| empty body, `text/plain` | same |
| `{"hello": "world"}` | `TypeError: 'NoneType' object is not iterable` |
| `{oops`, `application/json` | `json.JSONDecodeError`, raised from `ask()` and re-raised at `:183` |
| `tool_use` with `"input": "partial"` or `null` | pydantic `ValidationError` for `ToolCall` |
| `usage` null, or `input_tokens: -1` | `AttributeError`, or `ValidationError` for `Usage` |

`parse` fails the same way (`'str' object has no attribute 'content'`).

**Should happen:** a `ProviderError` that names the problem and a next action, such as checking `ANTHROPIC_BASE_URL` or the proxy. The first and fourth rows are the plausible ones, from a corporate proxy or a cut-off body. The last two rows would be an API bug. A bad reply also goes uncharged and untraced, because the crash comes before `_record_model_call`.

Review Focus 2 asks for a clear next action when the API fails mid-run.

### 2. A failed model call leaves no record

**File:** `src/arde/adapters/llm/anthropic_client.py:194-196`, and `src/arde/application/agent_loop.py:121`.

**Input:** HTTP 401, 429 and 500 through `complete`, and HTTP 500 through `run_agent`.

**Result:** with `complete`, neither `trace.jsonl` nor `debug.log` is created. With `run_agent`, `trace.jsonl` does not exist and `debug.log` holds only the prompts. `_log_failure` skips every `APIStatusError`, and nothing writes an event when `complete` raises. The role, status and request id exist only on the exception.

**Should happen:** a failed call appears in the trace with role, model, status and request id. The brief for this phase says every model call is recorded, and Review Focus 2 says the run "keeps the trace". A run that dies on its first call currently has no trace at all.

### 3. The trace has no cost, and debug.log has no model responses

**File:** `src/arde/application/agent_loop.py:128-140` and `:62`. `LLMResponse.text` (`ports.py:49`) is read nowhere in `src`.

**Input:** a two-turn run where the model says text, then submits.

**Result:** the `model_call` event carries `role, model, usage, stop_reason, latency_s`, with no cost. `debug.log` holds the prompts and nothing else. For a `no_progress` run and a `refused` run, a search of both files for the model's text finds nothing.

**Should happen:** design §14 says `model_call` holds "tokens, cost, latency, stop reason", and `debug.log` holds "full prompts and responses". The phase spec's purpose is an agent "with its cost and every step recorded". Per-role totals are planned for `run_end`, but per-call cost isn't, and today cost exists only in the `Budget` object's memory. The trace is the graded deliverable, and a run that stops on `refused` or `no_progress` can't be diagnosed from it.

### 4. A lone surrogate crashes the loop, and fixing the tracer alone won't stop it

**File:** `src/arde/application/agent_loop.py:168` and `:181`, `src/arde/adapters/tracer.py:64-66`, and `src/arde/adapters/llm/anthropic_client.py:182-183`.

This extends the Task 3 deferred item "tracer crashes on lone surrogates". That item treated it as a tracer problem and rated it Minor. Three things are new:

- It needs no model. `os.fsdecode(b"caf\xe9.go")` gives `'caf\udce9.go'`, which is how Python decodes a non-UTF-8 filename on Linux, and the container runs Linux. (macOS refuses to create such a file, so I produced the string with `fsdecode`.)
- Pydantic does not filter it: `ToolOutcome(content="x\ud83d")`, `ToolCall(name="x\ud83d", ...)` and `ToolCall(input={"p": "\ud83d"})` are all accepted.
- The safety net is what crashes.

**Input and result**, all raw `UnicodeEncodeError`:

| Trigger | Crash site |
|---|---|
| Tool returns `ToolOutcome(content=f"files: {name}")` | `agent_loop.py:181` then `tracer.py:66` |
| Tool raises `ValueError(f"cannot parse {name}")` | `agent_loop.py:168`, inside the `except Exception` whose comment says a failing tool is the model's to work around |
| Same content, with a tracer that never fails | SDK `_build_request`, `openapi_dumps`, then `anthropic_client.py:183` |
| Model's own tool input holds `\ud83d`, echoed next turn | same SDK path, on the second request |

**Should happen:** Review Focus 5 says agent tools "never crash". The loop should turn tool text into valid UTF-8 at the boundary, for example with `encode("utf-8", "replace")` where the content enters the loop, so the model gets a usable tool result. The echo path needs its own answer, or a `ProviderError`.

### 5. `cost_usd` knows only one of Haiku 5.5's two rate cards

**File:** `src/arde/domain/pricing.py:24` and `:38-47`.

**Input and result:**

| Call | `cost_usd` | Live pricing page |
|---|---|---|
| Haiku, 100,001 input, 1,000 output | $0.0105 | $0.0525 |
| Haiku, 900,000 input, 4,000 output | $0.092 | $0.46 |

**Should happen:** `platform.claude.com/docs/en/about-claude/pricing.md`, fetched 2026-11-02, lists $0.50 input and $2.50 output for prompts over 100,000 tokens, with $0.625 cache writes and $0.05 cache reads. It says "A request's prompt length counts all of its input tokens, including cache reads and cache writes". So the card must be chosen from `input_tokens + cache_read + cache_write`, and a check on `input_tokens` alone would still be wrong.

**Impact, stated fairly:** with the shipped config the classifier makes one call per run, so the undercount is at most about $0.40 per run against a $10 cap. It grows if a looping role moves to Haiku, which the design expects when it says the config "can be adapted". Then recorded spend is a fifth of real spend, and a $10 cap allows $50. The per-role cost that decision would rest on is wrong in the same case.

Two smaller errors in the same table, from the same page: `pricing.py:22` has Sonnet 5.5 cache reads at $0.20 and the page says $0.10, which overcounts and fails safe, and no shipped role uses it. The comment at `:23` calls the Haiku cache-read price an unpublished estimate, but $0.01 is now published.

### 6. Text that passes every validator still breaks `go build`

**File:** `src/arde/domain/base.py:13-16` (`SingleLine`) and `:41` (`_PLAIN_CHAR`).

The renderer doesn't exist yet, so I wrote validated values into the template lines from `docs/research/spike/python/render_demo.py`. Each value went through the real Pydantic model first, then real Go 1.27.1.

| Value | Accepted by | `go build` | `gofmt -l` |
|---|---|---|---|
| `"a\x00b"` | `summary`, `description`, `ParamName`, `RoutePattern`, `enum` | `read ./main.go: unexpected NUL in input` | parse error |
| `"a﻿b"` | same | `main.go:3:14: illegal byte order mark` | parse error |
| `"ok "`, `""`, or a trailing tab | `summary`, `FieldFact.description` | builds | a clean file becomes flagged |

**Should happen:** refuse NUL and U+FEFF, ideally all control characters, in `SingleLine` and `_PLAIN_CHAR`, and strip or refuse surrounding whitespace.

This fails safe: a red run writes nothing to the user's repo. But a renderer or build failure stops the run with no repair sent to an agent, so the spend is lost. The trailing-whitespace row is the weaker one, because the planned renderer may run `gofmt` over its output and repair it. That part is inferred, because the renderer isn't written.

### 7. Malformed YAML in the models file raises raw errors

**File:** `src/arde/application/models_config.py:66-72`. PyYAML raises more than `YAMLError`.

**Input and result:**

| Models file | Raised |
|---|---|
| `model: 2024-13-45` | `ValueError: month must be in 1..12` |
| `effort: 2001-12-14 99:59:43` | `ValueError: hour must be in 0..23` |
| `max_turns: ` followed by 5,000 digits | `ValueError: Exceeds the limit (4300 digits)...` |
| `[` repeated 100,000 times | `RecursionError` |
| `a: {b: {b: ...` nested 5,000 deep | `RecursionError` |

**Should happen:** a `ModelsFileError` with exit code 2 and its next action, as every other bad file gets. Catching `ValueError` and `RecursionError` beside `yaml.YAMLError` covers these. The first row is the plausible one: a typed date where a name was meant.

### 8. A bad API key or `max_tokens` exits 1 instead of 2

**File:** `src/arde/application/ports.py:62-63` hard-codes `CHECKS_FAILED`. It is reached from `src/arde/adapters/llm/anthropic_client.py:294-299` and `:302-306`.

**Input and result:** `AnthropicClient.from_api_key(SecretStr(k), tracer)` with `k` as `""`, `" "`, `"sk-ant\n"` or `"sk ant"` gives exit code 1, `CHECKS_FAILED`. So does `max_tokens: 1000000`, whose next action tells the user to edit the models file.

**Should happen:** exit 2. Review Focus 4 says "missing API key... Exit 2 with the next action and no LLM call", and design §16 defines 2 as bad usage. The CLI in Task 17 may check for a missing key first, but only this method can detect a malformed one, and `ProviderError` offers no way to pass another exit code. A script reading the code would report failed checks for a typo in `.env`.

## Minor

### 9. Exact-match redaction runs after the text has been changed

**File:** `src/arde/adapters/tracer.py:39-48`, and `src/arde/application/agent_loop.py:186-194`.

In all of these the secret is transformed first, then matched exactly.

| Input | Result |
|---|---|
| Handler validates input with Pydantic, and the model sends a 104-character key in an `int` field | `debug.log` gets `input_value='sk-ant-api03-AbCdEfGhAbC...EfGhAbCdEfGhAbCdEfGh-ZZ'`, 47 of 104 characters, through `traceback.format_exc()`. `trace.jsonl` stayed clean in both runs |
| Secret is a multi-line PEM | `_summary` turns the newlines into spaces, so the whole secret reaches `trace.jsonl` unredacted |
| Secret `correct horse battery staple`, cut at the 200-character limit | The trace keeps `correct ...`, half of it, despite the "whole words only" comment at `:186` |
| Secret with a backslash or quote, inside a `set` or `frozenset` | `str()` escapes it, so it doesn't match. The comment at `tracer.py:30` describes this exact risk for lists and dicts |

**Should happen:** redact before transforming, and also match by shape, such as `sk-ant-[A-Za-z0-9_-]+`, which catches a truncated key.

**Impact today:** only the first row applies to a real Anthropic key, and only if the key reaches a tool's input, which means it is already in repo content the model read. The full key never appeared in any probe. Task 5's incident shows a partial key is taken seriously, which is why this is listed.

### 10. The models file accepts values it should refuse

**File:** `src/arde/application/models_config.py:18-22`.

**Input and result, all accepted:**

- `max_turns: yes` becomes 1, and the agent stops after one turn.
- `max_turns: 1:30` becomes 90, because YAML reads it as base 60.
- `effort: banana`, `effort: ""` and `model: ""` pass.
- A repeated `max_turns: 15`, then `max_turns: 99999`, gives 99999.
- A repeated role silently replaces the first one.

**Should happen:** `effort` as a `Literal` of the documented levels, strict ints for the limits, a non-empty model, and duplicate keys refused. The comment at `:25-26` promises that a misspelt setting is refused before any model is called. I didn't check how the API answers `effort: banana`, because that needs a live call.

### 11. `strict_tool_schema` treats default values as schemas

**File:** `src/arde/domain/base.py:93` and `:105-110`. `_closed_value` recurses into data positions such as `default`.

**Input and result:**

- `d: dict[str, str] = {"properties": "x"}` raises `AttributeError: 'str' object has no attribute 'items'`.
- `d: dict[str, object] = {"properties": {"a": {}}}` silently rewrites the default to `{"properties": {"a": {}}, "additionalProperties": false, "required": ["a"]}`, so the schema no longer matches the model.

**Should happen:** recurse only into schema positions, and skip `default`, `const`, `enum` and `examples`. No current domain model has such a default, so this is latent. The comment at `:95` shows the authors meant to guard against this kind of mistake for field names.

### 12. A handler that mutates its input changes the history sent back

**File:** `src/arde/adapters/llm/anthropic_client.py:245` and `:256`, against the comment at `src/arde/application/ports.py:53`.

**Input:** model sends `{"p": ["a", {"b": 1}]}`, and the handler does `i["p"].append("MUTATED-BY-HANDLER"); i["p"][1]["b"] = 99`.

**Result:** the next request echoes `{"p": ["a", {"b": 99}, "MUTATED-BY-HANDLER"]}`. Pydantic copies only the top-level dict, so `ToolCall.input`, a "frozen" field, shares nested objects with the block that gets echoed.

**Should happen:** `copy.deepcopy(block.input)` in `_to_response`. The docs say edited history invalidates thinking blocks on Opus 5.5, with a 400 on accounts created from 2026-08-31 on. I didn't test that live. No handler exists yet, so nothing does this today.

## What held up

- **Directive smuggling by line break:** `\n` was refused on all 11 rendered fields tested, including bytes input. Form feed, NEL and U+2028 compile as plain comment text in Go.
- **Loop boundaries:** `max_turns` 1 to 4, idle limit, submit on the last turn, falsy `submitted=0`, a rejected submit that sets `submitted`, and `SystemExit` passing through.
- **Budget:** exactly at the cap doesn't trip, and one more token does. Opus 5.5 prices and the Haiku low card match the live page.
- **Redaction of a real-shaped key:** clean in dict keys, lists, tuples, sets, bytes and tracebacks.
- **Round trips:** 400 of 400 hostile `Wiki` values survive JSON and default `yaml.safe_dump`, and instances validate against `strict_tool_schema`.
- **Client:** `parse` keeps `effort` in `output_config`, too-high `max_tokens` is reported, and the wheel contains `models.yaml`.
- **Huge values:** `_summary` took 4 s on 40 MB, and the validator regexes show no catastrophic backtracking.

## Not verified

Each of these needs the live API:

- Repeated tool ids. The loop runs both calls and sends `dup` twice. The real API doesn't produce repeats, and I didn't test how it answers.
- Whitespace-only error text in a tool result. `_with_error_text` substitutes text only for empty content.

## Already on record, so not reported

- The items in `phase-0-deferred.md`, except where finding 4 adds to one.
- The strict-schema refusals in `docs/limitations.md`.
- The Task 14 notes on quotes and commas in `enum`, `default`, `ParamName` and `RoutePattern`.
- `extra_test_commands`: the plan has Task 12 give the agent a schema without it. `strict_tool_schema(RepoProfile)` lists it as required today, so Task 12 must not reuse that schema unchanged.

## Reproduced, and left out to stay within 12

- `JsonlTracer` raises a raw `FileExistsError` or `NotADirectoryError` on a bad run dir.
- A very small cap prints `$0.0000, past its cap of $0.0000`, and `0.1 + 0.2` trips a `0.3` cap.
- `yaml.safe_dump(..., allow_unicode=True)` turns `\x85` into a space on reload, which is a trap for the wiki store in Task 13. The default dump is safe.

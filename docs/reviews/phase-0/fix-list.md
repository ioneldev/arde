# Phase 0 fix list

One fix round for all five phase reviews. Details and measurements are in the reports:

- `phase-0-review-architecture.md`
- `phase-0-review-code.md`
- `phase-0-review-silent.md`
- `phase-0-review-bugs.md`
- `phase-0-review-security.md`

Work through the groups in order, with one commit per group. Tests first in each group. `make check` passes after each commit.

## Group 1: test guard (do this first)

From security I2, I3; code 2 and items 1, 35, 41.

1. `tests/conftest.py` scrubs any 16-character window of the key from pytest reports, popping the key from `os.environ` in `pytest_configure`. Tests get a `SecretStr` through a fixture. Prototype: `/private/tmp/claude-501/-Users-ionel-work-personal-emag/0f37fc47-1c0a-42ed-bd77-f0a6f96a2ead/scratchpad/phase0_security/guard/conftest.py`. Prove the guard with a failing inner pytest run.
2. Architecture test: fail on `get_secret_value` or `ANTHROPIC_API_KEY` reads outside the adapter and `conftest.py`.
3. Never bind the plain key to a local in tests. `JsonlTracer` takes `tuple[SecretStr, ...]`.
4. `--strict-markers`. Move `RecordingTracer` to one shared test helper, and set `pythonpath`.

## Group 2: agent loop contract

From architecture 1, 3, 5, 6; silent 1, 3, 4, 5, 6, 12; bugs 2, 3, 4; code 1, 3, 9, 10 and item 31.

1. Replace the contract (architecture 3): `Accepted[T]`, `Rejected`, `SubmitTool[T]`, `AgentSpec[T]` holding `config` and `submit`, and `run_agent[T](spec, deps) -> T`. It raises `AgentStopped(ArdeError)` with `stop`, `turns` and `last_rejection` for everything except an accepted submit. `ToolOutcome` loses `submitted`. `AgentDeps` holds client, tracer and budget. Use frozen dataclasses if Pydantic generics fight mypy.
2. Only `pydantic.ValidationError` from a handler returns to the model. Anything else becomes `ToolCrashed(ArdeError)`, with the traceback in `debug.log`. `ArdeError` propagates.
3. `max_tokens` drops the partial turn, tells the model to send less, and counts toward the idle limit. `model_context_window_exceeded` stays terminal, with its own stop value.
4. Remove the `pause_turn` branch, because ARDE sends no server tools. An empty error result uses `content or _NO_ERROR_MESSAGE` in the loop, and the 16-line rewrite in the adapter goes.
5. Clean lone surrogates from tool text in `_run_tool`, so neither the tracer nor the SDK encoder crashes.
6. Public `record_model_call`, shared by the loop and the classifier. `model_call` carries `cost_usd`. A tracer failure can't lose a paid call from the totals, so use `try`/`finally` around the charge.
7. Failed calls leave a record: an event before the error propagates, and `trace.jsonl` exists from the start of a run.
8. `debug.log` has model text, tool inputs, full tool results and re-prompts. Add an `agent_end` event with stop and turns, and list it in the `Tracer` docstring.
9. Test: a rejected submit with sibling calls gets a result for every tool_use.

## Group 3: Anthropic adapter

From silent 2, 7, 11, 12; bugs 1, 8; code 4, 7, 8; architecture 9, 11; security M2; items 38, 42, 43.

1. Stream every request, using `client.messages.stream(...).get_final_message()`. Remove `_STREAMING_REQUIRED`, the `ValueError` branch and `_max_tokens_too_high`. Read the docs in `python/claude-api/streaming.md` first.
2. Structured output uses `output_config.format` on the same path, built with `anthropic.transform_schema`, and validates after the reply. It checks `stop_reason`, and on failure raises `ParseFailed(ProviderError)` carrying `usage`. `cache_control` is sent here too.
3. A reply that isn't a proper `Message`, or fails `_to_response`, becomes a `ProviderError`.
4. `ProviderError` takes an `exit_code` keyword, default `CHECKS_FAILED`. A malformed key, 401, 403 and 404 use `BAD_USAGE`. A mid-run outage stays exit 1.
5. Every raise uses `from None`. Delete `_cause_that_is_safe_to_keep`, and correct its test.
6. Fix the comment at `:192` (code 7), and the `BadRequestError` action (item 38). Delete the "not billed" clause (item 42).
7. `stop_reason` is normalised in the adapter to a `Literal`. `ToolDef.strict` has no default, so each tool decides. Move `strict_tool_schema` to application and fix bugs 11.
8. Live: add a two-turn eval with a padded prompt that proves a cache hit (`cache_read > 0`). `Live` uses a real `JsonlTracer` in a folder that survives the run and prints its path, and asserts on both files.
9. Fast test: `run_agent` + `FakeLLM` + `JsonlTracer`, with a fake secret in a tool result, reading both files.

## Group 4: tracer, budget, pricing, models file

From silent 8, 9, 10; bugs 5, 7, 10; code 5, 6 and items 14, 19, 21, 22, 25; security M1, M4; architecture 8.

1. Tracer: `ensure_ascii=True`, non-finite floats as strings, `BaseModel` through `model_dump(mode="json")`, other unknown types through `str()`. Summaries are cut by character after redaction, inside the tracer. Refuse an empty secret set. Run directories use mode 0700 and `exist_ok=False`.
2. Budget: refuse a new call once spent reaches the cap, through a method the loop calls before each request. Document that the cap can be overshot by one call.
3. Pricing: add Haiku 5.5's rate above 100K prompt tokens ($0.50/$2.50). Update the comment on cache reads.
4. `BudgetExceeded` names `--max-usd` and the amount spent.
5. Move `load_models` and `ModelsFileError` to an adapter, keeping `RoleConfig` in application. `effort` is a `Literal`. The model must be in `PRICES`. Use a strict integer. Reject duplicate keys. Catch `ValueError` and `RecursionError`. A set-but-empty `ARDE_MODELS_FILE` is an error.
6. Tests: charge twice for cache totals (code 11), a Latin-1 models file, and the `debug.log` timestamp. Delete the tests listed in code 12.

## Group 5: domain

From architecture 4, 10; security I4, I5; bugs 6; item 14.

1. Split `DiscoveredProfile` (submit schema and `profile.yaml`) from `RepoProfile` (adds `extra_test_commands`, built only by `merge_override`). `main_package` refuses a leading `-`.
2. `RepoRelativePath` in `domain/base.py`: relative, no `..`, no NUL. Use it for `CodeQuote.file`, `Links.files` and `main_file`.
3. `SingleLine` and the name types refuse control characters and U+FEFF.
4. `ProbeCitation(kind="probe", method, path_and_query)` in `evidence`, and `Wiki.probes: tuple[ProbeRecord, ...]`, with a validator that every citation resolves.
5. Rename `go_type` to `declared_type`. Add `schema_version` to `RepoProfile` and `Wiki`.
6. The duplicate-id error names the next action (item 14).
7. Architecture test: add a `checks` rule, ban `arde.cli` from domain and application, and widen the Go patterns to `go vet`, `go test` and `go list`.

## Group 6: docs and hygiene

From security I6, M3; code 13 and items 17, 26, 27.

1. `docs/limitations.md`: add "Prompt injection and data", covering poisoned comments steering facts and docs, repo text going to Anthropic and `debug.log`, and native runs having no container. Extend "Trust and containers" with the shared `/data` volume. Add the one-call cap overshoot and the streaming requirement.
2. `AGENTS.md:32`: state what the code does, rather than "never appears".
3. `docs/architecture.md:5` and `README.md:5` are stale. Add paths for what now exists.
4. `.gitignore`: `.env*` plus `!.env.example`.
5. Phase spec: note that `make integration` starts working in Phase 1. Mark the review row done. Add API key rotation as an open item, to be done by Ionel before publishing.
6. Add a decision file for the agent contract and for streaming.

## Left alone on purpose

Items 2-13, 15, 16, 18, 20, 23, 24, 28-30, 32-34, 36, 37, 39, 40, 44 and 45 from the deferred list, unless a group above already covers them. Bugs 12 (handler mutation) stays deferred.

# Phase 0 architecture review

Result: no Critical. Phase 0's imports are clean. Plan changes from findings 1, 2 and 4 are already in the plan's "Amendments" section.

## 1. Important: a cut-off turn ends the agent

`run_agent` stops on `max_tokens` and returns nothing, while thinking counts against `max_tokens`. Explore must fit its whole wiki in one call (estimated 10-12K tokens before thinking, not measured). A rejected submit resends the whole batch.

Change: split `model_context_window_exceeded` from `max_tokens`, keeping the first terminal. For `max_tokens`, drop the partial turn, tell the model to send less, and count it toward the idle limit. Review Focus 1 allows a re-prompt. `test_max_tokens_stops_with_reason` must change. Task 13 becomes `add_facts` plus `finish_facts`.

## 2. Important: Phase 2 needs ports that don't exist

The plan has application importing adapter code, which would fail `test_application_imports_no_adapters` at Task 12. `load_models` reads the environment and a file from application (`models_config.py:60`, `:68`). The `checks` layer has no import rule (`tests/test_architecture.py:11-12`).

Change: ports are planned in the Amendments. Move `load_models` and `ModelsFileError` to an adapter, keep `RoleConfig` in application, add a `checks` rule to the architecture test, and ban `arde.cli` from domain and application.

## 3. Important: the `run_agent` contract is fail-open and untyped

Acceptance is the default state of `ToolOutcome`: a submit is accepted when `is_error` is false, and that's the default. `ToolOutcome(content="still to try ...")` finishes the agent with `stop="submitted"`. Also representable: `stop="submitted"` with `submitted=None`, `is_error=True` with a value, a `submit_tool` naming no tool, and `AgentSpec.role` paired with another role's `deps.config`. Evidence: `ports.py:74-77`, `agent_loop.py:33-43`, `:150`.

Change:

```python
class Accepted[T](Frozen):
    value: T
    reply: str = "accepted"

class Rejected(Frozen):
    reason: str

class SubmitTool[T](Frozen):
    definition: ToolDef
    validate: Callable[[dict[str, object]], Accepted[T] | Rejected]

class AgentSpec[T](Frozen):
    role: Role
    config: RoleConfig
    system: str
    user: str
    tools: tuple[Tool, ...]
    submit: SubmitTool[T]

def run_agent[T](spec: AgentSpec[T], deps: AgentDeps) -> T: ...
```

`run_agent` raises `AgentStopped(ArdeError)` with `stop`, `turns` and `last_rejection` for everything except an accepted submit. `ToolOutcome` loses `submitted`. `AgentDeps` keeps client, tracer and budget, so one instance serves the whole run. `Role` is a `Literal` shared with the models file. PEP 695 generics on Pydantic models are untried: use frozen dataclasses if they fight mypy.

## 4. Important: the model retypes the evidence meant to check it

`Fact.evidence` holds a full `ProbeRecord` including `body`, and `FactBatch` submits `Fact` directly, so the LLM authors the "recorded response". A truncated probe can't be cited, or the wiki stores model-typed text and one transcription slip marks a fact stale on every run.

Change: `ProbeCitation(kind="probe", method, path_and_query)` in `evidence`, and `Wiki.probes: tuple[ProbeRecord, ...]`, filled by code, with a validator that every citation resolves.

## 5. Important: the tool catch-all sends code bugs and outages to the model

Any non-`ArdeError` exception from a handler becomes a tool result, traced as a `correction` on a submit tool (`agent_loop.py:162-169`, tests at `test_agent_loop.py:456-485`). The design says code bugs stop the run.

Change: catch only `pydantic.ValidationError` in `_run_tool`. Wrap anything else in `ToolCrashed(ArdeError)` that stops the run, with the traceback in `debug.log`.

## 6. Important: the trace and debug log don't meet the design

`model_call` has no cost, and a test asserts the exact field set. `tracer.debug` logs no model output, tool inputs or full tool results. Re-prompts aren't logged. An agent's stop reason and turns are never traced.

Change: log response text and tool inputs in `_ask_model`, full results in `_run_tool`, add `cost_usd` to `model_call`, and add one `agent_end` event carrying stop and turns.

## 7. Minor: `parse` is unmetered

Trace and charge exist only on the `complete` path, in a private function. Make `record_model_call` public, and call it from the classifier.

## 8. Minor: the models file is checked for shape, not meaning

Validate `model in PRICES` when config is built, and make `effort` a `Literal`.

## 9. Minor: `ProviderError` hard-wires exit 1

Add an `exit_code` keyword defaulting to `CHECKS_FAILED`, and pass `BAD_USAGE` for key and model failures.

## 10. Minor: Go names in the domain, no file version

Rename `go_type` to `declared_type`. Add `schema_version` to the profile and wiki files. Widen the architecture test's patterns to `go vet`, `go test` and `go list`.

## 11. Minor: provider rules on the wrong side of the port

`strict_tool_schema` is an Anthropic rule in the domain with no production caller: move it to application. `ToolDef.strict` defaults to `True` although the API refuses both `RepoProfile` and `Wiki`: remove the default. `stop_reason` is an open set of Anthropic strings tested in the loop: normalise it to a `Literal` in the adapter. `_tool_result` can stay.

## Interfaces

Keep `LLMClient`, `Tool`, `Budget` (concrete, same-layer) and `Tracer` (add `agent_end`). `ArdeError` stays the one pipeline catch. `run_agent` is the right abstraction: discovery needs `last_rejection`, Explore needs findings 1 and 4, Docs fits best, and a refused `finish` is a normal rejected submit.

Unproven premise: no test shows a cache hit. Add a two-turn eval with a padded prompt.

# ARDE: design

Date: 2026-11-01. Status: approved 2026-11-01.

ARDE (Agentic Repo Docs Engine) is a command-line orchestrator that documents a Go HTTP service without manual edits. It adds swag annotations, generates an OpenAPI file with a standard tool, writes project documentation, proves it did not change behaviour, and leaves a trace of what it did.

Items marked *(builder's call)* are mine, not agreed decisions, and can change during planning. Everything else was discussed and approved in the design conversation. Where a decision rests on a measurement, the document says so, and "Measured facts" lists the evidence.

## 1. Intent

This is the take-home for eMAG's Agentic AI Engineer role. The brief is `../../../../Agentic AI Engineer_Tema.pdf`, relative to this file. Target effort is 6 to 8 hours, stopping at around 10.

Two outcomes matter equally:

1. A working orchestrator that meets the brief and survives a live follow-up call, where the interviewers change `catalog-poc` and watch a re-run.
2. Ionel fully understands what was built, why each decision was made, its limits, and how it could improve, because Ionel will defend it line by line.

What the brief grades: the design of the agentic flow (roles, tools, control loop, stop conditions), verification, grounding (no invented endpoints, params or fields), reproducibility and trace quality, and honest trade-offs.

## 2. Goals, non-goals, anti-goals

### Goals

- One command, on any system with Docker, runs the tool against a remote URL and branch, a local clone, or a local repo with unpushed or uncommitted work.
- Produces swag annotations, a generated `swagger.json` and `swagger.yaml`, and project documentation.
- Re-runs update only what a change affects, so the live demo shows a small diff.
- Every run writes a trace (the brief's deliverable) and debug logs.
- Works on another Go `net/http` service with no config, because discovery writes the repo profile.
- Go idioms sit behind one interface, so other languages can be added later.
- The tool and its repo suit both developers and agents (see "Humans and agents").

### Non-goals (design-only, listed in DESIGN.md as "week 2" or later)

- Separate deployment, a memory service, MCP or chat entry points.
- Per-branch workspaces.
- Frameworks other than `net/http` being tested, and languages other than Go.
- The wiki inside the target repo, which could help development agents understand the repo. A later `--export-wiki` flag could write a readable one-way copy. This is a future option to note in DESIGN.md.
- Detecting critical changes that alter a project's scope or can damage a service while tests still pass.
- A separate sandbox for untrusted merge-request code. ARDE and the repo's code share one container, so removing `ANTHROPIC_*` from child processes limits accidents but isn't a security boundary: code running as the same user could still read the parent's environment. This is reasoned, not tested. Repos must be trusted for now.
- A stored knowledge graph. This is dropped, not deferred. The only case that would reopen it is cross-repo questions such as "which services use this contract?".

### Anti-goals (failure even if the tool technically works)

- A red run that changes the user's repo or commits anything.
- Invented endpoints, params or fields in the spec or docs.
- Any behaviour change to the target service.
- An empty result counted as a pass, such as zero routes found.
- Code Ionel can't explain, or a framework that hides the control loop.
- LLM-chosen shell commands, or agents with shell or write tools.

## 3. Constraints

From the brief:

- The agent may add comments, annotations and documentation files. `go build ./...`, `go vet ./...` and `go test ./...` must still pass.
- One extra route is allowed. We don't use it, so the diff contains no new executable code, which is an easy claim to review.
- Re-runnable, and no secrets committed. Provide `.env.example`.
- Spec generated from annotations by a standard tool, not by an LLM.
- DESIGN.md is at most 2 pages.

From Ionel:

- TDD, DDD, clean architecture, SOLID, clear and simple code.
- Comments explain why, never what.
- A decision log is kept as work proceeds, and each piece gets a walkthrough before moving on.
- Destructive code gets independent test cases from the `tech-lead` agent, plus adversarial review.

From the target repo (measured):

- The module is stdlib-only, and the Dockerfile copies only `go.mod`, `cmd` and `internal`. So the agent must not add a dependency, a `go.sum` or a generated Go package.
- This machine has `core.autocrlf=true`, so local checkouts are CRLF while git stores LF.

## 4. Requirements and stack

### What the tool needs

Running it needs only Docker with Compose, an Anthropic API key and network access. The image holds pinned versions of Go, Python, uv, `swag` and git, so nothing else is installed on the host. `.env` holds the key and `REPO_PATH`, and `.env.example` documents both.

```sh
docker compose run --rm arde
docker compose run --rm arde --repo <url> --branch <name>
```

The first command documents the local repo at `REPO_PATH`, and the second uses a URL. Compose builds the image on first use. Developing ARDE needs uv, Go and git on the host, using `uv run arde`, so tests stay fast.

### Stack

- Python with uv, Pydantic (frozen models) and `mypy`. Pydantic validates data at runtime, and `mypy` checks code before running.
- Plain `anthropic` SDK with a hand-written agent loop of about 40 lines. Not LangGraph, the Tool Runner, the Claude Agent SDK or Managed Agents, because the loop, tools, trace and stop conditions must stay visible and agents must have no shell or write tools.
- A small Go helper, `tools/goinspect`, for AST work and the call graph, because those libraries exist only in Go. My four throwaway tools came to 354 lines. `golang.org/x/tools` is a dependency of the helper only, never of the target service.
- `swag` v2.0.0-rc6, pinned.
- `pytest`, `ruff`, `jsonschema`, `openapi-spec-validator`.
- Models are configured per role: `claude-opus-5-5` for discovery, explore, docs and judge, and `claude-haiku-5-5` for the classifier. The trace records cost and speed per role, so the config can be adapted later as cost, speed and quality are tracked.

Before writing API code, the builder must read the Python docs in the `claude-api` skill rather than rely on memory. Known constraints to respect: forced `tool_choice` returns a 400 on `claude-opus-5-5`, so use `auto` with a prompt instruction. Measured: the API refuses `strict: true` for `RepoProfile` (open dict) and the `Wiki` union (`oneOf`), so each tool states its own `strict` setting, and code validation holds the guarantee either way. Streaming is required, because non-streaming calls stop at about 21,333 `max_tokens`. Thinking cannot be disabled on Opus 5.5, so set `effort` explicitly. Check `stop_reason` before reading content.

## 5. Architecture

### The orchestrator is code

Python code runs the sequence. The steps are predictable, and rules such as "never commit before checks pass" can't be left to a prompt. Sub-agents work where the next action depends on what they find. Each is a configuration of one generic loop: a system prompt, a tool list, a submit tool with a validator, and limits.

Validation errors from a submit tool return to the model as tool results. That is what drives each agent's self-correction, and it shows up in the trace as corrections.

| Step | Kind | Sees | Tools |
|---|---|---|---|
| Discovery | Agent | Repo files | `list_files`, `read_file`, `grep`, `submit_profile` |
| Explore | Agent | Repo files, profile, inventory, change report, running "before" service | `list_files`, `read_file`, `grep`, `http_probe`, `submit_facts` |
| Docs | Agent | Wiki facts only, no code, so everything traces to a checked fact | `submit_section` |
| Classifier | One model call | Diff plus code measurements | None, structured output |
| Judge | Agent | Docs, spec and running "after" service only | `predict_and_call`, `finish` |

Discovery and Explore are separate agents because they have different jobs and run at different rates. Discovery makes the first assessment: how to build, run and read this repo. It runs once, and again only if the profile stops working. Explore works out what the service does, and runs on every real change. Separate prompts keep Explore's input small on each re-run, because it doesn't carry discovery's instructions. Explore also starts after the inventory exists, so its schema can list real names.

### First run

```
resolve source → copy into workspace → baseline build, vet, test
  → discovery writes profile → code validates it (patterns match, service starts)
  → explore builds wiki → render comments and tags → swag
  → docs agent writes sections → code checks
       fail → send findings to owner, at most 2 repair rounds
  → blind judge → write back → save snapshot → commit + trace
```

### Later runs

```
resolve source → copy → compare with snapshot
  no changes             → stop, no LLM calls
  no real change         → no LLM calls; re-render, regenerate spec, run code checks
  real change            → change report → classifier → stale facts
                         → explore updates them → render → swag → docs for stale sections
                         → code checks → judge → write back → save snapshot → commit + trace
```

On a later run, the profile is reused and re-validated. Discovery runs again only if patterns match zero routes, the service won't start with saved settings, or Explore finds a route or param in changed code that the matcher didn't list. If it still can't work it out, the run stops, names what's missing and writes nothing.

### Stop conditions

- Turn limit per agent. Defaults *(builder's call, tune on the first real run)*: discovery 15, explore 30, docs 8 per section, judge 30.
- Run cost cap, default $10 *(builder's call)*. The review estimated about $3 per first run with prompt caching and $8 without, so prompt caching is required in agent loops. Nothing has been measured yet.
- At most 2 repair rounds.
- Stop early if the same findings repeat.
- A failed run exits non-zero, leaves the user's repo untouched, keeps the old snapshot and writes a report.

## 6. Source handling and workspace

### Sources

`uv run arde --repo <path-or-url> [--branch <name>] [--commit] [--dry-run] [--json]`

- **URL:** clone into the workspace with `-c core.autocrlf=false`, on the named branch.
- **Local path:** copy the files listed by `git ls-files --cached --others --exclude-standard`. This keeps uncommitted and untracked work and leaves out ignored files such as `.env`, so the LLM never sees them. Measured.

All work happens on the copy. No `go.mod` stops the run.

### Output

- **Local path:** after every check passes, files are written back to the working tree. `--commit` also commits them, and needs a clean tree: uncommitted changes to tracked files stop the run at the start, before any cost, with a message to commit first or drop the flag. This avoids mixing the user's edits and the agent's in one commit *(builder's call)*.
- **URL:** a commit on a new branch in the workspace clone, never pushed.
- Line endings are preserved per file.

The container needs three things for local repos:

- **File ownership:** the service runs as a non-root user, set by `ARDE_UID` and `ARDE_GID`, default 1000. On Docker Desktop, files came back owned by the host user. On Linux the values must match the host user.
- **Line endings:** the container can't see the host's `~/.gitconfig`, so a CRLF checkout with `core.autocrlf=true` looks fully modified. When `git ls-files --eol` shows LF in the index and CRLF in the working tree, ARDE passes `-c core.autocrlf=true` to git. Measured: 16 files looked changed without it, and 0 with it.
- **Commit identity:** the container has no git identity, so a commit fails. ARDE uses `GIT_AUTHOR_NAME` and `GIT_AUTHOR_EMAIL` from `.env`, defaulting to an ARDE identity *(builder's call)*.

Write-back is the one step that can't be undone. It must:

- verify each target file is unchanged since the copy, and abort without writing if any changed
- write only files the run changed
- get independent test cases from the `tech-lead` agent and an adversarial review

### Workspace

One per repo, under `$ARDE_HOME/workspaces/<repo-id>/`. In the container, `ARDE_HOME` is a named volume, so memory survives between runs. Natively it defaults to `~/.arde` *(builder's call)*. Repo id comes from the origin URL, or a path hash if there's no remote. A fork has a different URL, so it gets a new workspace and a full first run.

```
<repo-id>/
  profile.yaml            repo profile from discovery
  profile.override.yaml   optional, written by hand when the profile is wrong
  wiki/                   one file per topic
  sections.yaml           which facts each doc section uses
  snapshot/               last successful tree, including the agent's edits
  runs/<run-id>/
    trace.jsonl  debug.log  service.log
```

Deleting the workspace or the volume means the next run is a full run. A second branch in the same workspace counts as a change from the last successful run, which is a known limitation.

## 7. Repo profile and inventory

### Repo profile

Written by discovery, reused across runs, re-validated each run. No config is needed to onboard. The override file exists only for when discovery gets something wrong.

Contains: main package, port variable, main file for swag, and extraction patterns for routes, params and error codes. Patterns are data, not code:

```json
{"kind": "route", "call": "^mux\\.HandleFunc$", "args": {"path": 0, "handler": 1}}
```

| Item | Found by | Checked by |
|---|---|---|
| Runnable package | `go list`, the agent picks if several | It must build |
| Port variable | Discovery | Service opens the port we set |
| Declaration patterns | Discovery | Code applies them; zero routes stops; routes must answer live |
| Extra test commands | Discovery reports them | Run only once listed in the override file, never through a prompt |

### Inventory

The agent describes how the repo declares things, and code extracts the items from source. If the agent listed routes itself, "no invented routes" would compare the LLM with itself.

The inventory holds routes with their handlers, params, error codes with status, response fields per struct, env vars and make targets. It is computed on every run and never stored. Explore's `submit_facts` schema is built from it using enums, so the agent can't name something that doesn't exist. It may report a suspected gap in a separate field, which triggers a re-check of the profile.

Patterns can't resolve router group prefixes. In a Gin-style sample the matcher reported `/ping` for `/v1/ping`. A live check should get a 404 and stop the run, which is untested.

## 8. Wiki

Stored between runs. One YAML file per topic *(format is builder's call)*, validated by Pydantic. Each fact has one home, an id, evidence, and links.

- **Evidence:** a code quote (file plus exact text, which code verifies exists) or a probe (a real request and its recorded response).
- **Links:** declarations, routes or files.
- **Structured facts:** endpoints with params, defaults, limits, enums, responses and error codes; a description per response field; polymorphic field variants. The renderer uses these.
- **Rule facts:** prose rules, such as how totals are computed.

Facts with an observable effect store a real request and expected response, replayed on every run. A different response marks the fact stale.

### Stale rule

A fact goes stale if any declaration, route or file it links to changed. Changed code with no linked fact goes to the explore agent too. The agent must keep, change or delete each stale fact, and a fact whose code quote no longer exists can't be kept.

Removal: an inventory item gone from the code has its fact deleted by code, without an LLM. Doc sections linked to a deleted fact are rewritten, or removed if nothing remains. The renderer deletes comment blocks with no fact.

## 9. Change detection

Comparing the snapshot with the new copy gives a change report. Layers 1 to 5 are code.

| Layer | Question | How |
|---|---|---|
| Files | Which files changed? | Hash contents, ignoring line endings |
| Declarations | Which func, type, const or var changed? | Hash each top-level declaration |
| Inventory | Did the contract change? | Patterns on both copies |
| Call graph | Which routes reach it? | Route fingerprints: a hash of every function the route can reach |
| Live responses | Did behaviour change? | Same requests against both builds |
| Classifier | What does it mean? | One Haiku call |

Fingerprints and declarations are both needed. Fingerprints follow changes through calls, as with `Convert`. Declarations see what calls don't reach: `fxRates`, `SeedProducts`, `run`, `NewRouter`, `logRequests`. Measured.

Middleware doesn't need its own fingerprint, because declaration links cover it. A static call graph misses interface and function-value calls, and stored requests are the backstop.

### Skip rule

```python
def should_classify(snapshot, copy):
    changed = changed_files(snapshot, copy)    # contents, ignoring line endings
    if not changed:
        return False
    if all(is_docs(file) for file in changed):
        return False
    if all(is_comment_only(file) for file in changed):
        return False
    return True

def is_comment_only(file):
    return is_go(file) and same_ast(file) and only_ordinary_comments_changed(file)
```

`same_ast` compares the whole file, because declaration hashes don't cover imports, and a blank import can change behaviour. It ignores comments and the allowed tags `oneOf` and `binding:"optional"`. `only_ordinary_comments_changed` is an allow-list: remove ordinary comment lines and the allowed tags from both versions, ignore spacing in code, and what's left must match. An ordinary comment is a whole line starting `// `, with no directive form and no block comment marker in it. Any comment edit in a file that could import `"C"`, or in a test file holding an example, counts as a real change. This rule is Go-only.

An earlier version listed the dangerous forms instead. A list can't be complete: the Phase 1 reviews found 26 wrong answers against it, including one that switched off a failing test. A false "run" costs a fraction of a cent, while a false "comments only" ignores a real change, so everything else counts as real, including block comments, trailing comments and added blank lines.

### Comparison baseline

The snapshot includes the agent's output, but a fresh clone or a URL run doesn't, because URL mode never pushes. So the comparison ignores everything ARDE adds: comments, the allowed tag keys and generated docs. A fresh clone of unchanged code then counts as no real change, and ARDE re-renders from the wiki with no LLM calls. Measured: an unannotated copy and the annotated output with tags compare as equal, and an edited `json` tag still shows as a change.

### Classifier

A single model call that can only add work.

- **Input:** the diff plus layers 1 to 5.
- **Labels:** comments-only, contract-breaking, contract-additive, behaviour, data, operational, security-compliance, internal, pipeline, testing. Several may apply.
- **Output:** labels, a short reason, and what the docs need re-checked.
- **Use:** labels go in the report, and pointers go to the explore agent. Anything it flags is added to the re-check list. It can never remove an item.
- **Disagreement:** if code and the classifier disagree, the explore agent resolves it. Example: code sees a new `lang` param, while the classifier sees it read and discarded.

## 10. Annotations and spec

Code writes comments from facts through a fixed template, and `swag` reads them. The LLM never writes a comment or spec line.

```go
// @Param qty query int false "Units to price" minimum(1) maximum(10) default(1)
```

Renderer rules:

- Insert above the declaration found through the Go AST, not by string match.
- Idempotent. Re-rendering the same facts changes nothing.
- Replace the existing `@` block, keep human comments, and delete blocks with no fact.
- Descriptions must be one line, so a fact can't smuggle in a directive.
- Preserve each file's line endings.
- Hand-edited `@` lines get overwritten from the wiki. This is a documented limitation.

Spec generation:

```sh
swag init --v3.1 -g <main-file> --requiredByDefault --ot json,yaml -o docs
```

- Generate JSON and YAML only. Default `swag init` writes `docs.go`, which breaks `go build ./...`.
- Use the struct tag `oneOf:"catalog.LaptopSpecs,catalog.BookSpecs,catalog.ApparelSpecs"` on `Specs any`. Swagger 2.0 can't express this, and `swag` v1 renders `{}`.
- Add `binding:"optional"` on `omitempty` fields, because v2's `--requiredByDefault` ignores `omitempty`.
- `@version` has no source in the repo, so it renders as `0.0.0` rather than an invented version *(builder's call)*.

## 11. Documentation output

Written to `docs/` in the target repo, one Markdown file per section *(builder's call)*, so an unaffected section stays byte-identical on a re-run: overview, architecture and request flow, API, business rules, errors, configuration, testing and shipping, and a change guide.

`sections.yaml` records which facts each section uses. A section is rewritten when any of its facts changed.

Reference tables for API params, response fields and error codes are rendered from facts by code, so a new param shows as one new table row. Only narrative sections go to the docs agent, which keeps seeing facts only *(builder's call)*.

The existing README stays as written, except for a link to `docs/` that code adds, because that's where a new engineer starts *(builder's call)*.

On `catalog-poc`, the docs must state the behaviours below. This list checks that one run and is never prompt text. The Explore prompt asks about classes of behaviour that apply to any HTTP service, so a miss is fixed in those classes, not by adding the fact. All but the last were measured:

- Only handler errors are JSON. Unknown routes and wrong methods return `text/plain`, and a 405 has `Allow: GET, HEAD`.
- Totals are computed in bani before conversion: `/products/1?currency=EUR&qty=10` totals 7041.98, not 10 × 704.20.
- `qty` above stock returns 200 with `available: false`, and `in_stock` in the list means `stock > 0`.
- Validation runs before lookup, so `/products/99?currency=USD` is a 400.
- `/products/+1` and `qty=%2B5` work, and `qty=+5` fails because `+` decodes to a space.
- `GET /` ignores `currency` and is always RON.
- Currency is case-insensitive and trimmed. Empty `qty` and `currency` use defaults. With a repeated param, the first wins.
- The access log deliberately omits query strings and client IPs for GDPR reasons.
- From reading the code: adding a currency takes four edits, including a hard-coded error message, and a missing `fractionDigits` entry silently rounds to 0 decimals.

## 12. Running the service

- Build a binary from the copy with `go build`. Avoid `make run`, because `go run` starts a child process.
- Start it on a free port through the profile's port variable.
- Wait for the port to accept connections, with a timeout, and fail clearly otherwise.
- Stop it with SIGTERM, then force-kill if it hangs. Output goes to `service.log`.
- Run two builds side by side: "before" (the input) and "after" (the output).
- Agents reach it through one HTTP tool limited to that local port.
- Child processes run with `ANTHROPIC_*` removed from their environment, because `go test` and the service execute repo code. This is not a security boundary; see the non-goals.
- The service and ARDE run in the same container, so the local port needs no publishing to the host.

## 13. Verification

Code enforces every rule. The LLM judge only tests usability.

| Failure | Caught by |
|---|---|
| Agent changed behaviour | AST comparison, allow-list, directive check, before/after responses, `go build`, `go vet`, `go test` |
| Agent added anything unexpected | Allow-list: every added line must be one the renderer produced, and only `oneOf` and `binding` tag keys may be added |
| Extra routes | Inventory before and after |
| Invented or missing route, param, field, error code | Spec, wiki and inventory compared both ways. Live checks alone can't catch an invented param, because the server ignores unknown params |
| Wrong default, limit or enum | Live probes at boundaries, derived from the spec |
| Wrong response shape | Live responses against closed schemas, because plain validation accepts undocumented fields. Closing schemas around `oneOf` is untested |
| Formatting damage | `gofmt -l` fails only on files not already flagged |
| Docs mention something unreal | Identifiers resolved against the inventory |
| Docs examples wrong | Fenced `curl` examples run against the "after" service and compared with the documented JSON |
| Empty result | Zero routes stops the run. Zero tests or build-tag-hidden test files give a warning |
| Docs valid but useless | Blind judge |

No single behaviour check is enough. `go test` passed on two real behaviour changes. The AST check caught both, and before/after responses caught only one, and only because the request list included the exact stock level. Measured.

The AST check can't see directive comments, which is why the allow-list protects agent output. Examples measured with an identical AST: `//go:debug httpmuxgo121=1` made every route tried return 404 while `go test` passed, and `//go:build ignore` dropped tests from 8 to 4 while `go test` passed.

Each check has a test proving it fires, using the measured changes as fixtures.

### Blind judge

1. Sees only the docs, the spec and the live "after" service.
2. `predict_and_call` takes the request and a prediction together, so the prediction exists before the response.
3. Code sends the request and compares status and predicted fields.
4. Code refuses `finish` until every route, param and reachable error code has been tried. Beyond that, the judge picks its own awkward requests.
5. A mismatch is re-tested once by a fresh judge context that sees only the same docs and that request, and code compares the result. If the second prediction is right, the mismatch clears as noise and stays in the trace. No LLM can dismiss a mismatch directly *(builder's call)*.
6. A confirmed mismatch blocks the run: Explore re-checks the fact, then Docs rewrites.
7. Clarity opinions go in the trace but don't block.
8. An `unknown` prediction on a required item counts as a mismatch. Elsewhere it's a trace note *(builder's call)*.
9. The judge has a `calculate` tool, because totals invite arithmetic slips *(builder's call)*.
10. On a re-run, the required items are those linked to changed facts *(builder's call)*.

Some error codes can't be reached. `internal` comes from two `writeError` calls, but `Store.Get` only returns `ErrNotFound` and nothing panics, so no request can produce it. A code is exempt only if the wiki holds an `unreachable` fact with code evidence, and every exemption is listed in the run report. A wrong claim of unreachable is a known limit *(builder's call)*.

Limits: it can't test rules with no HTTP effect, arithmetic slips look like mismatches, and a lucky guess passes. None of this has been run yet, so its catch rate is unknown.

### Repair routing

Each check has an owner. Code bugs, such as a renderer failure, stop the run rather than being sent to an LLM.

## 14. Trace and logs

Each run writes to `runs/<run-id>/`:

- `trace.jsonl`: the brief's deliverable. Run start (source commit, tool versions, model ids, prompt hashes), steps, model calls (tokens, cost, latency, stop reason), tool calls, check results, repairs, corrections, and totals per role and per run.
- `debug.log`: full prompts and responses, command output and check details. It contains source code, so it stays local and isn't committed.
- `service.log`: the target service's output.

The API key never appears in any of them.

## 15. Code structure

```
arde/
  domain/        frozen Pydantic models and pure rules: facts, inventory, findings, stale rule
  application/   pipeline, agent roles, ports (interfaces)
  checks/        one file per check
  adapters/
    llm/         Anthropic client and test fake
    language/go/ GoAdapter, renderer, swag runner, directive rules
    workspace, service runner, tracer
  cli.py
tools/goinspect/ Go helper: AST, declarations, patterns, call graph
tests/
docs/            wiki: index, architecture, glossary, limitations, decisions
AGENTS.md        entry point for agents working on this repo
```

Go idioms live only under `adapters/language/go/` and `tools/goinspect/`, and the orchestrator never touches Go directly. These rules are marked Go-only.

```python
class LanguageAdapter(Protocol):
    def detects(self, repo) -> bool: ...
    def is_comment_only(self, old_file, new_file) -> bool: ...
    def changed_declarations(self, old_repo, new_repo) -> list[Declaration]: ...
    def extract_inventory(self, repo, patterns) -> Inventory: ...
    def route_fingerprints(self, repo, routes, main_package) -> RouteHashes: ...
    def render_annotations(self, repo, facts) -> None: ...
    def generate_spec(self, repo) -> Path: ...
    def run_checks(self, repo) -> CheckResult: ...
    def built_service(self, repo, profile) -> AbstractContextManager[Path]: ...
```

The pipeline runs one step at a time. There are no threads, and the two service builds are separate OS processes.

## 16. Humans and agents

Developers and agents both run and change this repo. The principle is one source of truth with two views, and rules enforced by code rather than prose.

Running the tool:

- **One `RunReport` model** feeds a printed summary and `--json`, and is saved as `report.json`. The future MCP wrapper would return the same model.
- **Never prompts**, so it's safe to run unattended. Step progress goes to stderr, so `--json` output stays clean.
- **Errors name the next action.**
- **`--dry-run`** runs everything and writes nothing back.
- **Exit codes:** 0 success or nothing to do, 1 checks failed, 2 bad usage, 3 discovery needs input, 4 budget exceeded.

Working on the repo:

- **`AGENTS.md`** is short: commands, a map of the layers, rules, and "to change X, go here". `CLAUDE.md` points to it.
- **`make check`** runs `ruff`, `mypy` and `pytest`, the same for developers, agents and CI.
- **Architecture tests** fail if `domain/` imports an adapter, or if Go-specific code appears outside `adapters/language/go/` and `tools/goinspect/`.
- **Findable code:** explicit imports, full type hints, small files named after domain concepts, and no dynamic registration.
- **`docs/` is a small linked wiki, built as we go:** `index.md`, `architecture.md`, `glossary.md`, `limitations.md` and `decisions/`. `AGENTS.md` and the README point to `index.md`, so content lives in one place. `make check` fails on a broken link or a mentioned file that doesn't exist.

ARDE can't document itself yet, because it only understands Go. Pointing it at its own repo would need a Python adapter, which is a good week 2 idea.

## 17. Testing

- TDD for all new logic, using `pytest` and a fake LLM, so unit tests need no network.
- Tests that need the Go toolchain are marked as integration tests. Pure logic is tested without it.
- Go helper tests are written in Go.
- The 22 classifier changes plus the extra skip-rule cases become fixtures and an on-demand eval.
- `tech-lead` supplies test cases for write-back, and for anything else that destroys data.

## 18. Scope and order

Rough estimate: 11 to 12 hours, excluding Ionel's review time. The review added write-back as its own line, and the container adds half an hour.

Time doesn't force cuts up front. Ionel spent 4 hours on the design, and implementation runs in phases: each phase is built, then Ionel reviews, understands and tests it before the next starts. The cut list below stays as the fallback.

| Slice | Hours |
|---|---|
| CLI, workspace, trace and logs, LLM client with fake | 1 |
| Go helper and `GoAdapter` | 1 |
| Service runner and code checks | 1.5 |
| Repo profile, explore agent, wiki | 1.5 |
| Renderer, allow-list, `swag` | 1 |
| Docs agent | 0.5 |
| Repair loop and stop conditions | 0.5 |
| Change detection, stale rule, classifier | 1 |
| Blind judge | 0.5 to 1 |
| Write-back, with `tech-lead` cases and adversarial review | 0.5 to 1 |
| Humans-and-agents items, `docs/` wiki | 1 |
| Dockerfile, compose file, `.env.example` | 0.5 |
| Final run, DESIGN.md, README, rehearsal | 1 |

Build a thin first run end to end first, then deeper checks, then re-runs.

If time runs short, cut in this order:

1. `--dry-run` and the architecture tests.
2. Automatic re-discovery, so the run stops and says what's missing.
3. Classifier.
4. Stored requests on facts.

Proving no-config onboarding on a second repo is not in this estimate, so it ships as an untested claim unless time remains.

## 19. Deliverable

One repository:

- **`main`:** orchestrator, README with one command and env vars, `.env.example`, DESIGN.md, and the trace of the run.
- **`catalog-poc-docs`:** the original `catalog-poc` history plus the agent's commit, so the diff is easy to read.

README also notes briefly how AI assistants were used.

Delivery is a GitHub link the interviewers can access.

## 20. Measured facts

From throwaway experiments on 2026-11-01 with Go 1.27.1, swag v1.16.4 and v2.0.0-rc6.

- Real responses for all 3 products validate against the generated OpenAPI 3.1 spec, using the `oneOf` tag.
- Comment-only edits change the binary hash, because of line numbers. A position-free, comment-free AST dump works.
- `swag` reads comments, not the router: after removing a route or param with its comment left behind, the spec still listed it.
- Route fingerprints stayed the same under comment-only edits, and a rounding change in `Convert` flagged `index` and `product`.
- One pattern matcher worked on `catalog-poc` and a small Gin-style sample, using hand-written patterns. Zero matches stopped the run.
- Classifier test on 22 changes, after correcting one error in my key: code scored 13, and Haiku, Sonnet and Opus each scored 22. All three gave identical labels. I wrote the changes, key and labels, the diffs were tiny, and the models ran as agents in one batch rather than through the API, so real commits will score lower.
- Estimated classifier cost: about $0.0001 per typical change on Haiku.
- Rendering from a hand-written fact through `swag` worked on a CRLF file. Re-rendering changed nothing, and the AST stayed identical.
- The service stopped cleanly on SIGTERM with exit code 0.
- Ignoring comments and the two allowed tag keys, an unannotated copy and the annotated output compare as equal.
- A test image with Go 1.27, Python 3.12, uv, `swag` and git built in 33 seconds at 980 MB on Docker for Mac (arm64). Inside it, git, build, vet, test and the live service worked on a mounted repo, memory survived between runs through a named volume, and a file written back was owned by the host user.

The experiment code and data are kept in `docs/research/spike/`.

Not yet tested: Linux, Windows or amd64 hosts, anything using a real API call, agents producing patterns or facts, the judge, closing schemas around `oneOf`, a full run end to end, and any second repo.

## 21. What's left to the builder

- Prompts, exact Pydantic fields, helper command names, and how spec, wiki and inventory are compared.
- The request list for live comparison, beyond boundaries from the spec and stored requests.
- Items marked *(builder's call)*.
- Enabling the refusal fallback the `claude-api` skill recommends, stated to Ionel when added.

## Deviations

- **One-implementation interface.** `LanguageAdapter` has only `GoAdapter`. Ionel asked for it, and it gives fast tests through a fake. The cost is an abstraction with one real use.
- **Large interface.** `LanguageAdapter` has twelve methods, more than interface segregation prefers. It can split once a second language shows the right seams.
- **Library in the domain.** Domain models use Pydantic, where strict clean architecture would keep the domain library-free. This avoids two parallel type systems, and the cost is a dependency in the inner layer.
- **Two languages.** The Go helper adds a language and a top-level `tools/` directory, because the AST and call-graph libraries exist only in Go.
- **Pre-release tool.** `swag` v2.0.0-rc6 is a pre-release, but it's the only option found that can describe `specs`. It's pinned, and the run's checks would surface a bad spec.
- **More than comments in the target.** Struct tags on 3 fields go beyond comments, but the brief allows annotations, and nothing in the repo reads tags by reflection. Tags can be read at runtime in principle, so the allow-list limits them to `oneOf` and `binding`, and before/after responses confirm no effect.
- **No framework.** The agent loop is hand-written, although the brief lists frameworks, because a framework would add nothing this design needs.
- **Large image.** The image is about 980 MB, because it holds both Go and Python toolchains. That's the cost of needing nothing else on the host.
- **Paid evals.** Agent behaviour can't be covered by fast unit tests alone, so the classifier eval costs a small amount and runs on demand.

# Architecture

This is the short map. The reasoning and the measurements are in the [design](superpowers/specs/2026-11-01-arde-design.md).

**Status:** Phase 0 is built: the domain models, the agent loop, the Anthropic client, the tracer, the cost cap and the model settings. Phase 1 has started: the Go helper answers five questions, the Go language adapter calls it, and the service runner starts a built service on a free port and always stops it. No pipeline calls the adapter or the runner yet, so read the steps below as intent until later phases land them.

## The orchestrator is code

Python code runs a fixed sequence of steps. Agents work inside it only where the next action depends on what they find. Every agent is the same generic loop, configured with a system prompt, a tool list, a submit tool with a validator, and limits. Validation errors from the submit tool go back to the model as tool results, which is how an agent corrects itself. See [decision 0001](decisions/0001-orchestrator-is-code.md) and [decision 0010](decisions/0010-agent-contract.md).

| Step | Kind | Sees | Tools |
|---|---|---|---|
| Discovery | Agent | Repo files | list files, read file, grep, submit profile |
| Explore | Agent | Repo files, profile, inventory, change report, the running "before" service | list files, read file, grep, HTTP probe, submit facts |
| Docs | Agent | Wiki facts only, never code | submit section |
| Classifier | One model call | The diff plus code measurements | None, structured output |
| Judge | Agent | Docs, spec and the running "after" service only | predict and call, finish |

## A run

```
First run:
  resolve source -> copy into workspace -> baseline build, vet, test
  -> discovery writes profile -> code validates it
  -> explore builds wiki -> render comments and tags -> swag
  -> docs agent writes sections -> code checks (fail: back to the owner, 2 repair rounds)
  -> blind judge -> write back -> save snapshot -> commit + trace

Later runs:
  resolve source -> copy -> compare with snapshot
    no changes     -> stop, no LLM calls
    no real change -> no LLM calls; re-render, regenerate spec, run code checks
    real change    -> change report -> classifier -> stale facts -> explore updates them
                   -> render -> swag -> docs for stale sections -> checks -> judge
                   -> write back -> save snapshot -> commit + trace
```

A run stops on any of these:

- the turn limit of an agent (defaults: discovery 15, explore 30, docs 8 per section, judge 30)
- the cost cap (default $10 per run)
- two repair rounds used, or the same findings repeating

A failed run exits non-zero, leaves the user's repo untouched, keeps the old snapshot and writes a report.

## Layers

Each layer is a folder under `src/arde/`, created by the task that first needs it. The checks layer and the cli do not exist yet.

| Layer | Holds | Where |
|---|---|---|
| domain | Frozen Pydantic models and pure rules: facts, inventory, findings, the stale rule | `src/arde/domain/` |
| application | The pipeline, the agent roles, and the ports (interfaces) they need | `src/arde/application/` |
| checks | One file per verification check | not yet |
| adapters | The LLM client, the Go language adapter, the workspace, the service runner, the tracer | `src/arde/adapters/` |
| cli | The command line. It wires adapters into the application | not yet |

What exists today:

- `src/arde/application/agent_loop.py`: the one loop every agent runs, plus `record_model_call` and `record_failed_call`, which trace and charge every paid call, failed or not.
- `src/arde/application/ports.py`: the `LLMClient`, `Tracer`, `LanguageAdapter`, `ServiceRunner`, `ServiceClient` and `AgentToolbox` interfaces. `LanguageAdapter` holds ten methods so far, each with an implementation: it detects a repo, says what Discovery must know about the language, tells a comment-only edit from a real one, lists changed declarations, extracts the inventory, fingerprints routes, renders the annotations and tags the facts call for, runs the repo's own checks, builds the service for as long as a `with` body needs it, and generates the OpenAPI file from the repo's annotations. `RenderRequest` and `RenderOutcome` carry a render: the outcome's `RenderResult` says what changed in each file, measured before and after, and its `RenderManifest` lists the comment lines ARDE wrote, which the next render removes first. `ProfileProblem` lives here too, the one error the application may send back to Discovery: anything else an adapter raises means the repo or the machine is broken. `ServiceRunner` has one method, `running`, which takes a `ServiceConfig` and hands out a `ServiceHandle` (the base URL and the port). The errors a runner raises, `ServiceStartError`, `ServiceDiedError` and `ServiceStopError`, live here too, because the application catches them. `ServiceClient` sends one `HttpRequest` to a running service and returns the `ProbeRecord`, or raises `ProbeFailed`. An `HttpRequest` is GET or HEAD and a path with its query, never a host. `AgentToolbox` hands out the tools agents call, so the application never names an adapter.
- `src/arde/application/probe_log.py`: `ProbeLog`, every probe an agent made in a run, found by method and path. A fact cites a probe by its request, and code copies the response from here.
- `src/arde/application/budget.py`: the cost cap and the totals per role.
- `src/arde/adapters/llm/anthropic_client.py`: the Anthropic API behind `LLMClient`.
- `src/arde/adapters/language/go/`: the Go language adapter, which holds every Go idiom. `adapter.py` is `GoAdapter`. `goinspect_client.py` builds the helper and reads its JSON replies into typed models. `inventory_builder.py` turns the helper's replies into the inventory, keeping only what belongs to the service. `directives.py` and `makefile.py` hold the comment-only rule and the make targets. `discovery_guidance.py` holds what Discovery must know about Go, so no prompt has to. `toolchain.py` runs build, vet and test, and builds the service. `go_test_events.py` reads the stream of `go test -json`, so a result says which tests ran and which skipped, not only that none failed. `swag.py` builds the pinned `swag` and runs `swag init --v3.1 -g <main-file> --requiredByDefault --ot json,yaml -o docs` from the repo root, which writes `swagger.json` and `swagger.yaml` into the repo's docs folder and no `docs.go`. Only swag's exit code decides, because it warns on runs that went well. `renderer.py` writes the swag annotations above each route's anchor and above `func main`, a comment above each described field, and the `oneOf` and `binding:"optional"` tags, at positions `declarations` gives, after removing what the last run wrote. `templates.py` holds the text of each comment line, written so swag reads exactly the fact and the next comparison reads an ordinary comment. `struct_tags.py` edits a tag the way `goinspect`'s normaliser reads one, and `gofmt.py` lays out a file again when it was laid out before the render.
- `src/arde/adapters/child_env.py` and `src/arde/adapters/child_process.py`: every child process gets an allow-listed environment, never a copy of ours, and a timeout that also stops what the child started. A child's output goes to temporary files and is read back with a limit, so a chatty command costs disk, and memory only up to that limit. `src/arde/adapters/child_text.py` makes that output safe to show: a message keeps its last 2,000 bytes, and a control character is written out as `\xNN`. `child_env.py` holds the generic list and no Go variable, so a service started to be probed gets none, and `src/arde/adapters/language/go/go_environment.py` builds the Go commands' environment on it.
- `src/arde/adapters/repo_links.py`: the gate every `GoAdapter` method that takes a repo calls first. It lists each link whose real path leaves the repo's real path and stops the run, naming the link and never its target. It names no language.
- `src/arde/adapters/source.py`: the first step of a run. `resolve_source` clones a URL into the repo's workspace with `core.autocrlf=false`, on the branch `--branch` names, after asking the remote whether that branch exists. A local path must be the top of a git repo, and it is never written. `copy_tree` copies the files `git ls-files --cached --others --exclude-standard` lists, so uncommitted and untracked work comes along and ignored files such as `.env` do not, copies every file before it makes any link, copies each link as a link, and runs the links gate on the copy. A local tree is untrusted, `.git` included: git runs with the allow-listed environment and `core.fsmonitor` off, the links gate walks the whole tree before git runs, and an index entry that leaves the repo, or two names that are one path once case and Unicode form are ignored, stop the copy. `ensure_git_eol_config` gives the `-c core.autocrlf=true` a CRLF checkout of LF files needs. The repo id comes from the origin URL, the same for each spelling of one remote, or from a hash of the folder's path when there is no `origin`.
- `src/arde/adapters/workspace.py`: `Workspace`, the one `RunStore` (a port in `ports.py`), which names the files of one repo's workspace under `ARDE_HOME` (default `~/.arde`) and gives each run a folder. The snapshot and the clone are links, each replaced by one rename, so a run stopped at any instant leaves the last one whole.
- `src/arde/adapters/service_runner.py`: `LocalServiceRunner`, the one `ServiceRunner`. It reserves a free port by binding port 0 and closing, starts the binary itself (never through a make target) in the folder `ServiceConfig.cwd` names, in its own process group, with the allow-listed environment plus the port variable, and sends its output to a log that only the owner can read and that must be a plain file. It then waits for a TCP connection, with no health route. A service that exits first fails at once with its exit code and the end of the log, and one that never opens the port fails after `timeout_s`, naming the port variable and the log. When the body ends, whether it returned, raised or was interrupted, it first notes whether the service had already exited by itself, then sends SIGTERM to the whole group, waits 5 s, and kills the group twice, because on macOS one kill can miss a child being forked at that instant. It then checks that the port no longer answers. A service that had died raises `ServiceDiedError`, and one that survives the stop, or whose port still answers after it, raises `ServiceStopError`. Either is attached to the body's own error as a note when there is one. The process-group code is shared with `child_process.py`, and this file names no language and no repo.
- `src/arde/adapters/repo_tools.py`: `list_files`, `read_file` and `grep`, the read-only tools an agent has on a repo. One guard resolves every path a tool opens, links included, and refuses it unless it stays inside the repo, so a link that leads out is neither listed, searched nor read. `grep` matches text as written, not a regex, because the model picks the pattern and the repo the text. Every result goes through `clean_repo_text`, which writes out each control character but the line break and the tab.
- `src/arde/adapters/http_tool.py`: `http_probe`, the agent's one tool on a running service, and `LocalServiceClient`. Code joins the model's path to the service's own address and sends it with `http.client`, which neither follows a redirect nor rewrites `+` or `%2B`. Each reply goes to the `ProbeLog` whole, and the model sees a cut body.
- `src/arde/adapters/agent_toolbox.py`: `LocalToolbox`, the one `AgentToolbox`.
- `src/arde/adapters/tracer.py`: writes `trace.jsonl` and `debug.log`.
- `src/arde/adapters/models_file.py`: reads the model settings per role.
- `tests/fixtures/`: small `net/http` services that the adapter tests treat as real repos, sharing no names or layout with any repo ARDE was built against. `shop/` is one service behind a wrapper, whose price reply holds a field that is one of two structs, depending on the item. `parcels/` holds two `main` packages that share code, and a route added by a blank import. `library/` mounts two muxes, one behind middleware. `annotated/` holds hand-written swag annotations and the two tags ARDE adds, with its `main` in a folder of its own, so the spec tests need no renderer.
- `tools/goinspect/`: the Go helper. `compare` lists the Go files that changed, ignoring comments, formatting, line endings and the two struct tags ARDE adds. `declarations` hashes every top-level declaration and lists struct fields. `match` applies the profile's call patterns to every non-test Go file and prints each call that matches, with the value of each argument that is a string literal, and each pattern that matched nothing. Both skip what the Go tool leaves out, such as a folder with its own `go.mod` or a file whose build constraint requires the `ignore` tag. `routes` reads the service through the type checker: `main_package` and the packages of its module that it imports. It lists those folders and every call that registers a route, with the one function each route's docs belong on and every function of the module its handler names, so a route cannot be lost by a pattern that names too little. It needs a repo that compiles. `fingerprints` takes the function ids `routes` prints, and hashes everything each one reaches by static calls, closures included, so a later run knows which routes changed.
- `tools/swag/`: a Go module with no code, whose `tool` line pins `swag` v2.0.0-rc6 and whose `go.sum` holds the hash of every module it needs. `build_swag` builds it once per process into ARDE's own folder, so nothing is installed globally.

Imports go inward. These rules are enforced by `tests/test_architecture.py`:

- The domain imports no adapter, application or cli code.
- The application imports no adapter or cli code. It reaches the outside world through ports.
- The checks import no adapter or cli code.

The same test file keeps Go-specific code and words such as `go.mod`, `omitempty` and `oneOf` out of everything except the Go language adapter, in Python and in Markdown prompts, keeps reads of the plain API key to the adapter, the tracer and `tests/conftest.py`, and keeps product code and prompts from naming a repo ARDE was tried on. A small Go helper, `goinspect`, sits next to it for AST work and the call graph. The orchestrator talks to Go only through the `LanguageAdapter` interface, so another language could be added later.

## What the LLM may and may not do

It may discover how a repo is built, explore what a service does, write documentation prose from facts, label a change, and judge usability.

It may not write a comment or a spec line, pick a shell command, or touch a file. Code renders every annotation from facts, `swag` generates the spec, and code checks every rule. See [decision 0003](decisions/0003-code-enforces-rules.md).

## Where run state lives

Each target repo gets one workspace holding its profile, wiki, last snapshot, the clone of a URL source, and a folder per run with the run's copies, the trace and logs. Deleting the workspace means the next run is a full first run. The terms are in the [glossary](glossary.md).

# Phase 1 architecture review

Verdict: layering and size hold, but findings 1 to 4 go into the plan before Phase 2 starts. Read-only review: findings come from reading the code and plan. swag's method list comes from summaries of its v2.0.0-rc6 source.

## Important

1. **Links are unchecked free text (biggest risk).** `Links.declarations` and `.routes` are plain strings with empty defaults (facts.py:57). The ids are built privately (adapter.py:55, :98), `Inventory` holds no declarations, no port lists them, and Task 13 never validates links. A mistyped id never equals `changed_declarations` output, so Task 25 never marks that fact stale. Task 13: add `Route.key` to the domain and use it in the adapter. Add `Inventory.declarations`, from the reply read at adapter.py:61. `validate_batch` rejects an unknown declaration or route link, and a fact with no links. Check in code, not by enum, because of schema size.
2. **Task 12 can't tell a bad profile from a broken machine.** `NoRoutesError` and `RouteNotUnderstoodError` sit in an adapter (inventory_builder.py:49, :58), against ports.py:214. A bad regex and an unparsable file are both `GoinspectError` (goinspect_client.py:154), and a bad `main_package` is `GoCommandError` (toolchain.py:68). An `ArdeError` in a submit ends the agent (agent_loop.py:316). Tasks 12, 27: add `ProfileProblem(ArdeError)` to `ports.py`. Make these errors, plus new bad-pattern and bad-package errors, its subclasses. `goinspect match` exits 2 on a bad pattern file. Catch only `ProfileProblem`.
3. **Profile types disagree.** `port_env` is `str` (profile.py:40) but `CodeIdentifier` in `ServiceConfig` (ports.py:204), so a bad value fails late as a raw `ValidationError`. The plan's `merge_override` takes a `RepoProfile`, which only it may build (profile.py:46). `save_profile(RepoProfile)` writes `extra_test_commands` into `profile.yaml`, which should hold `DiscoveredProfile` (profile.py:35). Task 12: type `port_env` as `CodeIdentifier`, pass `DiscoveredProfile` to `merge_override` and `save_profile`, and give `validate_profile` the merged `RepoProfile`.
4. **`Inventory.dropped` has no plan text, and the obvious rule refuses everything.** `Accepted.reply` never reaches the model (agent_loop.py:297). "Must be empty" fails any repo with an env wrapper. Task 12: reject the first submit that has dropped lines, listing them, and accept the same set on resubmit. Task 17: trace them as warnings.
5. **`ANY` can't reach swag.** `mux.HandleFunc("/x", h)` reads as `ANY` and `RoutePattern` accepts it, but swag's `allMethod` doesn't. `@Router /x [any]` fails at Task 15, after Explore is paid for.
6. **Task 14's interface doesn't fit.** `RenderRequest` has no main file or title. `render(request)` can't reach the client inside `GoAdapter`: use `render(request, reading: DeclarationsReply)`. `Route.handler` reads `s.quote` and declarations read `server.quote`: join on the bare name, and stop on none or several. `run_child` fixes stdin to DEVNULL, so "through stdin" can't run: use `gofmt <tempfile>`.
7. **`build_service` hands cleanup to a layer with no port for it** (ports.py:188), and Task 12 builds too. Task 12: replace it with `built_service(repo, profile) -> AbstractContextManager[Path]`.
8. **Consumers are missing.** `api_reference(wiki)` gets no inventory, so `json_name` goes unused. `ResponseField` has no package, so `web.Quote` can't be an enum. Task 16: `api_reference(wiki, inventory)`. Task 13: add `package` to `fields`.

## Minor

- `Owner` has no discovery value for `validate_profile` findings (findings.py:7).
- `PipelineDeps` is defined at Task 17 but used at Task 12. Move it and add `service_runner`.
- Task 28 must call `GoAdapter(prebuilt)`, because `go_adapter` builds from `parents[5]`.

## Responsibilities

The seven-file split is right, apart from finding 2. A Python adapter would still touch application code: `is_comment_only`'s docstring defines Go's `//` and tags (ports.py:159); `ResponseField.omitempty`; `ErrorCode.status` holding `http.StatusBadRequest`; Discovery's prompt must teach Go call shapes; the leak test scans `*.py` only. Task 12: add a port method for the language's discovery guidance, rename `omitempty` to `may_be_absent`, and scan `.md` for `go.mod`, `omitempty` and `oneOf`.

## Size

Mostly earned. About 125 lines could go: walk.go:154-246 by reading `File.Comments` from `parser.ParseFile` with `PackageClauseOnly|ParseComments` (about 50, unproven); service_runner.py:123-160 by a plain `os.open`, since ARDE owns the fresh run folder (about 30, overturns R77); service_runner.py:186-195 and the problem list at :258 (about 20); `declarationCollector` and `fieldReader` as plain functions (about 25).

## Held

Imports point inward. `service_runner.py`, `child_env.py` and `child_process.py` hold no Go. `compare` (Task 18), `route_fingerprints` with missing keys (Task 24), `CheckResult` (Task 17) and `ServiceHandle` (Tasks 11, 18, 25) exist with the assumed shape.

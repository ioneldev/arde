# Limitations

What ARDE does not do, and what has not been tested. Most of it comes from the [design](superpowers/specs/2026-11-01-arde-design.md). The writable-mount risk was found later, in a security review of the container experiment. Add to this page when you find a new limit.

## Trust and containers

- **Repos must be trusted.** The repo's code runs in the same container as ARDE and the API key. Removing `ANTHROPIC_*` from child processes limits accidents, but it is not a security boundary: code running as the same user could still read the parent's environment. This is reasoned, not tested.
- **A writable repo mount is an open risk.** The container mounts the local repo so it can write results back. Repo code running in the container could use that mount to tamper with the host repo, for example by planting a git hook that later runs on the host. This must be resolved before the container ships (Task 28 of the plan).
- **The `/data` volume is shared across repos.** The container keeps `ARDE_HOME`, `GOCACHE` and `GOPATH` in one volume, owned by the same user as repo code. Repo A's tests could write repo B's `profile.override.yaml`, the file `extra_test_commands` trusts. This is reasoned, not tested, and must be resolved before the container ships (Task 28 of the plan).
- **No sandbox for untrusted merge-request code.** A separate sandbox is a non-goal for now.

## Prompt injection and data

- **Repo text can steer the agents.** Comments, READMEs and test names reach the models as plain input. A poisoned comment can steer a fact or a sentence in the docs. Code limits what that can reach: an agent has no shell or write tools, and it cannot set a command to run. A rule with no HTTP effect still goes unchecked, so a false one can reach the docs. This is reasoned: no agent has run against a hostile repo.
- **What the agents read leaves the machine.** Whatever an agent reads goes to Anthropic, including any secret committed to the target repo, and it is also stored in `debug.log`. Run folders can be read by their owner alone.
- **A native run has no container.** With `uv run arde`, repo code runs as the developer, with access to the home directory and the credentials in it.
- **Redaction is exact match.** The tracer replaces the API key wherever it appears whole. A key that was encoded or split on the way in would pass through. Only `trace.jsonl` and `debug.log` go through the tracer: `report.json`, `service.log` and stderr do not.

## What it understands

- **Go `net/http` only.** Other frameworks and other languages are not supported. Go idioms sit behind one interface so a second language can be added.
- **Names in facts are ASCII and plain.** A struct, field, type or error code with a non-ASCII letter, a generic type such as `Page[Product]`, or a `map` or `interface{}` response type fails validation instead of being written into a comment or tag. A service that uses them cannot be documented until the shape is allowed in the domain models.
- **Router groups are not resolved.** Patterns cannot see a group prefix. In a Gin-style sample the matcher reported `/ping` for `/v1/ping`. A live check should get a 404 and stop the run, but that is untested.
- **The call graph is static.** It misses calls through interfaces and through function values it cannot name, such as one read from a struct field or a map. A closure is followed, because a reached function's closures count as reached. Stored requests are the backstop.
- **A fingerprint covers function bodies, not package state.** A package-level `var` or type, and a function that runs only to fill a `var` or inside `init()`, are outside it. `declarations` reports such a change but cannot say which routes it touches.
- **A fingerprint sees one build.** A file excluded by build constraints on the machine running it is not hashed, so an edit behind a custom tag, or in `helper_linux.go` on a Mac, moves nothing.
- **Only Go source in the module is hashed.** Assembly bodies, files read through `//go:embed` and code in other modules, vendored ones included, are outside it.
- **Code the module's `./...` does not load has no body to hash.** That is any package whose path starts with the module path but that `./...` does not load: a nested module, a `testdata/` or `_`-prefixed directory that something imports, a module that a `replace` points at, a plain dependency under the same path prefix, and a `go.work` member. A route that reaches one gets no fingerprint and is listed as unresolved, so it counts as changed on every run.
- **A package const used only as an array length inside a type declared in a function is not in that function's fingerprint.** SSA prints the type by name, and the syntax holds only the const's name. `declarations` still reports the const.
- **Adding `oneOf` or `binding:"optional"` to an anonymous struct moves a fingerprint**, because the SSA listing prints the type with its tags. This only over-reports, and ARDE writes those tags on named structs.
- **Any edit to a cgo file can move a route that calls C**, even a comment, because cgo names its symbols from a hash of the file. This only over-reports.
- **Patterns match the callee as text.** An unanchored regex can match text inside a chained call, so anchor them.
- **Hand-edited `@` lines are overwritten** from the wiki on the next run.
- **`@version` renders as `0.0.0`**, because the repo has no source for it and ARDE will not invent one.
- **The comment-only rule is Go-only.**
- **No detection of critical changes** that alter a project's scope or can damage a service while the tests still pass.
- **No stored knowledge graph.** It was dropped. Only cross-repo questions such as "which services use this contract?" would reopen it.

## Workspaces

- A fork has a different URL, so it gets a new workspace and a full first run.
- A second branch in the same workspace counts as a change from the last successful run.
- The wiki is not exported into the target repo. A later `--export-wiki` flag could write a readable one-way copy.

## The judge

- It cannot test rules that have no HTTP effect.
- An arithmetic slip looks like a mismatch, and a lucky guess passes.
- An error code is exempt as unreachable only if the wiki holds an `unreachable` fact with code evidence. A wrong claim of unreachable is a known limit.
- It has not been run yet, so its catch rate is unknown.

## Cost

- **The cap can be overshot by one call.** A call's price is known only after it is paid for. The run stops once spending reaches the cap, but the last call can pass it by its own cost: at the shipped settings the worst single Opus 5.5 call costs $4.32, from 1M uncached input tokens and 16,000 output tokens.
- **A stream that breaks is charged only for what it had reported.** The first event says what the prompt used, so those tokens reach the trace and the cap. Output tokens arrive in the last event, so those written before the break are missing and the total reads low. A failure before the first event has nothing to charge. That the real API's first event carries the prompt's tokens comes from the SDK's types and has not been seen live, and that such a call is billed is assumed, not measured against a bill.

## The model API

Measured against the live API on 2026-11-02, with Opus 5.5.

- **Every request streams.** The SDK refuses a call that does not stream above about 21,333 `max_tokens`, and thinking counts against that limit. A dropped stream is not retried, because the SDK retries only before a stream starts, so it ends the run with a next action. See [decision 0011](decisions/0011-stream-every-request.md).

- **Strict tool use is refused for the repo profile and for the facts.** The API answers `For 'object' type, 'additionalProperties: object' is not supported` for the open `args` dict in the profile, and `Schema type 'oneOf' is not supported` for the fact union. Rewriting `oneOf` as `anyOf` did not help on Haiku 5.5: the API then said the compiled grammar was too large. Tools with these schemas must be sent with `strict=False`, and code validation holds the guarantee.
- **A refusal ends the agent, with no retry on another model.** The server-side fallback needs the beta client, and Haiku 5.5, which the classifier uses, has no fallback at all. The agent stops with the reason `refused`. The category and explanation go to `model_call` and `debug.log`, and `AgentStopped` exposes the category, so false positives can be counted on the first real run. No live refusal has been seen yet, so this rests on the SDK's types.
- **An error result with no text is refused by the API** (HTTP 400). The agent loop sends a stand-in text, so a tool that fails silently does not end the run.

## Not tested yet

As of Phase 0. Move an item out of this list when a test or a measurement covers it.

- Linux, Windows and amd64 hosts. The container was tried only on Docker for Mac (arm64).
- Agents producing patterns or facts. The API client itself has been run live, but no agent has produced these yet.
- A full run end to end, and any second repo. No-config onboarding is a claim, not a result.
- Run cost. The estimate of about $3 per first run with prompt caching rests on a review, not a measurement.

## Known costs

- The container image is about 980 MB, because it holds both the Go and Python toolchains.
- `swag` v2.0.0-rc6 is a pre-release. It is pinned, and the run's checks would surface a bad spec.

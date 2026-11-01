# Limitations

What ARDE does not do, and what has not been tested. Most of it comes from the [design](superpowers/specs/2026-11-01-arde-design.md). The writable-mount risk was found later, in a security review of the container experiment. Add to this page when you find a new limit.

## Trust and containers

- **Repos must be trusted.** The repo's code runs in the same container as ARDE and the API key. Removing `ANTHROPIC_*` from child processes limits accidents, but it is not a security boundary: code running as the same user could still read the parent's environment. This is reasoned, not tested.
- **A writable repo mount is an open risk.** The container mounts the local repo so it can write results back. Repo code running in the container could use that mount to tamper with the host repo, for example by planting a git hook that later runs on the host. This must be resolved before the container ships (Task 28 of the plan).
- **No sandbox for untrusted merge-request code.** A separate sandbox is a non-goal for now.

## What it understands

- **Go `net/http` only.** Other frameworks and other languages are not supported. Go idioms sit behind one interface so a second language can be added.
- **Names in facts are ASCII and plain.** A struct, field, type or error code with a non-ASCII letter, a generic type such as `Page[Product]`, or a `map` or `interface{}` response type fails validation instead of being written into a comment or tag. A service that uses them cannot be documented until the shape is allowed in the domain models.
- **Router groups are not resolved.** Patterns cannot see a group prefix. In a Gin-style sample the matcher reported `/ping` for `/v1/ping`. A live check should get a 404 and stop the run, but that is untested.
- **The call graph is static.** It misses calls through interfaces and function values. Stored requests are the backstop.
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

## Not tested yet

As of Phase 0. Move an item out of this list when a test or a measurement covers it.

- Linux, Windows and amd64 hosts. The container was tried only on Docker for Mac (arm64).
- Any real API call, and agents producing patterns or facts.
- Closing schemas around `oneOf`.
- A full run end to end, and any second repo. No-config onboarding is a claim, not a result.
- Run cost. The estimate of about $3 per first run with prompt caching rests on a review, not a measurement.

## Known costs

- The container image is about 980 MB, because it holds both the Go and Python toolchains.
- `swag` v2.0.0-rc6 is a pre-release. It is pinned, and the run's checks would surface a bad spec.

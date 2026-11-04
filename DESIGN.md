# ARDE design

ARDE (Agentic Repo Docs Engine) documents a Go `net/http` service. It adds swag annotations, generates `swagger.json` and `swagger.yaml` with swag, writes project docs, proves it changed no behaviour, and traces every step. One command: `uv run arde --repo <url or path>`.

## Flow

**The orchestrator is code; agents work inside it.** Step order and rules ("never commit before the checks pass", "no invented field") are code, not prompt text. A model works only where the next action depends on what it finds. Every agent is one generic loop: system prompt, tools, a submit tool whose validator returns errors as tool results (the trace's "corrections"), a turn limit and a shared cost cap.

```
resolve source → copy into a workspace → baseline go build, vet, test (red baseline stops, no paid call)
→ Discovery agent writes the repo profile → code validates it (patterns find routes, service starts)
→ code extracts the inventory (goinspect: routes, params, fields, errors via the type checker)
→ Explore agent builds the wiki of facts, probing the running "before" service
→ code renders annotations and tags from facts → swag generates the spec
→ Docs agent writes each narrative section from facts; code renders the reference tables
→ code checks → blind judge predicts replies from the docs alone
→ findings go to their owner, one repair round → commit on a new branch in the workspace clone
```

| Step | Kind | Sees | Tools |
|---|---|---|---|
| Discovery | agent | repo files | `list_files`, `read_file`, `grep`, `submit_profile` |
| Inventory | code | repo, profile | Go helper `goinspect` (go/packages, SSA) |
| Explore | agent | repo, profile, inventory, running service | repo tools, `http_probe` (GET/HEAD to the local port only), `add_facts`, `finish_facts` |
| Render, swag | code | facts | — |
| Docs | agent | facts only, never source | `submit_section` |
| Checks | code | input and output copies, both services, spec, wiki | — |
| Judge | agent | the pages and the spec only, and the running "after" service | `predict_and_call`, `calculate`, `note_clarity`, `finish` |

Why split this way: Discovery answers "how do I build, run and read this repo" once, and its profile is reused. Explore answers "what does this service do", and starts after the inventory exists, so its schema can list the real routes and names. Docs sees facts only, so every sentence traces to a checked fact.

## Context strategy

- **Discovery:** the file tree and what it chooses to read, plus ARDE's own notes on Go. Small and once per repo.
- **Explore:** a frozen system prompt (cached) asking about classes of behaviour any HTTP service has: responses no handler writes, validation order, input decoding, defaults, repeated params, rounding, what gets logged. Run-specific context (profile, inventory, current wiki) goes in the first user message. It reads code on demand rather than receiving the repo.
- **Docs:** one call per section, with the facts that section uses. No tools to read code.
- **Judge:** the pages and the spec in its first message, with the items it must try. No tool reads the code, the wiki or a probe, and each re-test is a fresh context with the same pages and one request.
- Repo text reaching a model is bounded and escaped. No prompt names a behaviour of one repo; an architecture test fails the build if `catalog-poc`'s identifiers appear in ARDE's code or prompts.

## Verification

Deterministic, by code:
- **No behaviour change:** every added line is one the renderer wrote or an allowed tag (`oneOf`, `binding:"optional"`); the AST is unchanged; `go build`, `go vet` and `go test` pass; the same requests give the same responses before and after.
- **Grounding:** spec, wiki and inventory compared both ways, so an invented or missing route, param or field fails. Live requests at every enum value, boundary and default, and responses validated against closed schemas.
- **Evidence:** a fact needs a quote that exists in a source file and holds code (not only a comment), or a probe that was really recorded. Each response status needs a probe that returned it, or an error code the code sends with it.
- **Docs grounding:** every URL, host and shell command in the docs exists in the repo (README, Makefile, Dockerfile, CI) or a recorded response.

By a model, scored by code: **the blind judge.** The checks above prove the docs valid; the judge tests whether they are good. Once they pass, a fresh agent that sees only the pages, the spec and the running "after" service predicts each reply (status, and fields at dotted paths) in the same tool call as the request, so code records the prediction before the request goes out. Code compares exactly, and refuses `finish` until every route, param and reachable error code has been tried; on a re-run, only those the changed facts link. A mismatch is re-tested once by a fresh context with the same docs and that one request: right, and it stays in the trace as noise; wrong again, and it is a finding. No model can dismiss one. Clarity notes never block; they are listed in the report's warnings. An error code is exempt only through an `unreachable` fact with code evidence, and every exemption is in the report. `--skip-judge` turns it off, and the report says so. No real model has run it yet, so its catch rate is unknown.

When a check fails: each finding has an owner. Explore findings, the judge's included, go back to Explore, then Docs rewrites; docs findings go to Docs; code findings stop the run at once. One repair round, then the run exits 1 with a report, and nothing is committed.

## Prompt injection

The repo is untrusted. Agents can't act: no shell or write tool, HTTP only to the local service, paths confined to the copy. The inventory comes from code, facts need verified evidence, code writes every annotation, and the docs agent never sees source. Prompts treat repo text as data and report suspected injections to the run report. Measured with instructions planted in a README, two comments and a reply body: nothing planted reached a fact, annotation, spec line or page, and all three agents reported all four plants.

## Re-runs and cost

A re-run reuses the saved profile (re-validated, so no Discovery) and wiki. Facts linked to a changed declaration, route or file are stale; Explore re-checks only those and any new route, param or field. Only sections whose facts changed are rewritten, and the renderer replaces its own blocks.

Measured on `catalog-poc` (Opus 5.5, prompt caching): a first run with the judge costs $2.34 in 8 minutes (the judge $0.29: 20 predictions, all right, one clarity note); a re-run with no change $0.48 in 74 s; a re-run after adding a param and a field $1.17 in 3.4 minutes. Details: `docs/decisions/first-real-run.md`.

## Known limitations

- Go `net/http` only. Routes are read statically: a wrapper that builds its path from more than its parameters, a route table looped at startup, or a router behind an interface stops the run with a named site rather than guessing.
- The service's own code runs on the machine during checks, as `make test` would. Repos must be trusted.
- A rule with no HTTP effect is checked only by its code quote, so a wrong reading of the code can reach the docs.
- Full list: `docs/limitations.md`.

## One more week

- **Skip and narrow re-runs further:** stop with no model call when nothing changed (today Explore still makes one call), let a classifier decide when a change is comments only, and re-validate a stale fact's evidence in code before asking Explore, since one edited line can stale most facts linked to a busy handler.
- **Run on every merge request:** the container already makes the one command portable; run the re-run above on the MR's head against the target branch's snapshot, and post the result as a commit or a review on the MR.
- **Keep the key away from the repo's code:** run the repo's build, tests and service as a separate user, with ARDE as root holding only SETUID and SETGID, so `/proc/<pid>/environ` and other repos' workspaces are out of its reach. Today the container keeps the host away from that code, not the key.
- **Services that need other services:** ARDE builds and starts only the Go binary, so a service that needs a database or another API won't come up. Let the profile name extra env vars for a test database, or start the repo's own dependency containers, which needs control of Docker and its security cost.
- **Routes that change data:** extract request bodies by type (the `Decode` target's struct, as replies are read now), back a success status with a quote that writes it (`w.WriteHeader(http.StatusCreated)`), and allow POST, PUT and DELETE probes only when the owner opts in and the service runs with throwaway state. Today such routes get params and errors but no success reply or request body.
- **Monorepo scale:** per-service profiles, inventory and explore scoped to the services a change touches, and a fact store keyed by declaration.

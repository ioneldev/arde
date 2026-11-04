# ARDE design

ARDE documents a Go `net/http` service: swag annotations, a spec swag generates from them, project docs, proof that behaviour is unchanged, and a trace. One command, in a container: `make run REPO=<url> BRANCH=<b>`.

## Flow

**Code orchestrates; agents work inside it.** Step order and rules, such as "no commit before the checks pass", are code that no model can skip; a model works only where the next step depends on what it finds. Every agent runs one loop: a system prompt, tools, a submit tool whose validator returns errors to fix (the trace's "corrections"), a turn limit and a shared cost cap. An agent ends only when code accepts what it submits.

```
baseline: go build, vet, test (red stops before any paid call)
→ Discovery: repo profile → code: inventory from Go's type checker
→ Explore: facts with evidence, probing the running service
→ code: annotations from facts → swag: the spec
→ Docs: pages from facts → 7 code checks → blind judge
→ findings to their owner, one repair round → commit on a new branch
```

| Step | Kind | Sees | Tools |
|---|---|---|---|
| Discovery | agent | repo files | `list_files`, `read_file`, `grep`, `submit_profile` |
| Inventory | code | repo, profile | `goinspect`: go/packages, the type checker, SSA |
| Explore | agent | repo, inventory, service | repo tools, `http_probe` (GET/HEAD, local), `add_facts`, `finish_facts` |
| Render | code | facts | swag |
| Docs | agent | facts, never source | `submit_section` |
| Checks | code | copies, services, spec | — |
| Judge | agent | pages, spec, service | `predict_and_call`, `calculate`, `note_clarity`, `finish` |

Why this split: Discovery's profile is learned once and reused. Explore starts after code has listed what exists, so it can only describe real names. Docs sees only checked facts. The judge never saw the code, so it reads the docs as a newcomer would. No extra route, so the diff holds no new executable code ([decision 0008](docs/decisions/0008-no-extra-route.md)). A re-run re-validates the saved profile, re-checks only the facts linked to changed code, and rewrites only the pages whose facts changed. A first run with the judge costs $2.34 in 8 minutes on `catalog-poc` ([all runs](docs/decisions/first-real-run.md)).

## Context strategy

- **Discovery:** the file tree and the files it chooses to read, once per repo.
- **Explore:** a cached system prompt about behaviour any HTTP service has; the profile, inventory and wiki in its first message; code read on demand.
- **Docs:** one call per page, with its facts and the replies they cite.
- **Judge:** the pages, the spec and the items to try. Each re-test is a fresh context.
- Repo text is data: bounded, escaped and fenced, and no prompt names any repo.

## Verification

By code:
- **No behaviour change:** only lines the renderer wrote, or allowed tags, are added; build, vet and test pass; the same requests get the same replies.
- **Grounding:** spec, wiki and inventory compared both ways; every allowed value, limit and default requested live, with replies checked against closed schemas.
- **Evidence:** each fact quotes real code from a build file, or a probe recorded in this run.
- **Docs grounding:** every URL and command in the docs is in the repo or a reply.

By a model, scored by code: **the blind judge** tests whether the docs are good, not just valid. It predicts each reply before code sends the request; code compares exactly, refuses `finish` until every route, param and reachable error code is tried, and has a fresh context re-test each mismatch: right, it is noise; wrong again, a finding.

A finding goes to its owner (Explore, then Docs; Docs; or code, which stops the run): one repair round, then exit 1 and no commit. A hostile repo meets four layers of defence ([limitations](docs/limitations.md)).

## Known limitations, and one more week

- Go `net/http` only; routes are read statically, and an odd shape stops the run at its line.
- Routes of every method are documented, but ARDE sends only GET and HEAD, so it never changes data: a POST, PUT or DELETE route gets its params and error replies but no success reply, and request bodies are not read.
- The judge has two real runs and no measured catch rate; a rule with no HTTP effect is checked only by its quote; SDK retries are not in the trace.
- The repo's code runs in the container, next to the key, and a service that needs a database runs without one. Full list: [docs/limitations.md](docs/limitations.md).

**One more week:** re-run on each merge request against the target branch's snapshot, posting a review; for a 500k-line monorepo, a profile and inventory per service, and Explore scoped to what a change touches; measure the judge on planted errors, and trace every retry; run the repo's code as a separate user; read request bodies.

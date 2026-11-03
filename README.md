# ARDE

ARDE (Agentic Repo Docs Engine) documents a Go `net/http` service without manual edits. It adds swag annotations to the code, generates swagger.json and swagger.yaml in the service's docs folder with swag, writes project documentation, proves it changed no behaviour, and leaves a trace of every step, model call and cost.

## Run it

You need Python 3.12, [uv](https://docs.astral.sh/uv/), Go 1.27 and git. The first run fetches swag v2.0.0-rc6, pinned by `tools/swag/go.sum`.

```sh
cp .env.example .env          # then put your key in it
uv sync
uv run --env-file .env arde --repo https://github.com/mihai-iorga-emag/catalog-poc --branch main
```

That is the one command. It clones a fresh copy into ARDE's workspace (`~/.arde`, or `ARDE_HOME`), never touches the source, and commits the result on a new branch in the workspace clone. It prints the run folder, which holds `report.json` and `trace.jsonl`.

| Variable | Needed | What |
|---|---|---|
| `ANTHROPIC_API_KEY` | yes | The Claude API key. Read from the environment only, never written to a file ARDE makes. |
| `ARDE_HOME` | no | Where workspaces and runs live. Default `~/.arde`. |

Options: `--json` prints only the run report on stdout, `--max-usd N` caps the run's cost (default 10), and `--skip-judge` skips the blind judge, and the report warns that it did not run. Exit codes: 0 success, 1 a check failed, 2 bad input, 3 the repo needs a profile fix, 4 the cost cap was hit. Every error names the next action.

Run it again after changing the service: it reuses the saved profile and wiki, re-checks only the facts linked to changed code, and rewrites only the sections whose facts changed.

## What one run produced

- The annotated code, spec and docs, as one commit on top of `catalog-poc`: branch `catalog-poc-docs`.
- Its trace and report: [examples/first-run/](examples/first-run/README.md).
- What it cost and found: [docs/decisions/first-real-run.md](docs/decisions/first-real-run.md).

## Read next

- [DESIGN.md](DESIGN.md): the flow, the context strategy, verification, limits, and what one more week would add.
- [docs/index.md](docs/index.md): the wiki, with the architecture, glossary, limitations, decisions and the phase guides.
- [AGENTS.md](AGENTS.md): the entry point for agents working on this repo.

## Develop

```sh
make check         # ruff, strict mypy, the fast tests
make integration   # tests that build and run Go services
make eval          # opt-in tests against the real API; costs money
```

## How AI assistants were used

Claude Code wrote most of this code under my direction. I designed ARDE with it over several sessions, approving each decision; the design and the plan are in `docs/superpowers/`. Implementation then ran task by task: a fresh agent implemented each task test-first in its own git worktree, independent agents reviewed each phase (architecture, code, bug hunting, security), and every finding and ruling is recorded in the phase guides in `docs/decisions/`. I own the decisions; the guides list every one made without me.

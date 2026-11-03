# ARDE

ARDE (Agentic Repo Docs Engine) documents a Go `net/http` service without manual edits. It adds swag annotations to the code, generates swagger.json and swagger.yaml in the service's docs folder with swag, writes project documentation, proves it changed no behaviour, and leaves a trace of every step, model call and cost.

## Run it

You need Docker with Compose, an Anthropic API key and the network. The image holds everything else, pinned: Go 1.27.1, Python 3.12, uv, git, and `goinspect` and swag v2.0.0-rc6 built from `tools/`.

```sh
cp .env.example .env          # then put your key in it
docker compose build
docker compose run --rm arde --repo https://github.com/mihai-iorga-emag/catalog-poc --branch main
```

That is the one command. It clones a fresh copy into ARDE's workspace, never touches the source, and commits the result on a new branch in the workspace clone. The workspace lives in the `arde-home` volume, so the next run remembers the profile and the wiki. The run prints its run folder, which holds `report.json` and `trace.jsonl`, and the branch and folder of its commit. To take that branch home, bundle it and fetch it into your own clone:

```sh
docker compose run --rm --entrypoint git arde -C <workspace clone> bundle create - <branch> > result.bundle
git fetch result.bundle <branch>:<branch>
```

To document a repo on your machine, set `REPO_PATH` in `.env` and run `docker compose run --rm arde`. The repo is mounted read-only and copied, uncommitted work included, and nothing is written back. The repo's build, tests and service run inside the container, next to the key, so run only repos you trust ([limitations](docs/limitations.md)).

| Variable in `.env` | Needed | What |
|---|---|---|
| `ANTHROPIC_API_KEY` | yes | The Claude API key. It reaches the container at run time and is never in the image or in a file ARDE makes. |
| `REPO_PATH` | no | The local repo `docker compose run --rm arde` documents. |
| `ARDE_UID`, `ARDE_GID` | no | The user the container runs as, default 1000. On a Linux host, your `id -u` and `id -g`, so ARDE can read files only you can read. |
| `GIT_AUTHOR_NAME`, `GIT_AUTHOR_EMAIL` | no | The author of the result's commit. ARDE stays its committer. |

Options: `--json` prints only the run report on stdout, `--max-usd N` caps the run's cost (default 10). Exit codes: 0 success, 1 a check failed, 2 bad input, 3 the repo needs a profile fix, 4 the cost cap was hit. Every error names the next action.

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

Developing ARDE needs Python 3.12, [uv](https://docs.astral.sh/uv/), Go 1.27.1 and git on the host. The first native run builds `goinspect` and swag, which fetches the modules `tools/swag/go.sum` pins.

```sh
uv sync
uv run --env-file .env arde --repo <url or path>   # workspaces in ~/.arde, or ARDE_HOME
make check         # ruff, strict mypy, the fast tests
make integration   # tests that build and run Go services, and the container's tests when Docker runs
make eval          # opt-in tests against the real API; costs money
```

To run the same suites on Linux, build the image's `dev` stage: `docker build --target dev -t arde-dev .`, then `docker run --rm arde-dev make check`, or `make integration`, which fetches the Go modules the tests build from.

## How AI assistants were used

Claude Code wrote most of this code under my direction. I designed ARDE with it over several sessions, approving each decision; the design and the plan are in `docs/superpowers/`. Implementation then ran task by task: a fresh agent implemented each task test-first in its own git worktree, independent agents reviewed each phase (architecture, code, bug hunting, security), and every finding and ruling is recorded in the phase guides in `docs/decisions/`. I own the decisions; the guides list every one made without me.

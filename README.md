# ARDE

ARDE (Agentic Repo Docs Engine) documents a Go `net/http` service without manual edits. It adds swag annotations to the code, generates swagger.json and swagger.yaml in the service's docs folder with swag, writes project documentation, proves it changed no behaviour, and leaves a trace of every step, model call and cost.

## Run it

You need Docker with Compose, `make`, an Anthropic API key and the network. The image holds everything else, pinned: Go 1.27.1, Python 3.12, uv, git, and `goinspect` and swag v2.0.0-rc6 built from `tools/`.

```sh
cp .env.example .env    # then put your key in it
make image
make run REPO=https://github.com/mihai-iorga-emag/catalog-poc BRANCH=main
```

That is the one command. It clones a fresh copy into ARDE's workspace, never touches the source, runs every step in the container, and commits the result on a new branch in the workspace clone. The workspace lives in the `arde-home` volume, so the next run remembers the profile and the wiki.

| Command | What it does |
|---|---|
| `make preflight` | Checks the `docker` command, the Compose plugin, a running daemon, `.env` and the key, and changes nothing. |
| `make image` | Builds the image. Run it again after any change to ARDE. |
| `make run REPO=<url> BRANCH=<branch>` | Clones a remote repo and documents it. |
| `make run REPO=<folder>` | Documents a repo on your machine. It is mounted read-only and copied, uncommitted work included, and nothing is written back. |
| `make run` | Documents the repo at `REPO_PATH` in `.env`. |
| `make runs` | Lists the runs in the volume, with each one's source, status, cost, time and branch. |
| `make extract` | Copies the latest run out of the volume into `runs/<run id>/`. `RUN=<id>` takes another run, and the start of its id is enough. |

Every target first checks what it needs, and names the fix for anything missing, before Docker starts. For `make run` that includes `.env` setting `ANTHROPIC_API_KEY`: the check asks only whether the key is set, never reads it out, and Compose alone hands it to the container.

`ARGS` passes options to ARDE, as in `make run REPO=<folder> ARGS=--skip-judge`: `--json` prints only the run report, `--max-usd N` caps the run's cost (default 10), and `--skip-judge` skips the blind judge, and the report warns that it did not run. Exit codes: 0 success, 1 a check failed, 2 bad input, 3 the repo needs a profile fix, 4 the cost cap was hit. Every error names the next action.

The repo's build, tests and service run inside the container, next to the key, so run only repos you trust ([limitations](docs/limitations.md)). Run ARDE again after changing the service: it reuses the saved profile and wiki, re-checks only the facts linked to changed code, and rewrites only the sections whose facts changed.

| Variable in `.env` | Needed | What |
|---|---|---|
| `ANTHROPIC_API_KEY` | yes | The Claude API key. It reaches the container at run time and is never in the image or in a file ARDE makes. |
| `REPO_PATH` | no | The repo `make run` documents when no `REPO` is given. |
| `ARDE_UID`, `ARDE_GID` | no | The user the container runs as, default 1000. On a Linux host, your `id -u` and `id -g`, so ARDE can read files only you can read. |
| `GIT_AUTHOR_NAME`, `GIT_AUTHOR_EMAIL` | no | The author of a URL run's commit in the workspace clone. ARDE stays its committer. |

## Patch your repo with a run's changes

A run never writes to your repo: its results stay in the `arde-home` volume. `make extract` brings a run out, and `git apply` puts its changes into your repo. Run these from ARDE's folder.

1. **Run ARDE, then extract the run.** The extract prints the run's id and where it put it.

   ```sh
   make run REPO=~/code/my-service
   make extract
   ```

   `runs/<run id>/` now holds `report.json`, `trace/trace.jsonl`, `trace/debug.log`, the service logs, each file the run changed, whole, under `changed/`, and `changes.patch`.

2. **Read what it changed.**

   ```sh
   git apply --stat runs/<run id>/changes.patch    # each file, with the lines added and removed
   less runs/<run id>/changes.patch
   ```

   Read `report.json` too: its `status`, its `warnings`, and its `revision`, the commit the run documented.

3. **Check that the patch applies, then apply it.** Give the patch as a full path, because `git -C` moves into your repo before it reads the path.

   ```sh
   git -C ~/code/my-service apply --check "$PWD/runs/<run id>/changes.patch"
   git -C ~/code/my-service apply "$PWD/runs/<run id>/changes.patch"
   ```

4. **Review, test and commit it, as any other change.**

   ```sh
   git -C ~/code/my-service status    # the changed files, and the new docs/
   git -C ~/code/my-service diff
   ```

The patch fits the repo as the run read it: at its `revision`, with the uncommitted work it had then, and with the same line endings. A run on a CRLF checkout makes a CRLF patch, which stops applying once git rewrites those files as LF; `git -C <repo> ls-files --eol` shows what the files hold now, `w/crlf` or `w/lf`. If `--check` refuses the patch, the repo has changed since the run, so run ARDE again and extract the new run. For a URL run, apply it to a clone checked out at that revision: `git clone <url> my-service`, then `git -C my-service checkout <revision>`.

## What one run produced

- The annotated code, spec and docs, as one commit on top of `catalog-poc`: branch `catalog-poc-docs`.
- Its trace and report: [examples/first-run/](examples/first-run/README.md).
- The same command run in Docker, with the judge's first mismatch: [examples/container-run/](examples/container-run/README.md).
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

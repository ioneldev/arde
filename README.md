# ARDE

ARDE (Agentic Repo Docs Engine) is a command-line tool that documents a Go `net/http` service without manual edits. It adds swag annotations, generates an OpenAPI file with a standard tool, writes project documentation, proves it did not change behaviour, and leaves a trace of what it did.

**Status:** under construction. Only the repo skeleton, the quality gate and the docs wiki exist so far, so there is no command to run yet.

## Develop

You need Python 3.12 and [uv](https://docs.astral.sh/uv/).

```sh
uv sync
make check
```

`make check` runs ruff, strict mypy and the fast tests. It is the same gate for people, agents and CI.

## Read next

- [docs/index.md](docs/index.md): the wiki, with the architecture, glossary, limitations and decisions.
- [AGENTS.md](AGENTS.md): the short entry point for agents working on this repo.

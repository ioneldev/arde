# AGENTS.md

ARDE (Agentic Repo Docs Engine) documents a Go `net/http` service: it adds swag annotations, generates an OpenAPI file, writes project docs, proves it changed no behaviour, and traces the run. This file is the entry point for agents working on this repo. The wiki starts at [docs/index.md](docs/index.md).

## Commands

- `make check`: ruff, ruff format check, strict mypy and the fast tests. Run it before every commit.
- `make integration`: tests that need the Go toolchain.
- `make eval`: tests that call the Anthropic API and cost money. It reads the key from `.env`.
- `uv run pytest tests/test_architecture.py -q`: run one test file.

## Layers

Dependencies point inward. Each layer is a folder under `src/arde/`, created by the task that first needs it. [docs/architecture.md](docs/architecture.md) has the full map.

- **domain**: frozen Pydantic models and pure rules (facts, inventory, findings, the stale rule).
- **application**: the pipeline, the agent roles, and the ports (interfaces) they need.
- **checks**: one file per verification check.
- **adapters**: everything that touches the outside world: the LLM client, Go, the workspace, the service runner, the tracer.
- **cli**: the command line, which wires adapters into the application.

A small Go helper, `goinspect`, does the AST work and the call graph, because those libraries exist only in Go.

## Rules

- The domain imports no adapter and no application code. The application imports no adapter and reaches the outside world through ports, wired in the cli.
- Go-specific code (`goinspect`, `gofmt`, `swag`, `go build`) stays in the Go language adapter and the helper. The orchestrator never touches Go directly.
- Code enforces rules. The LLM never writes a comment or a spec line, and agents get no shell or write tools.
- Tests come first. Unit tests use the fake LLM and need no network or Go. Mark Go tests `integration` and paid tests `eval`.
- Comments explain why, never what. Past two positional arguments, pass a model.
- Every user-facing error names the next action.
- The API key travels as a `SecretStr`. The tracer redacts it by exact match in `trace.jsonl` and `debug.log`, and `tests/conftest.py` scrubs any 16 characters of it from pytest reports. Only the adapter, the tracer and `tests/conftest.py` read the plain value, and a test enforces that. Never commit `.env`.
- Docs name only paths that exist, and `make check` fails on a broken link or a missing path. Add a path to a page when its file lands, not before. Phase specs in `docs/specs/` are the exception: they may name future paths, but their links must work.

## To change X, go here

- A data shape or a pure rule: the domain layer.
- The order of steps, an agent's prompt, a limit: the application layer.
- A verification rule: the checks layer. One file, and a test proving it fires.
- Anything about Go: the Go language adapter, or the helper for AST and call-graph work.
- A decision: add a file to `docs/decisions/` and link it from [docs/index.md](docs/index.md).
- A known limit: [docs/limitations.md](docs/limitations.md).

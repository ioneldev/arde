# The deliverable run

ARDE's run on `https://github.com/mihai-iorga-emag/catalog-poc` at `main` (`0538694`), from a fresh workspace, on 2026-11-03. It succeeded in 409 s for $1.96 and committed `483ec2f`, which this repo holds as the branch `catalog-poc-docs` on top of catalog-poc's own history.

- `report.json`: the run report: status, cost and calls per role, warnings, and the paths the run changed.
- `trace.jsonl`: one event per line: the run start (source commit, tool versions, model ids, prompt hashes), each step, every model call with its tokens, cost and latency, every tool call, each correction where code refused a submission and the model fixed it, the check results, and the per-role totals at the end.

`debug.log`, which holds full prompts and responses with the repo's source, stays local and is not committed.

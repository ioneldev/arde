**Status: Group F is done.** 13 commits on `phase-1`, on top of `722902e`. Nothing is pushed. All three gates passed on the exact tree before every commit, and the five-run load test passed. `.env` was not touched. catalog-poc was used only as a `cp -R` copy with its `.git` removed. No subagents were spawned. Every log is in `/private/tmp/claude-501/-Users-ionel-work-personal-emag/0f37fc47-1c0a-42ed-bd77-f0a6f96a2ead/scratchpad/fixF`: `<item>-check/-integration/-gotest.log`, `*-red*.log`, `compare/`, `final/`.

## Commits
| Commit | Item |
|---|---|
| `6937d46` | F1: a wrapper that writes its path or method parameter stops the run (`written_at` names the write) |
| `347ed73` | F2: a registrar is read only at its direct calls (`used as a value`, `called through an interface`) |
| `0bb9b31` | F3: a handler type's methods are in the hash, and a struct handler has an anchor |
| `dafb06c` | F4: the wrapper, its site's functions and the call's other arguments are roots |
| `37eaec9` | F5: the new wraps rules, `handler_identity`, the anchor guard, `Route.shares_anchor_with` |
| `6d0335a` | F6: a mux's ServeHTTP counts as the mux; a form-1 wrapper handed a mux is a mount; `mounts a mux` |
| `9be50a4` | F6: `string(m)` is form 3; `method is empty` |
| `e6d9d38` | F6: constants in the hash (`constants_hash`), the package-var slice line, glossary |
| `28cfb43` | F7.F1: a test name keeps its text unless every run of it passed |
| `a79c547` | F7.F2: one `UNPRINTABLE_RANGES` (Cc, Cf, Zl, Zp) in `domain/base.py` |
| `c16b715` | F7.F3: the links gate calls `os.lstat` and fails closed |
| `aeb87dd` | F7.F4: the N3 paragraph and the hard-link sentence |
| `9a82826` | Follow-up from the base comparison: a route with no roots gets no `constants_hash` |

## Test counts
| After | make check | integration | Go top-level |
|---|---|---|---|
| base | 1962 | 263 | 217 |
| F1 | 1963 (+1) | 267 (+4) | 219 (+2) |
| F2 | 1965 (+2) | 271 (+4) | 222 (+3) |
| F3 | 1965 | 274 (+3) | 225 (+3) |
| F4 | 1965 | 276 (+2) | 226 (+1) |
| F5 | 1967 (+2) | 281 (+5) | 228 (+2) |
| F6 mounts | 1968 (+1) | 284 (+3) | 230 (+2) |
| F6 methods | 1969 (+1) | 286 (+2) | 232 (+2) |
| F6 constants | 1975 (+6) | 288 (+2) | 233 (+1) |
| F7.F1 | 1977 (+2) | 289 (+1) | 233 |
| F7.F2 | 2129 (+152) | 289 | 233 |
| F7.F3 to HEAD | 2130 (+1) | 289 | 233 |

- **Sums:**
  - make check: 1962+1+2+2+1+1+6+2+152+1 = 2130.
  - integration: 263+4+4+3+2+5+3+2+2+1 = 289.
  - Go: 217+2+3+3+1+2+2+2+1 = 233.
- **F7.F2's +152:** the Unicode-database test 1, free text 54 (9 characters × 6 fields), names 81 (9 × 9 targets), one_line 9, child_text 6, the builder's path test 1.
- **Collected:** 2130 + 297 deselected = 2427 = 289 + 2138.
- **Load test** (on `9a82826`): `make check` ran 5 times, 2130 passed and exit 0 each time. `make integration` looped 3 times alongside, 289 passed and exit 0 each time.

## Each RED, on the code before its fix
- **F1:** all 8 Go write cases (field prefix, `"/v1"`, `+=` in a branch, `method=`, redeclaration, range, `&path`, a closure) gave no unread site. The 4 integration services (r10, r1, r1b, r2) did not raise.
- **F2:** all 11 use cases and the matched interface call gave no unread site. r3, both of its forms, and my interface probe did not raise.
- **F3:**
  - Go fingerprints: the edit left the hash unchanged, and `(*items).ServeHTTP` was missing from reach.
  - Go routes: no anchor or roots in 10 cases.
  - h2: the edits moved `set()`, and `GET /plain` was `('(inline)', None, ())`.
- **F4:** the roots lacked the wrapper, `writeJSON` and `requireAdmin`. The h4 and r8 hashes did not move.
- **F5:**
  - `spa(parcels)` and `adapted(d.failing)` anchored at the factory, and 5 middleware spellings anchored at the middleware.
  - Every identity was empty.
  - Integration: h1 with `next(w, r)`, h2's struct middleware, h3's guard and the single spa route all failed.
- **F6:**
  - mt kept `ANY /b/` and `ANY /c/`, and the mux ServeHTTP cases gave `[/stock/ /stock/items]`.
  - mw1 stopped with `registers nothing`.
  - r7 stopped with `builds its path`, and the empty method at another module's router did not stop.
  - The constants were empty, the h4 `paged(5→9)` edit moved nothing, and three routes with different constants shared one hash (assert `1 == 3`).
- **F7.F1:** the unit test's text was `''`. A real `go test` of `par` printed only RUN/PAUSE/FAIL.
- **F7.F2:** 88 failures, among them U+202E raw in the message.
- **F7.F3:** did not raise.
- **Follow-up:** the inline route's constants hash was not empty.

## Base comparison
`722902e` against `9a82826`, on shop, the catalog-poc copy, parcels tracker and console, and library. `go test` prints its lines in a varying order, so the output's lines were sorted.

**28 differences, all asked for:**
- 12 `constants_hash: ""` and 12 `shares_anchor_with: []` fields, one of each on every route (F6, F5).
- Shop's two routes gained the roots `router.handle` and `router.get`, and their two hashes changed (F4).

Everything else is identical: the route reasons, the declaration hashes, the routes moved and declarations changed by the one-token edit, and `run_checks`. No catalog, parcels or library hash moved: their handlers are bare names reached directly, and they convert no module type with methods to an interface.

The control changed `id < 1` to `id < 2` in a copy and found 4 differences: the `handlers.product` declaration hash, the `GET /products/{id}` hash, the test output and `test_ok`. Two HEAD dumps differed only by the follow-up fix.

## What the brief or the reports got wrong
- **apiFunc (R119):** "MakeInterface covers apiFunc" is false for `apiFunc(s.orders)` and for a factory that returns its own concrete type. That conversion happens at the registration, which no walk reaches. I added a site rule: every method of each module type the handler hands to an interface becomes a root. One existing test pinned the old roots for `failable(d.failing)`.
- **The interface case (F2):** with `\.get$` the pattern does read `r.get(...)`, but as GET while the wrapper registers POST. That is a silent wrong method, so it stops in both cases.
- **mt:** F5 alone removed the `secured(a)` and `passed(d)` rows. Only the ServeHTTP method values needed F6.
- **k2d:** E's test pinned that the passing run's text is dropped. With one name the runs cannot be told apart, so I flipped it.
- **Unicode set:** scanning the categories at import cost 0.235 s. I hardcoded the 23 ranges instead, and a test checks them against `unicodedata` 15.0.
- **Shared anchors (my design choice):** guarded routes keep their roots and hashes, and the new `Route.shares_anchor_with` is the reason the brief asked for. The guard compares the functions, variables (by declaration) and constants each handler holds. Because one group gets None as a whole, a plain handler at two paths also loses its anchor when it shares it with a different handler.

## Beyond the brief
- F2's registrars include net/http's own `Handle` and `HandleFunc`. So `add := mux.HandleFunc`, or a mux behind an interface, now stops; limitations line 43 had listed that as a silent loss.
- A method expression called directly, `(*app).get(a, …)`, stops as `used as a value`.

## Shapes no rule covered
- A form-2 or form-3 wrapper handed a mux, or one that wraps the mux in a factory that does not serve it, stops with `mounts a mux`.
- Middleware that fills a variable and returns it is a factory, so its docs go on it, and the guard catches any shared anchor.
- An anonymous struct that embeds a wrapper's type is not checked against interfaces (documented).
- An increment cannot apply to a string parameter, so F1 does not look for one.

## Not verified
- Linux.
- curl against any service.
- The boundary probes g1–g6: they write into the reviewer's folder, so I covered `par` with my own tests instead.
- Mutation testing.
- A generic type as an interface's carrier (matched by name only).
- The cost of the carrier scan on a large repo.
- Promoted methods from another module's embedded type.

**Group F boundary fixes (28cfb43, a79c547, c16b715, aeb87dd): all four hold. I found three Minors and no Critical or Important.**

I tested at HEAD `9a82826`, one commit after aeb87dd. Probes and logs are in `/private/tmp/claude-501/-Users-ionel-work-personal-emag/0f37fc47-1c0a-42ed-bd77-f0a6f96a2ead/scratchpad/p1f/boundary/` (`probes/`, `logs/`). I used a fake secret and `GOPROXY=off`. I didn't touch `.env` or catalog-poc. The arde tree is unchanged (`git status` shows only the existing untracked review files).

## Findings

**1. Minor: a route path with ZWNJ, ZWJ, a direction mark or a soft hyphen now stops the whole run, and the message does not say which character.** This is documented, and R123 accepted it. I'm flagging it because it was measured on realistic text.
- **Input:** `mux.HandleFunc("GET /كتاب‌ها", index)`, Persian "books" written with a ZWNJ. The same happens with `/team/\U0001F469‍\U0001F4BB`, with `/שלום‏` and with `/inter­national`.
- **What ARDE did:** `RouteNotUnderstoodError`, exit 3: "Cannot read the route registered at main.go:9: path: Value error, must hold no control, format or separator character… remove it." The next action is "Register the route that way". It names neither the code point nor its position, and a Persian path can't lose its ZWNJ.
- **Same cause, other fields:**
  - A json tag with a ZWNJ drops the whole response field, listed in `dropped` as "unreadable json_name".
  - A Go file name with a ZWNJ stops the run.
  - Go identifiers can't hold Cf (`go build`: "invalid character U+200D in identifier"), so struct and field names are safe.
- **Should:** name the code point and offset in the message. Better, refuse only the bidi controls (U+202A–202E, U+2066–2069) in route paths, or escape the rest when shown.
- **Related:** `RouteNotUnderstoodError` is a `ProfileProblem`, so this goes back to Discovery, and no profile change can fix it.

**2. Minor: two returned values carry raw repo file names, outside the escaping.** Neither is printed anywhere in `src/` yet, so there is no live message path.
- **Input:** `ign‮\x1b[2J\nx​_test.go` containing `//go:build ignore`. For the second value, the same name holds `func F` and differs between old and new.
- **What ARDE did:**
  - `CheckResult.ignored_files` returned `('ign‮\x1b[2J\nx​_test.go',)`: raw ESC, U+202E, a newline and U+200B.
  - `changed_declarations` returned `['d‮\x1b[2J\nz​.go::func F']`, equally raw.
  - The sibling `unchecked_modules` came back escaped (`mod\\u202e\\u001b[2Jdir`).
- **Should:** escape with `printable_name` where the value is made, as `unchecked_modules` and `tests_skipped` are, or state in the port that the printer must escape. For `changed_declarations`, escaping at the source would break matching against inventory ids, so the printer is the right place.

**3. Minor: the gate's next action is wrong for two errors it now catches, and a long path loses its end.**
- **Input A:** a file removed between listing and `lstat`. I simulated the race by deleting it inside a patched `os.lstat`.
- **What ARDE did:** `RepoNotReadableError`, exit 2: "No such file or directory: …/gone.go". The next action is "Make that folder readable, or remove it". It should say the repo changed while it was read, so run again.
- **Input B:** a 934-character folder path plus a 240-character link name, 1,175 characters in all, against a PATH_MAX of 1,024.
- **What ARDE did:** exit 2 with "File name too long: <first 200 characters of the path>". `printable_name` keeps the start and cuts the end, which is the part that matters. The advice is again "make that folder readable". It should show the end of the path and say to shorten or move the repo.
- **Smaller:** in the 0444 case the message names `d/go.mod`, a file, and then says "that folder".

## Checked and found right

**28cfb43, failing test text**
- **Interleaved case:** Rerun on a copy of `repos/m1/par`. `PAR_FAIL_TEXT_1`, `PAR_FAIL_TEXT_2` and `--- FAIL: TestSame` are all kept, and `PAR_PASS_TEXT` is kept too, which is accepted. `test_ok` is False and `tests_run` and `tests_listed` are both 9.
- **Same name in two packages:** the failing text is kept and the passing text dropped.
- **Subtests:** a real failing subtest keeps its text and its parent's, a passing sibling is dropped, and a skip reason is kept. A passing parent with a passing child is dropped entirely.
- **`-count`:** `run_checks` always passes `-count=1`, so I fed `measured_tests` real `go test -json -count=3` streams that I generated myself. With run 2 failing, all three runs' text is kept. The same holds for parallel runs under `-count=3`.
- **Hand-built streams:** a failing subtest under a passing parent keeps the subtest's text. A run with no end, a pass with no run, and extra passes beside a fail are all kept.
- **Bound:** a 3 MB passing run beside a failing run gives 1,048,653 bytes after `with_kept_tail`. The tail is kept, so the first failing line can be pushed out; I judge that a low trade-off.

**a79c547, one character set**
- The 23 ranges equal Cc, Cf, Zl and Zp exactly on Python 3.12.3 (Unicode 15.0): 237 code points, none missing, none extra.
- I checked all 0x110000 code points against each regex. `one_line` and `SingleLine` match the set. `visible_text` matches the set minus tab and newline. `_PLAIN_CHAR` matches the set plus `\s` and the three quotes. The escape widths are right.
- `grep` of `src/` finds no other control-character list.
- Every route from child output to a message goes through `bounded_text`, and every route from repo text goes through `one_line` or `printable_name`. `printable_name` is a separate mechanism (`json.dumps`) and is stricter.
- Not covered by the brief's set: invisible non-Cf characters such as U+3164, U+2800, U+034F and the variation selectors pass through.
- I only checked Unicode 15.0.

**c16b715, `os.lstat` in the gate**
- A link in a folder with mode 0444 gives `RepoNotReadableError`, exit 2, and so do 0644, 0000 and 0111. Mode 0555 gives `LinkLeavesRepoError`. A plain-file folder with mode 0444 is also refused, as designed.
- `detects`, `extract_inventory`, `route_fingerprints`, `run_checks` and `built_service` all stop on the `go.work` shape from the earlier review (`go.work` using `./d`, `d/go.mod` linked to the secret, `d` at 0444). The secret appeared 0 times.
- Dangling and loop links inside the repo pass.

**aeb87dd and a79c547, docs**
- All three changed lines in `docs/limitations.md` are true of the code.
- I re-measured the in-call plant at HEAD. A passing test links `leak.go` to the fake secret, and `go test -list` then raises `GoCommandError` quoting `leak.go:1:1: expected 'package', found FAKESECRET_LINE_ONE`, one secret line. The next call's gate refuses the link.
- The clone sentences match `git help clone`: a local path hard-links `.git/objects`, and a URL never does. This is from the manual, not measured, because clones were not allowed.
- Nit: "cannot … break a line" sits beside the exception for the line break.

I ran the touched test files (`test_base`, `test_child_text`, `test_repo_links`, `test_go_test_events`, `test_models`, `test_checks_measured`) with the cache and bytecode writes turned off. 694 passed.
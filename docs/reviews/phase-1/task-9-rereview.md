# Task 9 re-review: fix round 1

Base 6891084, head 04d96ea. The checkout's files for this task are byte-identical to 04d96ea (checked with `cmp` against `git show`); `HEAD` is now f145fe7, a docs commit above it. I ran everything on a copy in `/private/tmp/claude-501/-Users-ionel-work-personal-emag/0f37fc47-1c0a-42ed-bd77-f0a6f96a2ead/scratchpad/rr9`, macOS, Python 3.12.3, real processes and real signals. Scripts and outputs are there. No git inside `catalog-poc`, no `.env`, no subagent. Nothing in the checkout was written.

Verified by running: the two test files (71 passed, 2 `shop` tests deselected), `ruff check`, `ruff format --check` and `mypy src` (all clean), 17 mutants, and the experiments below. Inferred, not run: Linux (not run at all), and the full `make check` / `make integration` (not re-run; the copy lacks `README.md` and `.gitignore`, so two `test_docs_wiki` tests fail there for that reason only).

## Verdicts

| # | Item | Verdict |
|---|---|---|
| 1 | Important 2, a service that died | **ADDRESSED** |
| 2 | Important 3, the fork race | **ADDRESSED** |
| 3 | Minor 4, `PermissionError` on `kill()` | **ADDRESSED** |
| 4 | Minor 5, port still open after the stop | **ADDRESSED**, with a wording flaw (New 2) |
| 5 | Minor 6, the log path | **ADDRESSED** |
| 6 | Minor 7, `_log_tail` | **ADDRESSED** |
| 7 | Minors 8 to 12 and 14 | **ADDRESSED**, with two inaccurate sentences (New 1, New 3) |
| 8 | Minor 13, `port_env` and the limit lines | **ADDRESSED** |
| 9 | Minor 15, three test lines | **ADDRESSED** |
| 10 | Important 1, the limitation | **ADDRESSED** |
| 11 | The `integration` mark | **ADDRESSED** |

### 1. Important 2 (ADDRESSED)

`service_runner.py:58-61` sets `was_in_use` once the port answers. `:260` reads `poll()` before the stop. `:258-268` puts a `ServiceDiedError` first in the list. `:244-255` attaches it as a note when the body raised, and raises it when the body was good. `ports.py` makes it a `ServiceError`.

I re-ran the review's three rows plus two more, on the copy (`rr9/items.py died`):

| Row | Result |
|---|---|
| Opens the port, prints `fatal: lost the database`, exits 5 after 0.25 s | `ServiceDiedError`: "exited with code 5 while it was in use", log tail has the fatal line, `next_action` names the log |
| Leader exits 9 during the body, leaving a child that ignores SIGTERM | `ServiceDiedError` with code 9; the child is dead afterwards |
| Another process holds the port, service exits `Address already in use` | `ServiceDiedError` with code 1 and the log line, plus one note from the stop check (see New 2) |
| Same as row 1 but the body raises `ValueError` | The `ValueError` arrives, with the died message as a note |
| Service exits 0 on its own | `ServiceDiedError` code 0 (the `is not None` test is right) |

A start failure is not reported twice (`:260` gated by `was_in_use`; test at `test_service_runner.py:492` asserts no notes). Mutants "no died check" and "not gated" are both caught.

### 2. Important 3 (ADDRESSED)

`child_process.py:150-158`: `force_stop` sends `signal_group(SIGKILL)` again after `process.wait`. The service runner calls it (`service_runner.py:286`) and so does `run_child`'s timeout path.

Real runner, `rr9/fork_race_fixed.py 110`, children live 5 s, anything alive 1 s after the stop escaped:

| Setting | Stops | Live at +50 ms | Alive at +1 s |
|---|---|---|---|
| Leader ignores SIGTERM, forks with no pause | 110 | 0 | **0** |
| Same, forks every 1 ms | 110 | 0 | **0** |
| Control: same no-pause leader, `force_stop` patched back to one kill | 110 | 48 | **48** |
| `run_child` timeout, no-pause forker, fixed | 110 | - | **0** (0 skipped) |
| `run_child` timeout, control with one kill | 110 | - | **62** |

The control reproduces the review's 46 of 110, so the harness can see the defect. The new tests are `test_service_runner.py:761` and `test_child_process.py:258`. The "no second kill" mutant fails the service-runner test.

### 3. Minor 4 (ADDRESSED)

`child_process.py:152` suppresses both. With `os.kill` and `os.killpg` refusing (`rr9/items.py permission`): a good body gives `ServiceStopError` ("did not stop after SIGKILL", `kill -9 -<pid>`), and a failing body keeps its `ValueError` with the stop error as a note. Test at `test_service_runner.py:776`. Mutant caught.

### 4. Minor 5 (ADDRESSED)

`service_runner.py:303-309` (`_port_is_released`) and the check at `:292-301`. A daemon in its own session holding the port, with the leader alive, raised `ServiceStopError` naming the port and `lsof` (`rr9/port_check.py daemon`, 1.5 s with a 0.5 s grace). The daemon case where the leader exits and the old message said "before it opened port" now says "exited with code 0, but port P still accepts connections" (`:184-193`). Both mutants (no port check, no message variant) are caught.

Is the 1 s wait sound? Yes.
- A normal stop pays one refused loopback connection: median 0.20 ms, max 0.32 ms over 20 stops (`port_check.py normal`). Whole normal stops measured 4 to 11 ms.
- The wait is needed. For the forking stand-in whose children inherit the listening socket, the wait before the port closed was median 0.3 ms, p90 and max about 60 ms, in 40 stops. Under 24 CPU burners (load average 24 on 12 cores): median 2.2 ms, p90 55 ms, max 59 ms, 0 spurious `ServiceStopError` in 40 stops. A single check would have failed those stops, as the report says. The 1 s cap is about 17 times the worst case seen.
- It costs 1 s only when something really holds the port (measured 1.03 s).

### 5. Minor 6 (ADDRESSED)

`service_runner.py:127` (`O_NOFOLLOW | O_NONBLOCK`), `:143` (`S_ISREG` on `fstat`), `:149` (`set_blocking(True)`). Run in `rr9/logs.py`, each under a 5 s alarm:

| Log path | Result |
|---|---|
| Symlink to a 0644 file with content | `ServiceStartError` (ELOOP), 0 processes started, target intact, still 0644 |
| Dangling symlink | `ServiceStartError`, target not created |
| FIFO, no reader | `ServiceStartError` ("Device not configured") in 0.00 s, no hang, 0 started |
| FIFO with a reader | `ServiceStartError` "it is not a regular file", 0 started |
| Folder, `/dev/null` | `ServiceStartError`, 0 started |
| Old 0644 file with content | Truncated, now 0600; the child's fd 1 is blocking (`O_NONBLOCK` clear) |
| Missing folder | `ServiceStartError` naming the path |

Mutants "no `O_NOFOLLOW`" and "no regular-file check" are caught. The "no `O_NONBLOCK`" mutant makes the FIFO test hang instead of fail (as the report said it would); I killed it after 3 minutes. That is detectable but is not a clean failure (Nit 4).

### 6. Minor 7 (ADDRESSED)

`service_runner.py:230-241` catches `OSError`. Log deleted, chmod 0, replaced by a folder: each gave the service's own error ("exited with code 4", "could not be read: ...") with the right `next_action`, on the exit-during-start path. The timeout path (log deleted) and the died-in-use path (log deleted) also kept their own errors. Mutant caught.

### 7. Minors 8 to 12 and 14 (ADDRESSED, two sentences are inaccurate)

- **8, first signal inside its `try`:** `service_runner.py:279-286`. A KeyboardInterrupt at the SIGTERM call still ends with the group killed (`test_service_runner.py:798`; mutant caught). The Popen limitation line is added at `limitations.md:15`. Two sentences in it and in the code are not quite true: New 1.
- **9, `next_action` in the note:** `:271-274`. Mutant caught; the failed-stop test asserts `kill -9 -<pid>` in the note. Confirmed live in rows 1c and 3 above.
- **10, `_free_port` comment:** `:89-91` now matches what row 1c did: the probe connected to the other process and the death surfaced when the body ended.
- **11, `::1` message:** `:206-218` and `next_action` name `127.0.0.1` and `::1`. Seen in the timeout runs.
- **12, window figures:** `limitations.md:19`. I re-measured reap to signal with spies (60 runs each, `rr9/windows.py`). Reap to the first signal after it: run_child 35 us median, max 63 (doc: 35, about 120); normal stop 20 us, max 48 (doc: 10); died in use 36 us, max 179 (doc: about 50, about 140); exit during start 534 us, max 695 (doc: about 500, about 900). Same order of magnitude, and the doc says "about". The sentence about what follows the first signal is New 3.
- **14, `describe_os_error` comment:** `child_process.py:129-130` now names the log.

### 8. Minor 13 (ADDRESSED)

`service_runner.py:77-85`, called first in `running` (`:55`). `PATH`, `HOME`, `TMPDIR`, `LANG` and `LC_ALL` each gave `ServiceStartError` with 0 processes started; `SHOP_PORT` ran. Test parametrised over `ALLOWED_VARIABLES` (`:925`); mutant caught. New limit lines exist: `limitations.md:16` (127.0.0.1 only), `:17` (shared log), `:18` (no size cap), plus the Popen line `:15`. The FIFO and `PATH` limits are gone because the code refuses them.

### 9. Minor 15 (ADDRESSED)

Mutants run against the new tests:

| Mutant | Result |
|---|---|
| `STOP_GRACE_S = 50` | `test_the_grace_is_five_seconds` fails (`:816`) |
| `stdin=` line removed | `test_the_service_gets_no_input_from_arde` fails (`:953`); the test swaps fd 0 for a pipe first, so it does not pass by accident |
| ARDE keeps a descriptor on the log | `test_arde_keeps_no_descriptor_open_on_the_log_or_the_probe` fails (`:967`) |

The grace test pins the constant, so a mutant that multiplies the value at the `wait` call would not be caught. The brief's number is what it pins.

### 10. Important 1 (ADDRESSED)

`limitations.md:12` says SIGKILL, SIGTERM or SIGHUP to ARDE leaves a service behind, that SIGKILL stays so, and that SIGTERM and SIGHUP stay so "until the CLI installs its handlers for them in Task 17". The plan has an owner for it: `docs/superpowers/plans/2026-11-01-arde.md:94` ("Task 17, signals", with a real-signal test), added in f145fe7, after the fix commit. The review's real-signal behaviour is unchanged and now documented. The code was not changed, as agreed.

### 11. The `integration` mark (ADDRESSED)

`pytestmark` is gone. `test_service_runner.py:209` and `:221` carry `@pytest.mark.integration`. `pytest --collect-only -m integration` on the file: 2 of 49 collected. `shop_binary` is used only by those two tests. `pyproject.toml:53` and `AGENTS.md:30` ("needs the Go toolchain") are now true of the mark.

## The poll-before-SIGTERM question

On the normal path it cannot signal a stale group. `poll()` returns `None` for a live leader and reaps nothing, so SIGTERM goes to a group whose leader is alive, or a zombie that still reserves the id. `poll()` reaps only a leader that has already died, which is the died-in-use path. There the group id is free if the group is empty, and all three signals (SIGTERM, SIGKILL, SIGKILL) follow the reap. The limitation names this ("longer when the service ended by itself, because the leader is reaped before the first signal"), so its wording matches the code for the first signal. What it leaves out is New 3. A leader that died with children alive keeps the id reserved, so only the empty-group case is exposed, and it still needs a process id wrap inside the window.

## New Breakage in the Fix Diff

None of these is above Minor. Nothing in the fix makes a previously passing case fail, and `mypy src`, `ruff` and the 71 tests are clean.

**New 1 (Minor). The start and stop windows are not fully closed, and the new limitation says they are.**
- `limitations.md:15` says an interrupt "anywhere else in the start" leaves nothing running. Between `Popen` returning and the `try` (the log close, `_Started(...)`, the assignments) there is a window of 56 us median (83 max) against 6.6 ms for `Popen` itself. An interrupt injected right after `Popen` returned left the service running (`rr9/start_window.py`). This is the same class as the review's 39 non-`_execute_child` leaks out of 753.
- The new `poll()` at `service_runner.py:260` sits before the stop's `try` (`:279`). A second interrupt raised inside it left the service running (`rr9/second_interrupt.py`). From entering the stop to SIGTERM is 28 us median, 128 max. So Minor 8's second half moved the unprotected code rather than removing it. It needs two interrupts tens of microseconds apart.
- Fix: say "inside or just after `Popen`" in the limitation. Optionally move the `poll()` into `_stop`'s `try`, though then the died check has no home before the SIGTERM.

**New 2 (Minor). "Something it started outside its process group" is sometimes false.** `service_runner.py:189` and `:295`.
- In-group variant: the leader exits 0 while a child in its own group still holds the port. The start error says it was "started outside its process group". The child is in the group and is killed at the stop (`rr9/port_check.py ingroup`: child dead afterwards).
- Squatter variant (row 1c): an unrelated process holds the port, the service could not bind and died. The stop appends a note blaming "something it started". The `lsof` advice still points at the right process, and a test asserts the note (`test_service_runner.py:674`).
- Fix: say "something is holding port P" and leave out who started it.

**New 3 (Nit). The window figures describe the first signal after the reap, and the fix added later ones.** The second SIGKILL is the new late signal. Measured (`rr9/windows.py`): after a normal stop 2 signals follow the reap, the last at 48 us median (max 110), where the doc says 10. After a died-in-use there are 3, the last at 91 us (max 378), where the doc says about 50 (up to about 140). Still needs a process id wrap. The review accepted this cost ("adds the same short reuse window").

**Nit 4. Removing `O_NONBLOCK` makes the FIFO test hang, not fail.** Known to the implementer, who did not run that mutant. A hang is still caught by CI's timeout.

**Not verified:** Linux; the full `make check` and `make integration` (not re-run); the implementer's own load runs. The fork-race fix targets macOS behaviour and the review saw no such miss on Linux, so these tests pass there trivially.

## Process notes

- Cleanup was scoped to my own folders and to the 24 `yes` burners I started (all gone; `pgrep -x yes` gave 0). No process of mine is left.
- Two experiments of mine failed on first run for harness reasons (`rr9/items.py` argument path, a `waitpid` spy that `Popen.poll` bypasses); both were rerun and the numbers above come from the corrected runs. The "unix socket at the log path" row was dropped because the path was too long for `AF_UNIX`.

All items addressed. Open: none of the eleven; three small corrections remain (New 1, New 2, New 3), none blocking.

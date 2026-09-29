---
phase: "25"
name: "Multi-frame Mappings & systemd User Scheduling"
created: 2026-09-29
status: passed
---

# Phase 25 Verification — Multi-frame & Scheduling

**Date:** 2026-09-29
**Status:** PASSED (code-complete) — suite 486 passed; criterion 1
evidenced by the container journey + offline runner tests; criterion 2's
unit round-trip evidenced offline with content assertions, the
systemd-in-container run deliberately deferred (see Scope honesty).

## Criteria → Evidence

| # | Criterion (ROADMAP §25) | Evidence | Result |
|---|--------------------------|----------|--------|
| 1 | Two pairs (one album → two frames) sync in one `--all` run, per-pair manifest/cache shards, shared budget capping the total, per-pair reports exact (container + fake-API proof) | `scripts/test-multipair-journey-container.sh` (pristine ubuntu:26.04, wheel install, two pairs configured file-only, `--all` run, sharding contract probed, single budget file, report shape) + offline `test_all_runs_every_pair_with_one_shared_budget` (the budget INSTANCE identity asserted across pairs) + `test_all_continues_after_first_pair_failure_and_exits_1` | ✅ PASSED |
| 2 | `schedule add/list/remove` round-trips units in `~/.config/systemd/user/` (unit-content assertions); scheduled google-sync prompt-free, logs to `~/.local/state/pushframe/<job>.log` | `tests/test_schedule.py`: unit-content assertions (Type=oneshot, Restart=no, RandomizedDelaySec, Persistent, append: log naming `<job>.log`), add→exists / remove→gone round-trips, list output parse; prompt-free inherited from phase 24 (non-TTY → SessionExpiredError, never a hang) | ✅ PASSED (offline; container-with-systemd run deferred — see honesty) |
| 3 | SAFE-02 semantics in scheduled runs: skip-and-log, per-job logs, linger documented | `test_scheduled_threshold_breach_skips_and_logs` (exit 0, SKIPPED + reason, nothing applied); log path asserted in the service unit; linger documented in docs/CLI.md with the tool never executing it | ✅ PASSED |

## Requirement traceability (MTF-01..03, TMR-01..03)

- **MTF-01** pairs CRUD + `--pair/--all` (tests/test_pairs.py 7 tests).
- **MTF-02** per-pair state shards (paths derived from the pair NAME;
  fixture monkeypatches `pair_state_paths` after the drift lesson).
- **MTF-03** one budget instance shared (identity asserted); per-pair
  reports exact; failing pair never blocks.
- **TMR-01** add + list (units named `pushframe-<job>.*`).
- **TMR-02** remove stops + deletes both units + daemon-reload; idempotent.
- **TMR-03** non-interactive ExecStart; SAFE-02 skip-and-log; per-job log;
  linger documented.

## Scope honesty

- **Real-systemd container run deferred**: unit content is fully asserted
  offline and systemctl is a monkeypatchable seam; a genuine `systemctl
  --user` inside docker needs a privileged systemd-enabled image — worth
  doing, deliberately not now.
- **Real-tick validation pending venus**: the timers are built to survive
  the trip (first-occurrence stop + Restart=no + token sessions), but the
  first REAL scheduled run on venus should be watched (job log) once the
  account's anti-abuse state is calm.
- The `--every` mapping supports Nmin (60-divisors), Nh, Nd; odd intervals
  fail named pointing at `--at "OnCalendar…"`.

**Commits:** `4271fdd` (pairs + --all + journey) · `27e79d8` (schedule +
sweep + docs) on `gsd/phase-25-multiframe-scheduling`

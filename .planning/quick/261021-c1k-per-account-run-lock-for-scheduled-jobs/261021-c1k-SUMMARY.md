---
phase: quick-261021-c1k
plan: 01
subsystem: cli / concurrency
tags: [flock, run-lock, scheduling, write-budget, anti-abuse]
requires: [WriteBudget persistence, session.account_email, schedule units]
provides: [pushframe/runlock.py, ACCOUNT_LOCK_MAX_WAIT setting, _under_account_lock dispatch helper]
affects: [pushframe/cli.py, pushframe/utils/settings.py, docs/CLI.md, docs/ERRORS.md, CHANGELOG.md]
tech-stack:
  added: []
  patterns: [kernel flock with polled wait and injectable clock, call-time setting]
key-files:
  created: [pushframe/runlock.py, tests/test_runlock.py, tests/test_runlock_cli.py]
  modified: [pushframe/cli.py, pushframe/utils/settings.py, tests/conftest.py, tests/test_report.py, docs/CLI.md, docs/ERRORS.md, CHANGELOG.md]
decisions:
  - Lock taken in _main dispatch (not inside run_sync/run_google_sync): one choke point, no self-deadlock, google-sync --all takes it once
  - Polled flock(LOCK_NB) every 5 s instead of a bare blocking flock, so the 12 h cap and the 5-minute holder log work without threads/signals
  - account_key stays byte-identical sha1(email)[:12] (pinned by a test), now single-sourced for budget file and lock
  - Read-only paths take no lock at all (no LOCK_SH): a dry-run must not sit behind an hours-long nightly apply
status: complete
commits: 5
plan_head_before: 90bfb155bd4335f2bcf6cd8af896e29a35cbf7da
plan_head_after: 01849f8d8d9032c689941570246315eec9df6ff4
actuals:
  tokens: 60000
  tasks: 3
  commits: 5
---

# Phase quick-261021-c1k Plan 01: Per-account run lock for scheduled jobs Summary

Every write run against one Aura account (sync/push/google-sync `--apply`, `reconcile --remove`) now holds a per-account kernel `flock` for the whole run, so two scheduled jobs in the same timer window run one after the other instead of doubling the write rate or racing the persisted WriteBudget file.

## What was built

- `pushframe/runlock.py` (stdlib only): `account_key`, `lock_dir`/`lock_path_for`, `AccountLockError`/`AccountLockTimeout`, `AccountLock`, `acquire_account_lock`, `account_run_lock`. Lock file `~/.local/state/pushframe/locks/account-<key>.lock` (dir 0700, file 0600, `O_NOFOLLOW|O_CLOEXEC`, never `O_TRUNC`, never unlinked). Holder info is pid/job/verb/start time only; waiter display is sanitized. Re-entrant within a process (no self-deadlock).
- Waiters print `waiting for account lock held by job X (pid N, verb) since HH:MM — …` at once and every 5 minutes, then `account lock acquired after …`. Past `PUSHFRAME_ACCOUNT_LOCK_MAX_WAIT` (default 43200 s, call-time setting, `config set ACCOUNT_LOCK_MAX_WAIT`) the run exits 1 with `<verb> failed: account lock still held by …` plus a remedy and changes nothing.
- `cli._under_account_lock` wraps sync, push, both google-sync branches (the `--report` call sits inside the Tee so waiting lines reach the emailed report) and reconcile `--remove`. Header/footer of scheduled runs stay outside, so elapsed honestly includes the wait. Junk cap value falls back to 12 h with a note; no identity skips the lock with a stderr note.
- `_build_write_budget` now uses `runlock.account_key` (digest unchanged, pinned by test for `a@b.c` and the live account); the unused `hashlib` import was dropped from cli.py.
- Docs: ERRORS.md (3 rows), CLI.md (scheduling bullet, env table, config key table), `config set --help`, CHANGELOG `[Unreleased]` with the run-lock entry plus the two previously unlogged fixes (566a28f per-item content failures / GIF as JPEG; 90bfb15 credential refresh / `AwsCredentialsError`).

## Verification

- `uv run --extra dev pytest -q -m "not live"`: 932 passed, 8 deselected, zero warnings.
- Real two-process test (subprocess): cross-process exclusion, holder pid+job visible to the waiter, per-account scoping, release on SIGKILL.
- Dispatch matrix: all 6 write shapes run with the lock held; 8 read-only/dry-run shapes never call acquire; exit codes 0/1/2 propagate unchanged; contended, junk-cap and no-identity paths covered.
- `pushframe/sync.py`, `pushframe/gsync.py`, `pushframe/aws/` untouched (empty diff vs 90bfb15). No version bump, tag or publish.

## Deviations from Plan

### Process deviation

**1. [Rule 3 - Blocking] Protected-branch guard**
- **Found during:** Task 1 (RED commit)
- **Issue:** The working tree was on `master`, which `git.base-branch --is-protected` reports as protected. The RED commit `7ce6ab5` had already landed on master before the guard ran.
- **Fix:** Did not rewrite master. Created branch `quick/261021-c1k` from that commit and made every later commit there (guard now reports `false`). The orchestrator must fast-forward master (or merge the branch) and make its docs commit accordingly.
- **Commits:** 7ce6ab5 (also on master), 58b35a1, 517530a, 8c46975, 01849f8 (branch only).

### Auto-fixed Issues

None otherwise. One test assertion of mine was wrong (`';'` also occurs in the fixed message template) and was corrected in the same task before commit.

## Known Stubs

None.

## Threat Flags

None. The only new surface is the local 0600 lock file in the user's private state dir, covered by T-c1k-01..05.

## Notes

- STATE.md/ROADMAP.md were not touched (quick task; the orchestrator owns the docs commit).
- The plan's 871 baseline was stale; the suite stood at 839 per commit 566a28f and is 932 now.

## Self-Check: PASSED

- Files present: pushframe/runlock.py, tests/test_runlock.py, tests/test_runlock_cli.py.
- Commits present on `quick/261021-c1k`: 7ce6ab5, 58b35a1, 517530a, 8c46975, 01849f8.

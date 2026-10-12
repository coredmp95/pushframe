---
phase: quick-261012-qwv
plan: 01
subsystem: gsync, report, private-state
tags: [security, tls, umask, expanduser, mirror-report]
requirements: [B1, B2, S4, S5]
key-files:
  modified:
    - pushframe/gsync.py
    - pushframe/sync.py
    - pushframe/utils/io.py
    - pushframe/google/cache.py
    - pushframe/google/bootstrap.py
    - pushframe/google/manifest.py
    - pushframe/ratelimit.py
    - pushframe/config_store.py
    - pushframe/cli.py
    - pushframe/report.py
    - tests/conftest.py
    - tests/test_gsync_engine.py
    - tests/test_report.py
  created:
    - tests/test_private_paths.py
decisions:
  - "Visible hashless frame assets are reported (SyncPlan.unmanaged_visible), never hidden"
  - "Tighten-only retro-chmod limited to pushframe's own default dirs (config dir, default chrome-profile)"
  - "STARTTLS failure on non-465 ports raises before login/send; deliver() still returns 'email send failed: ...'"
metrics:
  tasks: 3
  commits: 6
  tests_before: 639
  tests_after: 662
actuals:
  tokens: 40000
  tasks: 3
  commits: 6
plan_head_before: 585681fba3ec4ef7f29c44a0bcd10099145268e8
plan_head_after: 3de3782cf7dc3e0373df32d3b641821e8084531b
status: complete
---

# Quick 261012-qwv: Review quick fixes (B1, B2, S4, S5) Summary

Four findings from the 2026-10-12 review landed test-first (red commit, then green commit per task); offline suite 662 passed (639 + 23 new), DeprecationWarnings still errors.

## What changed

- **B1** `default_cache_dir` now expands `~` at call time (absolute, HOME-rooted). A package-wide source-scan test fails on any new unexpanded `Path("~...")` literal (UPPER_CASE constants whose consumers expand are the only allowed form).
- **B2** `SyncPlan.unmanaged_visible` (defaulted list, last field) is populated by `compute_plan` (still pure; all existing counters unchanged). `format_plan_report` prints the "N visible photo(s) ... NOT managed by the mirror" line (ids + taken date in `detail_ids` mode); new pure `format_noop_message(plan)` replaces the inline no-op f-string (byte-identical when N==0, honest and still starting with "Nothing to do" when N>0). Nothing is hidden automatically.
- **S5** `ensure_private_dir` / `write_private_bytes` in `utils/io.py` (0700 on every NEWLY created component, 0600 files), wired into cache staging, chrome-profile (default tightened, env override never chmod-ed), manifest, write budget, and config dir (default `~/.config/pushframe` tightened, `PUSHFRAME_CONFIG_FILE` dirs never retro-chmod-ed). `cli.main` calls `_tighten_umask()` (tighten-only, ORs in 0o077). `tests/conftest.py` has an autouse fixture restoring the umask after every test.
- **S4** `report.send` builds `ssl.create_default_context()` for both transports (cert + hostname verified), passes it to `SMTP_SSL(context=...)` on 465 and `starttls(context=...)` elsewhere, and raises an `SMTPException` (mentions STARTTLS and cleartext) on STARTTLS failure before any login or message. `deliver()` untouched and still turns it into `email send failed: ...`.

## Deviations from Plan

None - plan executed as written. Notes:
- Commits were made directly on `master` (protected-branch heuristic) per the orchestrator's explicit instruction for sequential main-tree execution; the project's own history commits on master.
- The em dash in the new gsync strings is written as `—` escapes in source (runtime-identical to the existing literal em dash).

## Notes for the operator

- The stray literal directory created on venus by B1 (`/home/fabrice/~/.config/pushframe/google-cache`) is an operator cleanup on the prod host; this plan did not touch it.
- Existing loose subdirectories under a now-0700 config dir are protected by the parent but were not individually chmod-ed.
- No version bump, no release, no push; ROADMAP.md and STATE.md untouched.

## Known Stubs

None.

## Threat Flags

None.

## Self-Check: PASSED

All 14 listed files exist; commits c757029, b94f967, 3e111d5, cc044e6, 24a8d2d, 3de3782 are ancestors of HEAD; full offline suite green (662 passed).

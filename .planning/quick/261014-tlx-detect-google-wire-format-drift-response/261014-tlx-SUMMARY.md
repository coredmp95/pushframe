---
phase: quick-261014-tlx
plan: 01
subsystem: google-sync
tags: [google, wire-format, observability, report]
requires: []
provides:
  - pushframe/google/wire.py (fingerprint, WireWatch state, diagnostics, streak)
affects: [gsync, report, cli google-album, docs/ERRORS.md, docs/CLI.md]
key-files:
  created: [pushframe/google/wire.py, tests/test_google_wire.py]
  modified:
    - pushframe/google/parsers.py
    - pushframe/google/enumerate.py
    - pushframe/gsync.py
    - pushframe/report.py
    - pushframe/cli.py
    - tests/conftest.py
    - tests/test_google_enumerate.py
    - tests/test_gsync_execute.py
    - tests/test_gsync_all.py
    - tests/test_gsync_engine.py
    - tests/test_cli_google_album.py
    - tests/test_report.py
    - docs/ERRORS.md
    - docs/CLI.md
decisions:
  - Fingerprint = value kinds + power-of-two buckets + at most 4 cursor chars; cursor recorded only for a FULL page 1
  - State per target (pair dir, or sha1(album target)[:16] file), tolerant closed-schema JSON, 0600/0700, atomic
  - Success = listing passed the SAFE-01 listing gates; resets streak and stores fingerprint
  - First-failure note avoids the literal escalation phrase so report markers and tests stay unambiguous
status: complete
metrics:
  tasks: 3
  tests_before: 734
  tests_after: 811
actuals:
  tokens: 23900
  tasks: 3
  commits: 7
plan_head_before: 2dea192d0c2d1e7a0841654503f6c34e1cdc308e
plan_head_after: 7b234a0339616da821f96d58f47d9d7ac6e3a662
commits: 7
completed: 2026-10-14
---

# Phase quick-261014-tlx Plan 01: Detect Google wire-format drift Summary

A privacy-safe per-run Google wire fingerprint with a per-target last-good store, a one-line WARNING when it changes, a diagnostics block after every Google listing failure, and a consecutive-failure streak that escalates to "probable Google protocol change", all surfaced through the report subject.

## What was built

- **TLX-1 (Task 1, tracer):** `parsers.py` gains `_len_bucket`, `wire_shape`, `media_item_shape`, `cursor_signature` and defaulted `SnackcPage.inner_shape/item_shape/cursor_shape`, `AlbumSummary.row_shape`. `enumerate.py` sets `AlbumListing.fingerprint` from the FINAL page-1 answer (after any null or tokenless retry), cursor only when page 1 was full. New `wire.py` stores/compares it per target; `run_google_sync` prints at most one `WARNING: Google response format changed: ...` after the trust gate. `report.py` treats it as a problem marker and gives `ATTENTION - Google response format changed (check for a pushframe update)` at every level on exit 0. Autouse conftest fixture redirects the state root per test.
- **TLX-2 (Task 2):** `EnumerateError.kind/pages/fingerprint`, `PageTrace` per snAcKc POST answer, `SafeSyncError.kind`, pure `wire.format_diagnostics` (all lines through `redact_tokens`). Printed after the unchanged `google-sync failed:` / `google-album failed:` line (google-album without last-good lines). Frame/manifest errors print none.
- **TLX-3 (Task 3):** `wire.failure_class` and `WireWatch.on_failure` persist `{failure_class, consecutive_failures}`; first failure prints a "may be a transient Google answer" note, 2nd+ of the same class prints `ATTENTION: probable Google protocol change - pushframe likely needs an update ...` plus the fingerprint diff. Report subject rule: escalation outranks everything (`FAILED`, or `ATTENTION` on rc 0).
- **TLX-4:** state problems yield one `note:` line and never raise; `check_listing_trust`, `run_google_sync_plan`, `build_demand`, `compute_plan` stay pure (pinned by test); no new dependencies; version, CHANGELOG, uv.lock, pyproject untouched. Docs updated in `docs/ERRORS.md` and `docs/CLI.md`.

## Deviations from Plan

**1. [Rule 1 - Test expectation] Existing subject text for `Applied:` lines**
- The plan's report test expected `OK - Applied: 111 ...`, but today's `build_subject` strips the `Applied:` prefix (`OK - 111 uploaded, ...`). The test pins the real current subject ("exact subjects they get today").

**2. [Rule 1 - Spec conflict] First-failure note wording**
- The plan's first-failure note ends "... is flagged as a probable Google protocol change", but the same plan requires run 1 not to contain "probable Google protocol change" and the report marker would escalate every first failure. The note now says "is escalated as an ATTENTION". Escalation line text is as specified.

**3. [Rule 2 - Tooling] sha1 file-name key**
- sha1 kept as the plan-mandated stable name key, with `usedforsecurity=False` and a comment (semgrep flagged it; it is not a security primitive).

Otherwise the plan was executed as written (RED test commit before each GREEN commit; the autouse conftest fixture ships with the Task 1 GREEN commit as the plan directs).

## Verification

- `uv run --extra dev pytest -q -m "not live"`: 811 passed, 8 deselected (734 before).
- `git diff --quiet 2dea192 -- pyproject.toml uv.lock CHANGELOG.md pushframe/__init__.py`: unchanged.
- Greps: `probable Google protocol change` in report.py/ERRORS.md/CLI.md; `WARNING: Google response format changed` in ERRORS.md/CLI.md; `google-wire` in ERRORS.md.

## Commits

- b45ebd8 test: red tests (Task 1)
- 1569c6b feat: fingerprint + warning (Task 1)
- ee9cec4 test: red tests (Task 2)
- e62599e feat: diagnostics (Task 2)
- 94cb1d4 test: red tests (Task 3)
- 0c207f0 feat: streak + escalation (Task 3)
- 7b234a0 docs: ERRORS.md / CLI.md

## Known Stubs

None.

## Threat Flags

None beyond the plan's threat model (new surface is the local state file, mitigated: closed schema, 0600/0700, hashed album key, safe pair names).

## Self-Check: PASSED

- wire.py, test_google_wire.py FOUND; all 7 commits are ancestors of HEAD.

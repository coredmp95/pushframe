---
phase: quick-261014-swa
plan: 01
subsystem: google
tags: [snAcKc, continuation-cursor, parser, redaction, urgent-fix]
requires: []
provides:
  - structural inner[2] snAcKc cursor (AKFE live 2026-10-14)
  - item-safe legacy AH_ fallback
  - AH_/AKFE cursor redaction
affects: [pushframe/google/parsers.py, pushframe/google/enumerate.py, pushframe/google/redaction.py]
key-files:
  modified:
    - pushframe/google/parsers.py
    - pushframe/google/enumerate.py
    - pushframe/google/redaction.py
    - pushframe/google/__init__.py
    - tests/test_google_parsers.py
    - tests/test_google_enumerate.py
  created:
    - tests/fixtures/google_rpc_snackc_page_akfe.json
decisions:
  - cursor read structurally from inner[2]; AH_ regex only a restricted legacy fallback
  - ProbeParseError from a snAcKc page becomes a chained EnumerateError
status: complete
actuals:
  tokens: 28000
  tasks: 2
  commits: 4
plan_head_before: 5b1a2b7
plan_head_after: 2206207
---

# Phase quick-261014-swa Plan 01: snAcKc structural cursor Summary

The snAcKc continuation cursor is now read structurally from `inner[2]`, which fixes the nightly `google-sync` failure caused by Google's switch from `AH_...` to 226-char `AKFE...` cursors.

## Structural rule chosen

- The structural slot applies when `inner` is a list with len >= 3, `inner[1]` is a list or None, and `inner[2]` is a str or None.
- On that path an empty string or None means end of album. A non-empty value must fullmatch `[A-Za-z0-9_-]{16,4096}`. Anything else raises `ProbeParseError` naming `inner[2]` and the length, never the value.
- The AH_ regex is not consulted on the structural path, so an AH_-looking string inside item data can never become the cursor.
- With no structural slot the legacy fallback `_legacy_cursor` walks the parsed inner payload. It skips media-item nodes (shared `_is_media_item` predicate), accepts only whole-string AH_ values, and returns the last one found.
- `_snackc_page` in `enumerate.py` now converts any `ProbeParseError` (envelope or payload) into a chained `EnumerateError`. `google-sync` and `google-album` therefore print their existing `... failed:` line and exit 1 instead of a traceback.
- The retry budgets, the tokenless full-page gate, the short-page terminal rule and the call-time `PAGE_SIZE` read are untouched.

## Commits

- 75250da test: red tests for the structural AKFE cursor
- 7917e4b fix: read the snAcKc continuation cursor from inner[2]
- ef0d49c test: red tests for the item-safe legacy fallback and cursor redaction
- 2206207 fix: legacy AH_ fallback never reads item data; redact AH_/AKFE cursors

## Tests

Full offline suite: 734 passed, 8 deselected (713 existing plus 21 new), with DeprecationWarnings still treated as errors.

The AKFE replay (300+300+157) returns 757 unique items in 3 POSTs, with and without `expected_count`. The tokenless transient, short-page-with-cursor and null-payload retry gates all behave as before on the AKFE wire. The malformed-cursor case surfaces as a named `EnumerateError` that does not echo the cursor.

## Consumer audit (SWA-2)

- No module inside `pushframe/` other than `parsers.py` matches cursors (grep-verified).
- Left unchanged on purpose: `probes/browser_bootstrap.py` (disposable instrument, historical wire), `tests/fixtures/google_share_page_sample.html` (`parse_af_initdata` never reads cursors), `tests/fixtures/google_rpc_snackc_page.json` (kept as the legacy-format fixture), and `docs/` (no AH_ mention).
- `probes/common.py` re-exports `redact_tokens` and inherits the new redaction.
- `redact_tokens` now also collapses AH_ and AKFE cursors (prefix + 40 or more chars) to `<prefix>…<last4>`.

## Deviations from Plan

None. The plan was executed as written. Version, CHANGELOG and uv.lock are untouched.

## Known Stubs

None.

## Self-Check: PASSED

All four commits are present, the fixture exists, and the suite is green.

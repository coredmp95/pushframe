---
phase: "24"
name: "Token-First Sessions & Preflight Sweep"
created: 2026-09-29
status: passed
---

# Phase 24 Verification — Token-First Sessions & Preflight Sweep

**Date:** 2026-09-29
**Status:** PASSED — suite 459 passed; the three ROADMAP §24 success
criteria evidenced by file-content tests, mocked-401 flows, and the
parametrized sweep.

## Criteria → Evidence

| # | Criterion (ROADMAP §24) | Evidence | Result |
|---|--------------------------|----------|--------|
| 1 | `pushframe config` + login → `~/.config/pushframe/` contains a token, no password, mode 0600 (file-content test) | `tests/test_session.py::test_tty_prompt_logs_in_and_persists_token_never_password` — parses the stored JSON, asserts email/token/user_id present, the password STRING absent from the raw file, and no `password` key at all; 0600 covered by the phase-23 wizard test (same writer) | ✅ PASSED |
| 2 | Token-expiry simulation (mocked 401): transparent re-login ONCE and retry; a second failure surfaces the login error (no loops) | Establishment: `test_expired_token_tty_prompts_once_and_persists_new_token` (resume 401 → exactly ONE prompt → new token persisted → session continues) and `test_expired_token_non_tty_named_error_no_prompt` (SessionExpiredError, prompt asserted impossible). Mid-run: `execute_plan`'s relogin seam is token-aware — env login / TTY one-prompt / else `AuthenticationError` named (chunk fails, breaker guards; the one-retry-then-surface shape is the pre-existing REL-02 contract, still tested in `test_execute_plan.py`) | ✅ PASSED |
| 3 | The traceback-free sweep: every documented foreseeable failure mode maps to a named error with a remedy (parametrized test) | `tests/test_preflight_sweep.py` — parametrized over the 24-CONTEXT inventory (vault missing, dir nonexistent, no-credentials ×3 commands); per case: rc≠0 + named marker + remedy substring + zero `Traceback`. Modes 1–4 (playwright/Chrome/headless/near-miss env) are the phase-23.5 google-link tests, referenced in the same inventory | ✅ PASSED |

## Requirement traceability (SEC-01..03, PRF-01..02)

- **SEC-01**: password never persisted — wizard test (phase 23) + the new
  prompt-persist test both assert raw-file content; `_store_token` writes
  only `{email, auth_token, user_id}`.
- **SEC-02**: all five commands run from the stored token; env stays the
  override (`test_env_password_still_wins_the_override`); discouraged-for-
  humans wording in docs/CLI.md.
- **SEC-03**: `logout` tests — token-only removal, idempotent, no token in
  output; redaction regression suite unchanged and green.
- **PRF-01**: vault preflight (google-sync, remedy includes the headless
  ssh -X recipe) + source-dir preflight (D-03 severity split).
- **PRF-02**: the sweep itself + the preflight-ORDER fix it caught (machine
  checks precede every network call).

## Scope honesty

- The token-expiry REFRESH is a re-login (credentials in memory), not a
  server-side refresh token — Pushd offers none; that is the phase-22
  decision, unchanged.
- `schedule`'s systemd preflight is a phase-25 item; the inventory already
  lists it so the sweep grows by one case then.
- `--password` CLI flag (vs env) was deferred: the env override satisfies
  SEC-02's "documented, discouraged" bar; the flag lands with phase 25's
  scheduled-jobs ergonomics if wanted.

**Commits:** `9a84480` (session + call sites + logout + preflights + sweep)
on `gsd/phase-24-token-first-preflight`

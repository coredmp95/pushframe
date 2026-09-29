# Plan 24-01 Execution Summary — Token-First Sessions & Preflight Sweep

**Phase:** 24-token-first-sessions-preflight-sweep-sec-01-03-prf-01-02
**Date:** 2026-09-29
**Status:** Complete — suite **459 passed** (was 443; +16: session 7, logout 4, sweep 5)
**Branch:** `gsd/phase-24-token-first-preflight`

## What Shipped

- **`pushframe/session.py`** — `establish_session`, the ONE path: env
  password override → stored token resume (no login call) → ONE TTY prompt
  (token persisted, SEC-01 file-content tested) → `NoCredentialsError`
  named. Expiry (D-01): a 401 on resume = TTY one-prompt-and-continue /
  non-TTY `SessionExpiredError`; `_is_auth_failure` keeps corrupt-config
  errors from masquerading as expiry.
- **Call-site conversion** — `run_inspect`, `run_reconcile`, `run_sync`
  (sync+push), `gsync` all funnel through establish_session. DI contract
  now enforced at every site: an **injected Aura is never re-authenticated**
  (tests, doctor, pipelines manage their own auth).
- **Token-aware relogin** — `execute_plan`'s 401-retry seam no longer
  assumes `aura.login`: env password → real login; TTY → the one prompt
  (budget-charged as before); non-TTY → `AuthenticationError` named, the
  chunk fails, the breaker still guards. No loops, no hangs.
- **`pushframe logout`** (D-02) — token+user_id removed, email/settings/
  default_frame kept, 0600 preserved, idempotent, token never printed.
  `config_store.update` learns None-means-remove.
- **`pushframe/preflight.py`** — `require_google_vault` (google-sync, with
  the ssh -X headless remedy) and `require_source_dir` (D-03: nonexistent =
  hard named error BEFORE any network; empty = the friendly nothing-to-do).
  Wired: gsync vault check precedes session; run_sync dir check precedes
  login entirely.
- **The PRF-02 sweep** — `tests/test_preflight_sweep.py` parametrized over
  the 24-CONTEXT failure-mode inventory (5 cases live; the remaining 4 are
  already covered by the phase-23.5 google-link tests, referenced in the
  inventory). Contract per case: rc≠0, named marker, remedy substring,
  zero "Traceback".

## Roadmap §24 criteria check

| # | Criterion | Evidence |
|---|---|---|
| 1 | config+login → token, no password, 0600 (file-content test) | `test_tty_prompt_logs_in_and_persists_token_never_password` (asserts the password string is absent from the file) |
| 2 | mocked 401 → transparent re-login once, then surface | `test_expired_token_tty_prompts_once_and_persists_new_token` (exactly one prompt, new token persisted) + `test_expired_token_non_tty_named_error_no_prompt` + the relogin seam's named AuthenticationError |
| 3 | traceback-free sweep passes, parametrized | `test_preflight_sweep.py` — 5 modes, zero tracebacks |

## Broken-and-fixed along the way

- The sweep caught the preflight ORDER bug: run_sync checked the source
  dir AFTER login — machine prerequisites now precede any network call
  (that ordering is the PRF-01 point).
- Three tests encoded the old "call sites re-login an injected aura"
  contract; converted to the DI contract (no-credentials named path), and
  the 475 test now exercises the establish_session env path where that
  behavior actually lives.

**Commits:** this branch (session + call sites + logout + preflights + sweep)

---
slug: gsync-apply-budget-none-crash
status: resolved
created: 2026-09-30T23:05:00+02:00
updated: 2026-09-30T23:50:00+02:00
trigger: pushframe google-sync --apply crashes with AttributeError 'NoneType' object has no attribute 'encode' in _build_write_budget after the y confirmation — operator framing: "la gestion de la dualité config env et fichier de conf ne fonctionne pas" (the env-vs-config-file duality is broken)
---

# Debug: google-sync --apply crashes building the write budget (email None)

## Symptoms

- expected: after the `y` confirmation, the plan applies at write-budget pace (or runs budgetless when the account is not identified), then prints the apply report — never a traceback (PRF-02).
- actual: raw AttributeError traceback from cli._build_write_budget via gsync.py line 491.
- error: `AttributeError: 'NoneType' object has no attribute 'encode'` at `budget-{sha1(email.encode())…}` — the email passed is None.

## Environment

- venus (headless server, ssh), pushframe 5.1.5 (installed this evening)
- config.json: stored token session (email coredmp95@gmail.com) — NO PUSHFRAME_EMAIL/AURA_EMAIL/PASSWORD env in the operator shell
- command: `pushframe google-sync Cadre --frame "Cadre de Fabrice"` then `y` at the apply gate
- the SAME dry-run command earlier tonight printed the plan fine; the crash is strictly past the y gate

## Evidence

- 23:10: crash site + mechanism located (this context, no subagents available in this runtime): gsync.py:497 builds the budget from `os.getenv('PUSHFRAME_EMAIL') or os.getenv('AURA_EMAIL')` ONLY — on venus neither env var is set (token-session posture) → email=None → `email.encode()` inside the sha1 → AttributeError. The auth layer (session.establish_session) DOES resolve the stored config email — two different identity resolutions = the operator's "duality" is real.
- scope audit (this context): same env-only identity read at cli.py:1129 (reconcile — crashes on --remove with no env) and cli.py:1392 (sync/push --apply — crashes with no env). Reconcile has NO --ignore-budget, so it is unreachable on venus until fixed.
- why tests never caught it: every gsync apply test injects s3_client=…, and the build condition is `budget is None AND s3_client is None` — injection skips the crash line entirely. Same for sync/push tests (exec_kwargs path).
- why it never crashed before tonight: phase-25 nightly runs were `--pair` DRY-RUN (plan print only — budget built after the gate, never reached). Tonight was the first interactive `--apply` on venus.
- 23:2x: operator removed ALL env vars from the venus shell (user message) — the crash posture is exactly "stored token session, no env identity". The auth layer resolves that posture (token resumed fine — the plan printed); the budget layer does not (None.encode()). Duality confirmed on both sides.

## Hypotheses

- H1 (CONFIRMED): the write-budget identity is resolved env-ONLY while auth resolves env-then-config — two different resolvers for one identity. Mechanism proven by code read + crash line + operator env removal reproducing.

## Root Cause

`gsync.py:497` / `cli.py:1129` / `cli.py:1392` build the budget email from `os.getenv('PUSHFRAME_EMAIL') or os.getenv('AURA_EMAIL')` ONLY. On a token-session host (venus's intended posture) that is None and `_build_write_budget` calls `email.encode()` → AttributeError. The config store's email (the one auth uses) is never consulted. Fix: ONE resolver — `session.account_email()` (env override first, then stored session) — wired into all three call sites; `_build_write_budget(None)` now returns None (skip pacing) instead of crashing.

## Resolution

root_cause: identity duality — auth resolves env-then-config-store, the write-budget layer resolved env-ONLY (gsync.py:497, cli.py:1129 reconcile, cli.py:1392 sync/push). Token-session host + no env ⇒ email=None ⇒ email.encode() AttributeError AFTER the operator's y confirmation.
fix: ONE resolver `session.account_email()` (env override, then stored session) wired into all three budget call sites; `_build_write_budget(None)` skips pacing with a named stderr line instead of crashing (a budget is per-account state — with no account named there is nothing to key).
verification: 6 regression tests (tests/test_budget_identity.py: env-over-store, stored fallback, None never crashes + names the skip, state-file keyed to sha1(email)[:12], direct probe of the gsync crash expression) + sweep case mode-17 reconcile-no-identity (named, never traceback); suite 524 passed, seeded and fixed order. Deployed to venus from the local wheel; existing budget file budget-eeda3bb09fc5.json = sha1('coredmp95@gmail.com')[:12] — continuity preserved.
files_changed: pushframe/session.py, pushframe/cli.py, pushframe/gsync.py, tests/test_budget_identity.py (new), tests/test_preflight_sweep.py, CHANGELOG.md

## Current Focus

next_action: none — resolved

Note on process: the orchestrator already located the crash site + mechanism before creating this file (gsync.py:497 → cli._build_write_budget email=None; same latent shape at cli.py:1129 reconcile and cli.py:1392 sync/push when the account is unnamed). No subagent dispatch is available in this runtime (established 2026-09-30, session status-crash-401-trip) — the scientific loop runs in this context.

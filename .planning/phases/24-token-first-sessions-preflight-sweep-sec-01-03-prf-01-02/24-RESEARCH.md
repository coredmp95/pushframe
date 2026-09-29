# Phase 24 — Research

**Researched:** 2026-09-29 (code reads + venus regressions 23.5 as evidence)

## 1. The unified session establishment (SEC-01/02)

Target: one helper every command funnels through, replacing the four copies
of `aura = aura or Aura(); aura.login()`.

```
establish_session(aura=None, *, tty_ok: bool, password_override=None) -> Aura
  1. env password present (PUSHFRAME_/AURA_PASSWORD) → aura.login()        (override, discouraged)
  2. config has auth_token            → resume_session(email, token, uid)
  3. else TTY and tty_ok              → wizard-style prompt, login, PERSIST token (never the password)
  4. else                             → NamedError: remedy = pushframe config
```

- Call sites: run_inspect, run_reconcile, run_sync, gsync (google-sync),
  run_status (already done — reuse). `_wizard_login` reused as the login
  seam; persistence via `config_store.update`.
- 401 DURING establishment with a stored token (expired/revoked): D-01 —
  TTY: one password prompt, re-login, persist new token, continue;
  non-TTY: `SessionExpiredError` (named), remedy text, no prompt.

## 2. Token-aware relogin for execute_plan (the 401-retry seam)

`execute_plan(relogin=...)` default is `aura.login` — breaks under
token-first (no password). New default behavior, injected at the
establish_session boundary:

- If env password exists → `aura.login` as today.
- Else TTY → the D-01 one-prompt path (with budget charge as today).
- Else → raise `AuthenticationError` immediately with the named remedy;
  the chunk's items are attributed as failures and the breaker may abort —
  identical semantics to a failed relogin today (no new failure shape).

## 3. `pushframe logout` (SEC-03, D-02)

- `config_store.update(auth_token=None, user_id=None)` — atomic rewrite,
  email/settings intact. Mode stays 0600.
- Output: what was removed, what was kept, remedy (`pushframe config`).
- Exit 0 also when nothing was stored (idempotent).
- Sweep asserts: no token material in output; file afterwards contains no
  `auth_token` key (file-content assertion, mirrors roadmap criterion 1).

## 4. Preflight inventory (PRF-01) with remedies

| Command | Check | Remedy text points to |
|---|---|---|
| google-sync | vault `~/.config/pushframe/google-cookies.json` present + parseable | `pushframe google-link` (+ ssh -X recipe for headless) |
| google-sync | playwright package importable (it imports the google stack) | install-shape-aware line (uv tool / pip --user) |
| sync/push | source dir exists (D-03 hard) | the exact path echoed back |
| sync/push | source dir empty → current "nothing to do", NOT an error | — |
| status/doctor | — | already green paths |
| schedule (25) | systemd user session + linger | placeholder assertion in sweep (phase 25 fills) |

## 5. The PRF-02 traceback-free sweep

Parametrized test driving each command's handler with the failing
dependency injected (mock transports / monkeypatched builders) — asserting
`rc != 0`, **no `Traceback` in captured stderr**, and the named remedy
substring present. Failure-mode list lives in 24-CONTEXT §Failure-modes;
new modes must be added there (the sweep iterates that inventory).

## 6. Test strategy

- Session flows: fake config path (tmp), fake `_wizard_login`, MockTransport
  Aura asserting **login called exactly once on expiry then success**
  (criterion 2's mocked-401 simulation), non-TTY raises SessionExpiredError.
- execute_plan relogin: existing offline harness; relogin stub asserting
  one call then surface (no loops) — criterion 2, second half.
- logout: file-content assertion + idempotency.
- Sweep: `tests/test_preflight_sweep.py`, parametrized over the inventory.

## 7. Risks / open points

- `google-sync` resolves frames AFTER login; with no session AND no env it
  must fail preflight-style before any Google work — ordering in the plan.
- The wizard currently only runs interactively; the D-01 prompt reuses it —
  keep the tty-guard one guard, not two diverging ones.
- Budget charging for the TTY relogin mid-run: follow the existing
  RETRY_RELOGIN_REQUEST_COST shape (D-06 lineage) — one acquire, no bespoke
  path.

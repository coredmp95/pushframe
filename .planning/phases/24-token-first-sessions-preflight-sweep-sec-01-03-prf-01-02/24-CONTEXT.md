# Phase 24: Token-First Sessions & Preflight Sweep — Context

**Gathered:** 2026-09-29
**Status:** Ready for planning
**Provenance:** compiled from REQUIREMENTS SEC-01..03 / PRF-01..02 verbatim,
ROADMAP §24 success criteria, a full read of the session plumbing after
phase 23 (which already delivered the stored-session foundation), and the
operator's three design decisions taken at the discuss checkpoint
(2026-09-29).

## Operator decisions (locked at discuss)

- **D-01 Expiry in an interactive session**: on a 401 while establishing or
  using the stored session, a **TTY session prompts once for the password
  and continues** (token persisted after re-login); a non-TTY (scheduled)
  run fails with a **named error and the remedy**, never a hanging prompt.
- **D-02 `pushframe logout`**: deletes **only the stored token** — email,
  settings and (future) pairs stay. The next `pushframe config` proposes
  the stored email as the default. Conforms to SEC-03 verbatim.
- **D-03 sync/push source-dir preflight**: a **nonexistent directory is a
  hard, named error before any work**; an existing-but-empty directory
  stays the current friendly "nothing to do" (not an error) — looping
  scripts over sometimes-empty dirs must not break.

## Current state after phase 23 (probe facts)

- `config_store` already persists `{email, auth_token, user_id}` 0600; the
  wizard writes it after a live login (D-03 of phase 23).
- `run_status` (only) resumes the stored session when no password env is
  set — `Aura.resume_session(email, auth_token, user_id)` exists and is
  tested. All other commands (`inspect`, `reconcile`, `sync`/`push`,
  `google-sync`) still call `aura.login()` (env-password path) — the
  env-only path is kept (SEC-02 override) but stored sessions are not
  consulted there yet.
- `execute_plan(relogin=...)` defaults to `aura.login` — **wrong seam for a
  token session** (no password in config, by design). The 401-retry path
  (REL-01/02) needs a token-aware relogin that can: try a silent token
  refresh (re-POST login only if a password is available via env/`--password`),
  else surface the named error.
- `google-sync` calls `aura.login()` unconditionally at its start
  (`gsync.py` ~line 306).
- The wizard's `_wizard_login` seam returns `{email, auth_token, user_id,
  frames}` — reuse for the TTY prompt-and-continue path (D-01).
- Preflight pattern to generalize lives in `google/bootstrap.py`
  (`_require_profile`, prerequisite checks with install-shape-aware
  remedies) + `cli.py` headless detection — proven pattern, phase 23.5.

## Failure modes inventoried for the PRF-02 sweep (traceback-free contract)

1. missing `playwright` (google-link) — done, keep the test
2. missing Chrome/Chromium — done, keep
3. headless `$DISPLAY` (google-link) — done, keep
4. near-miss env var name — done, keep
5. missing/blank vault on `google-sync` — named error, remedy `google-link`
6. ambiguous/absent `--frame` — already resolve_frame; assert in sweep
7. sync/push source dir nonexistent — NEW (D-03 hard error)
8. no credentials at all (no env, no config) — status has it; sweep the rest
9. corrupt config.json — status has it; sweep the rest
10. systemd user session absent (`schedule`, phase 25) — placeholder
11. multi-arch apt notice — docs, N/A in-code
12. token expired + non-TTY — NEW (D-01 named error)

## Constraints & invariants

- The password must NEVER be written to config.json (SEC-01) — wizard and
  prompt-and-continue paths both persist **token only**.
- Env-password path (`PUSHFRAME_PASSWORD`, `--password` in a future CLI)
  stays the override and is documented discouraged-for-humans (SEC-02).
- Token material never in logs/output (existing `_REDACT_KEYS` enforced by
  tests; extend sweep to assert on new paths).
- Suite is offline-first: every new behavior gets a mock-transport test;
  the real-API live tests stay `@live`.
- Roadmap §24 criteria are the acceptance gate (file-content test, mocked
  401 expiry re-login once-then-surface, parametrized traceback-free sweep).

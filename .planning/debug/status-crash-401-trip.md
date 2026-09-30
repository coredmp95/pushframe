---
slug: status-crash-401-trip
status: resolved
created: 2026-09-30T16:20:00+02:00
updated: 2026-09-30T16:45:00+02:00
trigger: pushframe status crashes with a raw httpx traceback when the account's anti-abuse trip is armed (401 logout:true on frames.json)
---

# Debug: status crashes with raw traceback on tripped-account 401

## Symptoms

- expected: `pushframe status` resumes the stored token session and either lists frames or fails NAMED with a remedy (PRF-02 contract: never a traceback).
- actual: uncaught `httpx.HTTPStatusError` — a 3-exception chain ending in a raw traceback.
- error: `401 Unauthorized for https://api.pushd.com/v5/frames.json — server body: {"error": true, "message": "Request Unauthenticated", "logout": true}`
- timeline: visible since the venus trip (2026-09-30); the client DOES log the body via loguru (`_raise_for_status_with_body:270`) but nothing classifies or catches it on this path.
- reproduction: `pushframe status` on an account whose trip is armed (venus, live 2026-09-30 16:15).

## Scope decisions (user)

- Fix the status UX crash ONLY — the trip itself is not a pushframe bug (the 24h protocol is documented).
- Contract: classify the 401 body — trip-shaped (`logout:true` / "Request Unauthenticated") → anti-abuse verdict + wait remedy, exit non-zero; token-shaped → `pushframe config` remedy.

## Current Focus

hypothesis: run_status has no handler around the frames read; the 401 trip classification lives only in the WRITE path (execute_plan) and doctor, so a READ-surface 401 escapes as a raw exception.
test: read run_status' try/except and the write-path classifier; confirm no read-path catch.
expecting: no except clause covering HTTPStatusError from get_frames in run_status.
next_action: gather initial evidence (run_status, classifier, PRF-02 sweep inventory).

## Evidence

- 2026-09-30T16:20:00+02:00: user transcript (venus 16:15) — establish_session SUCCEEDED (resume is offline: headers only), the 401 came from frame_api.get_frames; loguru warning from client.py:270 printed the body, then the exception propagated to the REPL.
- 2026-09-30T16:35:00+02:00: DECISIVE user experiment — with PUSHFRAME_EMAIL/PASSWORD env set (fresh login each run), `pushframe status` SUCCEEDS: login OK, frames listed (Cadre de Fabrice). Same account, same minutes: token path 401, env path 200.
- 2026-09-30T16:35:00+02:00: the 14:30 login-475 has LIFTED by ~16:35 (~2h) — the user's env login succeeded with no 475. Lockouts on the login surface clear in hours, not 24h.

## Eliminated

- hypothesis: the anti-abuse trip spread to READS (refuted 16:35) — a fresh login reads frames.json fine; the 401-on-reads with the trip-shaped body was actually a DEAD STORED TOKEN. The 14:30 wave-4 interpretation ("reads refused too") is void: that probe resumed a stale token and never reached a write.
- hypothesis: account permanently 475-locked (refuted 16:35) — login surface answers again after ~2h.

## Current Focus (updated 16:35)

hypothesis: CONFIRMED for the crash — run_status has no handler around get_frames(); the 401 trip classification lives only in the write path (sync._is_write_trip_body) and doctor. On the READ surface, the logout:true body means STALE TOKEN first (env-login reads green); trip-on-reads only if it recurs on a fresh token.
test: wrap the frames read; classify trip-shaped body (remedy: refresh token via config; caveat: if it recurs on a fresh login, it is the trip — wait); RateLimitError → wait message; generic → named + config remedy.
expecting: status exits 1 named, never tracebacks, on all three shapes.
next_action: implement + tests, then suite.

## Resolution

root_cause: run_status had no handler around the frames read — the 401 trip classification lived only in the write path (sync._is_write_trip_body) and doctor, so a read-surface 401 escaped as a raw 3-exception-chain traceback (PRF-02 violation on the one read command every operator runs first). Root cause of the USER'S alarm was different: the stored token was dead (venus resumptions), while the env path's fresh login read frames fine — proving the trip never reached reads and the 14:30 login-475 had lifted in ~2h.
fix: run_status wraps get_frames() — trip-shaped 401 body → named message with the refresh-token remedy AND the fresh-token-24h caveat; other 401 → token-rejected remedy; RateLimitError (475/429) → named WAIT (never `pushframe config`); generic → named; all exit 1, zero tracebacks. Tests: 3 new in test_cli_status.py (trip body, token body, 475) — suite 507 passed.
verification: tests/test_cli_status.py 10/10; full offline suite 507 passed; user's live repro shape covered by the trip-body test.
files_changed: pushframe/cli.py, tests/test_cli_status.py, CHANGELOG.md, .planning/debug/status-crash-401-trip.md
addendum (16:50): followed by the one-shot TTY refresh — run_status now offers ONE re-login on a refused stored token and continues in the same run; a refused FRESH login prints the 24h verdict and is never retried; non-TTY fails named. tests/test_cli_status.py 12/12, suite 509 passed. Updated contract test: fresh env creds + trip body = trip-on-reads verdict.

## Lessons

- The wave-4 interpretation "reads refused too" is VOID: that probe resumed a stale token and never reached a write. Trip-on-reads remains UNPROVEN; the 24h protocol now has a concrete re-entry criterion (same body on a FRESH login).
- The env path logs in fresh EVERY run — during tripped/token-dead periods it is the honest comparison path, but it also means each env `status` costs a login call (do not loop it).
- Silence clock resets: last pushd contact moved to 16:15–16:3x (status 401 + the user's env login). Next single probe: tomorrow ≥ 16:30, `pushframe status` on the refreshed token.

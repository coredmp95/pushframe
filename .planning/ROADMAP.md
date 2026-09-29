# pushframe Roadmap — v5.1 Operations: Config, Sessions, Multi-frame & Scheduling

**Defined:** 2026-09-29
**Ships when:** a fresh machine goes from `apt install pushframe` to a
scheduled, unattended, multi-frame Google mirror with **zero** hand-edited
config files and **zero** stored passwords.

## Milestone Goal

Make pushframe *operable* the way it is *installable*: one conversational
command (`pushframe config`) to set everything up, password-free sessions
(token-first), `pushframe schedule` for root-less systemd user timers,
named album↔frame mappings with per-pair mirror state, and preflights that
always fail with a remedy.

## Phases

### Phase 23: Config Wizard & Precedence (CFG-01..04)

**Goal**: `pushframe config` replaces env-var archaeology — interactive,
masked, validated, `0600`; env vars keep working; `show` prints effective
config with sources; `import` swallows an existing `.env`.

**Success Criteria**:

1. On a fresh machine, `pushframe config` → `pushframe status` works with
   no env vars set at all (file-only path), verified in a clean container.
2. With env vars set, they override the file — proven by a test that flips
   both and asserts which value each command sees.
3. `config show` never prints a secret value (redaction test); `config
   import` on a real `.env` reproduces the same effective config as the
   env-var path (golden diff test).

**Depends on**: nothing (master already carries the google-link preflights
that started this work).

### Phase 24: Token-First Sessions & Preflight Sweep (SEC-01..03, PRF-01..02)

**Goal**: the password is used once at login and never stored; the token
drives every subsequent command with transparent re-login on expiry; the
preflight pattern from google-link covers every command's machine
prerequisites; no foreseeable condition ends in a traceback.

**Success Criteria**:

1. `pushframe config` + login → `~/.config/pushframe/` contains a token,
   no password, mode 0600 (file-content test).
2. A token-expiry simulation (mocked 401) transparently re-logins once and
   retries the request; a second consecutive failure surfaces the login
   error (no loops).
3. The traceback-free sweep passes: every documented foreseeable failure
   mode maps to a named error with a remedy (parametrized test).

**Depends on**: Phase 23 (config file is where the token lives).

### Phase 25: Multi-frame Mappings & systemd User Scheduling (MTF-01..03, TMR-01..03)

**Goal**: GSF-01 and GSF-02 close — named album↔frame pairs (N:M),
per-pair mirror state with an account-wide shared write budget, and
`pushframe schedule` installing systemd **user** timers that run pairs
unattended (SAFE-02 skips-and-logs, per-job logs, linger documented for
headless hosts like venus).

**Success Criteria**:

1. Two pairs (one album → two frames) sync in one `--all` run, each with
   its own manifest/cache shard, the shared budget capping the total, and
   per-pair reports exact (container + fake-API proof).
2. `schedule add/list/remove` round-trips units in
   `~/.config/systemd/user/` (unit-content assertions in a container with
   systemd available); a scheduled google-sync run is prompt-free and
   logs to `~/.local/state/pushframe/<job>.log`.
3. SAFE-02 behavior under scheduling: a pair whose plan exceeds the
   removal threshold is skipped-and-logged, the unit exits 0, and the
   other pairs still run.

**Depends on**: Phase 24 (scheduled runs read token sessions, not
passwords; preflights guard scheduled contexts).

## Progress

**Execution Order:** 23 → 24 → 25

| Phase | Plans Complete | Status | Completed |
|-------|----------------|--------|-----------|
| 23. Config Wizard & Precedence | 0/? | Not started | - |
| 24. Token-First Sessions & Preflight Sweep | 0/? | Not started | - |
| 25. Multi-frame & systemd User Scheduling | 0/? | Not started | - |

## Requirement Coverage (v5.1)

| Phase | Requirements | Count |
|-------|--------------|-------|
| 23 | CFG-01..04 | 4 |
| 24 | SEC-01..03, PRF-01..02 | 5 |
| 25 | MTF-01..03, TMR-01..03 | 6 |
| **Total** | | **15 / 15** |

No orphaned requirements; no requirement mapped to more than one phase.

## Backlog

- Yank 5.0.0 from PyPI (empty-page release, superseded by 5.0.3).
- OS keyring token storage (Secret Service) as an optional backend.
- Ubuntu 26 runner observation after 2026-10-19 (deb job toolchain).

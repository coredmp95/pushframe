# Requirements: pushframe — v5.1 Operations: Config, Sessions, Multi-frame & Scheduling

**Defined:** 2026-09-29
**Core Value:** `pushframe` becomes *operable unattended*: configuration is a
conversation (`pushframe config`) instead of env-var archaeology, the Aura
password never touches disk (token-first sessions), mirrors run on a schedule
without root (systemd user timers), one album can mirror to every frame
(named mappings), and every command that needs a machine prerequisite fails
with the remedy instead of a traceback.

## Requirements

### Config wizard (CFG)

- [ ] **CFG-01**: `pushframe config` (interactive) asks for and stores: Aura
  email, Aura secret, default frame name, and the tool's non-secret settings
  (default album, write-budget override, debug) — prompts are masked for
  secrets (getpass-style), values are validated (email shape; login test on
  request), and the result is written to `~/.config/pushframe/config.json`
  with mode `0600`. Re-running edits in place (existing values shown
  redacted, Enter keeps).
- [ ] **CFG-02**: precedence is `env var > config file > built-in default`
  everywhere the CLI reads configuration; the env vars keep working exactly
  as today (deprecation window opened in 5.0.0 stays honored); `pushframe
  config show` prints the *effective* configuration with secrets redacted
  and the source of each value (env/file/default).
- [ ] **CFG-03**: `pushframe config import` migrates an existing `.env`
  (or exported `PUSHFRAME_*`/`AURA_*` set) into `config.json` without
  retyping; the command reports what it took from where and never stores
  what it could resolve from the environment instead.
- [ ] **CFG-04**: `pushframe config path` prints the config file location;
  `pushframe config set <key> <value>` / `get <key>` exist for scripts;
  unknown keys fail with the list of known keys.

### Credential handling (SEC — token-first)

- [ ] **SEC-01**: the Aura password is **never persisted** — not in
  `config.json`, not in env files the tool writes. `pushframe config`
  authenticates once, stores the returned `auth_token` (0600) plus the
  account email, and discards the password when the process exits.
- [ ] **SEC-02**: every command that needs the API uses the stored token
  (header auth) and falls back to interactive password login **only** when
  the token is absent/expired/revoked, after which the new token is
  persisted; a `--password` / `PUSHFRAME_PASSWORD` override still wins
  (CI/script path) but is documented as discouraged for humans.
- [ ] **SEC-03**: `pushframe logout` deletes the stored token (and only the
  token); token material never appears in logs, output, or the history
  ledger (existing redaction stays enforced by tests).

### Scheduling (TMR — systemd user)

- [ ] **TMR-01**: `pushframe schedule add <job>` installs a systemd **user**
  timer + service unit under `~/.config/systemd/user/` for the supported
  jobs (`google-sync --apply` for a named pair, directory `sync`) with a
  prompted or `--every` schedule; no root anywhere; `pushframe schedule
  list` shows installed jobs with next-run times (from `systemctl list-timers`).
- [ ] **TMR-02**: `pushframe schedule remove <job>` uninstalls cleanly
  (units + timer state); installed units are named `pushframe-<job>.service/.timer`
  and never collide with system units.
- [ ] **TMR-03**: unattended correctness: a scheduled run uses only
  stored configuration (no prompts), honors SAFE-02's removal threshold
  (a run that would exceed it **skips and logs** rather than fails the unit),
  writes a per-job log (`~/.local/state/pushframe/<job>.log`), and the
  docs cover `loginctl enable-linger` for headless machines (venus).

### Multi-frame (MTF — GSF-01 closed)

- [ ] **MTF-01**: named **album↔frame mappings** live in config: one album
  may mirror to N frames and one frame may receive from N albums
  (`pushframe config` manages them; `--pair <name>` selects one,
  `--all` selects every pair).
- [ ] **MTF-02**: `google-sync` resolves a pair the same way it resolves the
  current single pair today (dry-run default, `--apply` gated, SAFE-01..04
  per pair); **state is per-pair** — the manifest and staging cache are
  sharded by pair so two pairs never share dedupe memory.
- [ ] **MTF-03**: the write budget is **account-wide and shared** across
  pairs within one run (`--all`), preserving SAFE-02 semantics at account
  level; per-pair reports stay exact (pairs list what they did, and a
  skipped pair never blocks the others).

### Preflights & error quality (PRF)

- [ ] **PRF-01**: commands with machine prerequisites check them up front
  and fail with per-item remedies — `google-link` (playwright package,
  Chrome/Chromium, profile dir) ships in 5.1's first cut (already
  implemented on master), extended to: `google-sync` (vault present),
  `schedule` (systemd user session available, `loginctl` linger status),
  and `sync`/`push` (target dir exists/contains images).
- [ ] **PRF-02**: no user-facing command may end in an unhandled traceback
  for a foreseeable condition (missing module, missing browser, missing
  vault, ambiguous frame, multi-arch notice): each maps to a named,
  one-screen error with the next action. A test sweep asserts the
  traceback-free contract for the documented failure modes.

## Non-Goals

| Feature | Reason |
|---------|--------|
| OS keyring storage (gnome-keyring/Secret Service) | headless servers (venus) lack a keyring session; file-0600 token is the contract, keyring is a later nicety |
| Encrypting config.json with a user passphrase | circularity (where does the passphrase live?); token-first removes the high-value secret anyway |
| Cron support | systemd user timers are the modern path; cron adds a second code path for no new capability |
| Real deletion exposure on the Google verb | unchanged posture from v2.0/v4.0; hide-not-delete stays |
| Many-to-many with per-pair schedules | each pair is its own scheduled job — same capability, simpler model |

## Traceability

Every v5.1 requirement maps to exactly one phase; numbering continues from
Phase 22.

| Requirement | Phase | Status |
|-------------|-------|--------|
| CFG-01..04 | Phase 23 | Pending |
| SEC-01..03 | Phase 24 | Pending |
| PRF-01..02 | Phase 24 | Pending |
| MTF-01..03 | Phase 25 | Pending |
| TMR-01..03 | Phase 25 | Pending |

**Coverage:** v5.1 requirements: 14 total — no orphans, no double-mapping.

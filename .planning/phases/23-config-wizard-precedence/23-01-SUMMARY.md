# Plan 23-01 Execution Summary — Dynamic config resolution + `pushframe config` (CFG-01..04)

**Phase:** 23-config-wizard-precedence
**Date:** 2026-09-29
**Status:** Complete — suite **426 passed** (was 420 before the phase; +6 wizard tests, +8 resolution/store tests, 2 legacy alias asserts updated to the dynamic contract)
**Commits:** `936a2c4` (T1 dynamic resolution + config_store) · `acd3ba1` (T3 docs) · T2 wizard shipped in the T1→T2 working commit on this branch

## What Shipped

- **Dynamic settings resolution (D-01/D-04, CFG-02)** — `settings.py` rewritten
  around PEP 562 module `__getattr__`: every `settings.X` access resolves live as
  **environment → config file (`~/.config/pushframe/config.json`) → DEFAULTS
  table**. All existing importers kept their attribute-style reads (the 411→420
  regression passed byte-for-byte on defaults); `s3client`/`sqsclient` converted
  from frozen `from settings import X` copies to module-attribute reads so
  long-running processes see updates.
- **`config_store.py` (D-02)** — 0600 JSON store, atomic writes (tmp + rename),
  known-key whitelist (fail-loud on unknown keys), schema-versioned, `"pairs"`
  key reserved for phase 25 (multi-frame). Reads `settings.CONFIG_PATH`
  dynamically (module import, not from-import) so tests and the wizard share
  one path truth.
- **The `config` family (D-03, CFG-01/03/04)** — wired into the CLI parser and
  dispatch:
  - `pushframe config` — wizard: email → hidden password → **real API login
    before anything is written** (fail = zero writes); stores email +
    `auth_token`; optional questions afterwards; refuses non-tty stdin for the
    interactive path (scheduled jobs never hang on a prompt).
  - `config show` — every setting with effective value (secrets `***`) and
    its source (`env`/`file`/`default`) + explicit env-shadow warnings.
  - `config import FILE` — adopts `.env`-style files; skips env-provided keys
    (reported), validates against the whitelist.
  - `config set/get/path` — single-key direct access; `get` reflects true
    precedence.
- **Docs (T3)** — README gets a wizard-first *Configuration* section (env vars
  folded into an override `<details>`); `docs/CLI.md` gets a full `config`
  command section + precedence-aware env-var intro; CHANGELOG `[Unreleased]`
  carries Added/Changed entries.

## Broken-and-fixed along the way

- CHANGELOG had been corrupted by the session interruption (sections out of
  order: Unreleased/5.0.4/5.0.3 stuck *after* 5.0.0; 5.0.4 content duplicated
  inside its own block). Rewritten cleanly in Keep-a-Changelog order,
  deduplicated — no content lost.
- `config_store` initially froze `CONFIG_PATH` via from-import (test
  monkeypatch invisible) → module-attribute read.
- `_bool_env` signature drifted from the historical `default=` keyword →
  restored (12 budget tests green again).
- AWS from-imports froze values at import time → dynamic access (matches the
  phase-19 MOD-02 intent; two legacy alias asserts updated accordingly).

## Live proofs

- Wrong-password login against the real API raises
  (`RateLimitError … HTTP 475`, the account's current Aura-side lockout) and
  writes **no config.json** — the D-03 "nothing written on failed login"
  contract, verified live. A successful-login wizard run was not repeated
  against the real API to avoid worsening the lockout; the seam
  (`_wizard_login`) is exactly the tested `Aura.login` path.

## Must-have truths check (plan 23-01)

| Truth | Status |
|---|---|
| Env/file/default precedence at access time | ✅ PEP 562 + DEFAULTS table (8 tests) |
| config.json 0600, atomic, whitelist, schema-versioned | ✅ config_store + tests |
| Wizard tests login before writing; fail = zero writes | ✅ 6 tests + live negative proof |
| `config show` reports source per key + shadows | ✅ implemented + tested |
| Import skips env-provided keys | ✅ tested (env-shadow contract) |
| Existing readers unchanged | ✅ 426 passed incl. all pre-phase tests |

---
phase: "23"
name: "Config Wizard & Precedence"
created: 2026-09-29
status: passed
---

# Phase 23 Verification — Config Wizard & Precedence

**Date:** 2026-09-29
**Status:** PASSED — 431 offline tests green; all three ROADMAP §23 success
criteria evidenced, criterion 1 by a pristine-container journey.

## Criteria → Evidence

| # | Criterion (ROADMAP §23) | Evidence | Result |
|---|--------------------------|----------|--------|
| 1 | Fresh machine: `pushframe config` → `pushframe status` works with no env vars at all (file-only path), clean container | `scripts/test-config-journey-container.sh`: pristine `ubuntu:26.04`, wheel install via uv standalone, wizard driven through a pty against a **fake API**, config stored 0600 **without the password**, then `pushframe status` under `env -i` (zero env vars) lists the frame; the fake API's hit log proves `login.json` was hit **exactly once** (wizard only) — status resumed the stored session | ✅ PASSED |
| 2 | Env vars override the file — proven by a test that flips both | `tests/test_settings_resolution.py::test_precedence_flip_env_always_wins`: both sources flipped (env=de-DE/file=fr-FR, then env=fr-FR/file=de-DE); resolved value follows env both times. Plus `config show` shadow warnings and `shadowed_keys()` | ✅ PASSED |
| 3 | `config show` never prints a secret; `config import` on a real `.env` reproduces the same effective config as the env path (golden diff) | `tests/test_config_wizard.py::test_show_redacts_secrets_and_lists_sources` (secrets `***`, per-key source); `::test_import_golden_same_effective_config_as_env_path` (import-then-file resolves byte-identically to env-only for the same values) | ✅ PASSED |

## Extra evidence beyond the criteria

- **Suite**: 431 passed offline (was 411 pre-phase; +20: resolution/store 9, wizard
  family 8 incl. the hermetic wizard→status journey, stored-session status 3).
- **Live negative proof** (D-03): a wrong-password `_wizard_login` against the real
  API raised (HTTP 475 — account lockout) and wrote no `config.json`.
- **Two latent bugs found and fixed by the journey**: `_wizard_login` read session
  facts off `Aura.login()`'s return value (the Aura itself, not the user) — the
  first real wizard login would have crashed, hidden by mocks; and
  `PUSHFRAME_API_BASE_URL` had been declared in DEFAULTS but `client.py` still read
  its own frozen constant, so the override never took effect.

## Scope honesty

- The stored-session resume covers `status` only; generalizing token sessions to
  every verb (and re-login on expiry) is exactly phase 24 (SEC-01..03).
- `auth_token` sits in a 0600 file — same threat model as the Google cookie vault.
  OS keychain integration was considered and deferred (phase 22 D-05 posture).
- The wizard's optional questions stop at country/budget/debug for now; pair
  management arrives with multi-frame in phase 25 (`pairs` key already reserved
  in the config schema).

## Files

- Code: `pushframe/utils/settings.py` (PEP 562 dynamic resolution),
  `pushframe/config_store.py` (0600 store), `pushframe/cli.py` (config family,
  stored-session status), `pushframe/aura.py` (`resume_session`), 
  `pushframe/client.py` (settings-driven base URL)
- Tests: `tests/test_settings_resolution.py`, `tests/test_config_wizard.py`,
  `tests/test_cli_status.py`
- Journey: `scripts/test-config-journey-container.sh`
- Docs: README (wizard-first Configuration), `docs/CLI.md` (`config` section),
  `CHANGELOG.md` `[Unreleased]`

**Commits:** `936a2c4` (dynamic resolution + store) · wizard family (branch commits) ·
`acd3ba1` (docs + changelog repair) · `3ef9b49` (stored-session status + journey)

# Phase 23 Research — config plumbing & wizard seams

**Date:** 2026-09-29 · probes on master (post-5.0.4).

## R1. settings.py inventory (the full surface)

Constants (all via `_env('PUSHFRAME_X', 'AURA_X') or default`):
LOCALE, AURA_APP_IDENTIFIER, DEVICE_IDENTIFIER, AURA_API_BASE_URL,
AURA_API_VERSION, AURA_WRITE_BUDGET_CAPACITY (30), AURA_WRITE_BUDGET_REFILL_PER_MIN (0.75),
AURA_WRITE_BUDGET_WAIT (True), AURA_WRITE_BUDGET_MAX_WAIT (3600), and
GOOGLE_SYNC_REMOVAL_THRESHOLD (20%) — read by cli.py, export.py, the api/
package, and aws/. No importer mutates settings; all read attribute-style
at call time (`settings.X`), which makes attribute-level dynamic
resolution a drop-in. (Code refs: settings.py L16-45, importers listed in
23-CONTEXT.)

## R2. Dynamic-resolution mechanics (D-01)

Constraint: `from pushframe.utils import settings` + `settings.LOCALE`
must keep working WITHOUT importers changing. Two viable shapes:

- **Module `__getattr__`** (PEP 562): define `def __getattr__(name)` in
  settings.py; resolution order: config.json key → env var → default
  table. Keeps lazy semantics, zero importer churn, and the DEFAULTS
  dict becomes the single table for both resolution and `config set`
  key validation (CFG-04's "unknown keys fail with the list").
- Plain functions (`settings.get('LOCALE')`): explicit but breaks the
  attribute-style importers — rejected.

`__getattr__` only fires for names NOT defined at module level, so the
module defines only helpers + `__getattr__` + explicit constants that
must stay static (`DEFAULT_VAULT_PATH` lives in vault.py, unaffected).

## R3. Config file schema (D-02)

```json
{
  "version": 1,
  "email": "coredmp95@gmail.com",
  "auth_token": "...",          // stored here from phase 23 on (SEC-01 refines in 24)
  "default_frame": "Cadre de Fabrice",
  "debug": false,
  "settings": {                 // optional overrides, key = settings name
    "AURA_WRITE_BUDGET_CAPACITY": 60
  },
  "pairs": {}                   // reserved for phase 25 (MTF-01)
}
```

0600 via os.open with mode; atomic write via tmp+rename (the manifest
pattern — google/manifest.py `_atomic_write`).

## R4. Wizard flow (D-03)

1. If config.json exists: show current email (redacted), Enter keeps.
2. email prompt → shape check.
3. password getpass → live `Aura().login()` (reuse cli's login path).
4. On success: persist email + `user.auth_token`; proceed to optional
   prompts (default frame chosen from `get_frames()` when available;
   debug y/n). On failure: print the API's message, write NOTHING, exit 1.
5. Warn when an env var (PUSHFRAME_/AURA_ prefix) currently shadows any
   key the wizard just wrote (D-04 lesson from venus).

Non-interactive guard: `config` commands refuse when stdin is not a tty
and required input is missing — fail loud (same posture as `google-sync
--apply` requiring `--yes`).

## R5. Import-existing (CFG-03)

`config import`: read `.env` (cwd, then --file), map
PUSHFRAME_*/AURA_* keys onto settings names, write into config.json ONLY
the keys that are NOT currently provided by the environment (CFG-03's
"never store what env already resolves"), report a table
key → source (file/env/default).

## R6. Hermetic tests

- Fixture `tmp_config` (monkeypatch the module-level CONFIG_PATH in
  settings.py; the test_migration.py pattern).
- Wizard tests: stdin fed via monkeypatched input/getpass; login mocked
  at the `Aura` seam (same seam as TEST-02); assert file mode + content
  + "nothing written on failure".
- Precedence tests: file says X, env says Y → resolved Y; unset env → X.
- `config show` redaction test asserts the token/password never appear.

## R7. Release shape

This phase ships as 5.1.0's first cut (or 5.0.5 if pulled early — no
decision needed now); CHANGELOG `[Unreleased]` accumulates.

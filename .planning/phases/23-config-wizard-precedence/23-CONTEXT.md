# Phase 23: Config Wizard & Precedence — Context

**Gathered:** 2026-09-29
**Status:** Ready for planning
**Provenance:** compiled from REQUIREMENTS CFG-01..04, a full read of the
current config plumbing (`pushframe/utils/settings.py`, its 7+ importers,
`cli.py`'s env reads), and the operator's design decisions taken at the
discuss checkpoint (2026-09-29).

## Current state (probe facts)

- `pushframe/utils/settings.py` exposes ~15 **module constants evaluated
  at import** via the `_env(*names)` helper: PUSHFRAME_* primary, AURA_*
  legacy fallback (IDN-04), hard-coded defaults last.
- Importers do `from pushframe.utils import settings` and read
  `settings.LOCALE`, `settings.AURA_API_BASE_URL`, budget constants, etc.
  — none import `settings` as mutable state they replace.
- Credentials today: `PUSHFRAME_EMAIL` / `PUSHFRAME_PASSWORD` read in
  `pushframe/aura.py` (login) and `cli.py` (status health check prints
  set/NOT SET only).
- Existing JSON in `~/.config/pushframe/`: `google-cookies.json` (0600
  vault), `google-manifest.json` (0600), budget file. A config.json sits
  naturally in that family.
- The deb/wheel ships no `python-dotenv` auto-import for user shells
  (`.env` is loaded by `main()` via `load_dotenv()` — cwd-relative, a
  known fragility the file-config supersedes for humans).

## Design decisions (operator, 2026-09-29)

- **D-01:** Dynamic resolution — `settings.py` keeps its public names but
  each becomes a *resolved-at-access* value with precedence
  **config file → env var → built-in default**; importers stay untouched.
  `config set` affects the next process and any re-read; no stale
  import-time snapshot. Implementation must keep the offline test suite
  hermetic (tests patch the file path / env, never the network).
- **D-02:** JSON — `~/.config/pushframe/config.json`, mode 0600, stdlib
  `json` only — same family as the vault/manifest. The MTF-01 pairs
  (phase 25) will live there as a dict.
- **D-03:** Wizard order: credentials first, login-tested — email →
  password (getpass, masked) → **live login test** → on success store
  email + the returned `auth_token` (SEC-01 lands in phase 24; phase 23
  already stores the token since the API hands it back) → optional
  questions (default frame — listed from a real `get_frames` when
  credentials work —, debug). Login failure ⇒ clear message, **nothing
  is written**.
- **D-04:** Precedence (locked): env var > config file > default —
  Rationale: 12-factor; CI/scripts keep working unchanged; the file is
  human comfort. The wizard warns when an env var would shadow the file
  value it just wrote (the venus lesson: silent shadowing is the trap).

## Non-goals for this phase

- Token *expiry/re-login* semantics (phase 24, SEC-02).
- OS keyring backends, config encryption (v5.1 non-goals table).
- MTF pairs schema beyond reserving the `"pairs": {}` key in the file
  schema (phase 25).

## Operator steps

None — everything is in-repo; the phase ships in the next release.

## Discretion areas (executor)

- Exact wizard prompt wording/order of optional questions.
- Whether `config show` reads settings lazily per key or snapshots
  (recommend lazy — matches D-01).
- Test seam choice: fixture writing a temp config.json + monkeypatched
  path constant (existing pattern in tests/test_migration.py).

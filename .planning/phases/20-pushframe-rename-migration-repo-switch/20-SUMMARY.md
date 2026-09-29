---
phase: 20-pushframe-rename-migration-repo-switch
plan: 01
subsystem: identity
tags: [rename, pushframe, migration, idn, offline-tested]
requires:
  - pyproject.toml (name, scripts, wheel target)
  - pushframe/utils/settings.py (env resolution)
  - pushframe/google/vault.py + manifest.py (0600 file discipline)
provides:
  - `pushframe` binary + `pushframe` Python package (no aura-cli anywhere)
  - pushframe/migration.py (one-shot config migration, migration_notice())
  - PUSHFRAME_* primary env spellings with AURA_* legacy fallback
  - README provenance callout + updated revive credit; docs/CLI.md + .env.sample renamed
stems_from: []
keys_in: []
keys_out: []

decisions:
  - IDN-01/02 honored — repo-wide sed with grep gates (0 occurrences outside the
    migration module + one explanatory comment); console script pushframe only.
  - IDN-03 honored — copy-based migration (shutil.copy2), never a move; old dir
    kept; idempotent by construction (new dir exists ⇒ no-op). LIVE-VERIFIED on
    the operator's real machine: vault+manifest+probes+budget migrated, 0600
    preserved, google link still usable, second run printed nothing.
  - IDN-04 honored — _env(*names) helper resolves PUSHFRAME_* first, AURA_* as
    documented fallback; applied to credentials, locale, device id, state dir,
    budget, geo, AWS vars, Chrome profile, removal threshold.
  - IDN-05 honored — README provenance callout up top (not-affiliated disclaimer),
    Credits block updated to the full revive story (incl. google mirror + v5.0
    rename), docs/CLI.md + .env.sample renamed to pushframe spellings.
  - IDN-06 honored — done ahead of this phase (see D-06 note): new standalone repo
    coredmp95/pushframe (isFork false), full history pushed, origin switched, old
    fork kept as archive under the local remote name old-fork-archive. LICENSE
    (MIT + provenance) added earlier the same day.

session_type: execution
completed: 2026-09-29
commits: [ca0a456]
---
# Phase 20 Execution Summary: pushframe Rename, Migration & Repo Switch

**Status:** complete · **Commits:** ca0a456 (+ 1a19447/2a8f0b2 license, b4eb438 badges, fd80c95..d1d5afb milestone init)

## Delivered

- **Rename (IDN-01/02):** `git mv auraframes pushframe` + repo-wide import/name
  rewrite; pyproject: `name = "pushframe"`, `[tool.hatch.build.targets.wheel]
  packages = ["pushframe"]`, console script `pushframe = "pushframe.cli:main"`.
  Gates: `grep -rn '\bauraframes\b' --include=*.py` outside the migration module
  and one explanatory comment → **0 matches**; `aura-cli` → 0 anywhere.
- **Migration (IDN-03):** `pushframe/migration.py` — one-shot copy-based migration
  of the config home; `migration_notice()` called first thing in `main()`.
  6 offline tests (fresh-machine silence, copy+non-destructive, 0600 preserved,
  idempotence, never-overwrite, main() wiring incl. argparse SystemExit).
- **Env (IDN-04):** `settings._env(*names)` first-wins resolution; all vars
  documented as `PUSHFRAME_*` with `AURA_*` fallback; credentials in
  `Aura.login` and the CLI (`status` prints `PUSHFRAME_EMAIL/PASSWORD`).
- **Docs (IDN-05):** README — provenance callout (upstream link + not-affiliated),
  Credits revive block updated to the full story, env table + Google paths +
  all commands renamed; docs/CLI.md and .env.sample likewise.
- **Repo switch (IDN-06):** completed earlier in the day — new standalone repo
  `coredmp95/pushframe` (not a fork, API-verified), 337-commit history pushed,
  origin switched, phase branches re-pushed to the new repo; LICENSE (MIT,
  dual copyright + provenance note) and CI tests workflow added.

## Verification (success criteria 1-5)

1. **Green rename:** 407 passed offline (401 pre-existing + 6 migration); grep gate 0.
2. **Migration on a real install:** live run migrated the operator's actual
   `~/.config/auraframes/` (vault incl. legacy probes location, manifest, budget,
   chrome-profile) → `~/.config/pushframe/`; `pushframe status` right after shows
   `linked: yes`; 0600 preserved (`stat` 600); second run silent.
3. **Fresh machine:** covered by test (`test_fresh_machine_is_silent_noop`).
4. **PUSHFRAME_* env:** primary everywhere (code + docs); legacy fallback kept and documented.
5. **Repo:** standalone, full history, origin → pushframe; README provenance note credits zmanowar with the upstream link; old fork untouched.

## Deviations

- Phase executed without a pre-written PLAN file (roadmap criteria + inline task
  breakdown; single-phase, single-commit production). Recorded here for honesty;
  SUMMARY + commit serve as the plan-of-record.
- `--version` flag (REL-02) deliberately NOT added here — it belongs to Phase 22's
  release engineering with the version single-sourcing decision.

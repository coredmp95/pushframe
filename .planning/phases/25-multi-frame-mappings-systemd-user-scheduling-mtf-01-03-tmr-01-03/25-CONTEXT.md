# Phase 25: Multi-frame Mappings & systemd User Scheduling — Context

**Gathered:** 2026-09-29
**Status:** Ready for planning
**Provenance:** REQUIREMENTS MTF-01..03 / TMR-01..03 verbatim, ROADMAP §25
criteria, code probe of the state-sharding points, operator decisions
locked at the discuss checkpoint (2026-09-29).

## Operator decisions (locked)

- **D-01 Pair schema**: named dictionary in config.json's (already
  reserved) `pairs` key —
  `{"cadre-venus": {"album": "Cadre", "frame": "Cadre de Fabrice"}}`.
  Stable operator-chosen names; `--pair <name>` and timers reference them;
  `--all` executes in dict order.
- **D-02 Isolation in `--all`**: a failing pair is recorded in the global
  report and the remaining pairs still run. Exit 1 if ANY pair failed,
  0 only if all OK. (MTF-03's "skipped pair never blocks the others".)
- Carried from phase 24 discuss: scheduled runs must be prompt-free —
  token sessions (never passwords), preflights guard, SAFE-02 **skips and
  logs** on threshold breach instead of failing the unit.

## Code-probe facts (where state lives today)

- Single-pair state today: manifest defaults to
  `~/.config/pushframe/google-manifest.json` (`GoogleManifest.load(None)`)
  and cache defaults to `default_cache_dir(album.album_id)` — BOTH are
  sharding points already parameterized (`manifest_path=`, `cache_dir=`
  kwargs exist on `run_google_sync`). Multi-pair = compute per-pair
  default paths from the pair name; no engine change needed.
- `config_store` already whitelists `pairs` and defaults it to `{}`.
- Write budget: built per account email in `_build_write_budget`; the
  `--all` runner passes ONE budget instance across pairs → account-wide
  sharing falls out naturally (MTF-03), persisted budget state on disk is
  already per-email.
- Phase 24 gave the prompt-free session (`establish_session` non-TTY
  contract) and the trip guards (first-occurrence stop, 401 body capture)
  — scheduled runs inherit them.
- `pushframe schedule` does not exist yet; units would live in
  `~/.config/systemd/user/` named `pushframe-<job>.service/.timer`; logs
  at `~/.local/state/pushframe/<job>.log` (TMR-03).
- Venus constraint (2026-09): the account trips on write volume; timers
  MUST default to conservative pacing (`--batch-size` forwarded from the
  pair config, `OnCalendar` spaced, `RandomizedDelaySec` to de-sync hosts).

## Failure modes to add to the PRF sweep inventory

- `--pair <name>` unknown → named error listing installed pair names
- `--all` with zero pairs configured → named error with the
  `pushframe config` remedy (never a silent success)
- `schedule add` without a systemd user session (`loginctl` absent) →
  named error + linger remedy
- duplicate pair name on create → named error (never silently overwrite)

## Constraints & invariants

- SAFE-01..04 apply PER PAIR (threshold is computed against that pair's
  target frame), and the shared budget caps the whole `--all` run.
- Timers install as user units; no root anywhere; `loginctl enable-linger`
  documented for headless hosts (venus) — never executed by the tool.
- Dry-run remains the default for google-sync; scheduled jobs pass
  `--apply --yes` explicitly in the generated unit's ExecStart.
- The `TripDetectedError` first-occurrence stop and the 60-min guidance
  apply to scheduled runs: the unit must NOT restart on failure
  (`Restart=no`), the next timer tick is the retry.

# Phase 25 — Research

**Researched:** 2026-09-29 (code reads + venus trip constraints)

## 1. Pair management (MTF-01, D-01)

Config shape (schema already whitelisted):

```json
{"pairs": {
  "cadre-venus": {"album": "Cadre", "frame": "Cadre de Fabrice"}
}}
```

- `pushframe config pair add <name>` — prompts album + frame (both
  validated: album resolvable later at run time, frame by substring at run
  time); duplicate name = named error.
- `pushframe config pair remove <name>` / `config pair list` — the latter
  prints name → album → frame and the per-pair state paths (manifest/cache)
  so the operator sees what a delete would orphan.
- CLI surface: `google-sync` grows `--pair <name>` and `--all`
  (mutually exclusive; neither = today's direct album/frame behavior,
  unchanged). Scheduled units use `--pair`.

## 2. State sharding (MTF-02)

Per-pair state paths derived from the pair name (NOT the album id — the
name is stable across album re-shares):

- manifest: `~/.config/pushframe/pairs/<name>/google-manifest.json`
- cache:   `~/.local/state/pushframe/pairs/<name>/cache/`
  (staging is transient/state-like → XDG_STATE; manifests are durable
  config-adjacent → XDG_CONFIG, mirroring today's layout)

Both already parameterized (`manifest_path=`, `cache_dir=`). A legacy
single-pair default path is NOT auto-migrated: today's single-pair users
keep their behavior when they don't use pairs (paths untouched); the docs
note a manual move for those who adopt pairs after running direct.

## 3. The `--all` runner (MTF-03, D-02)

```
for name, spec in pairs.items():            # dict order
    rc = run_google_sync(spec.album, spec.frame, apply=..., yes=...,
                         budget=shared_budget,          # ONE instance
                         manifest_path=pairs/<name>/google-manifest.json,
                         cache_dir=~/.local/state/pushframe/pairs/<name>/cache,
                         batch_size=spec.get('batch_size'))
    results[name] = rc                                   # never aborts the loop
report: per-pair one-liner (uploaded/hidden/skipped+reason)
exit 0 only if all rc==0
```

- The shared budget instance is the ONLY cross-pair coupling — SAFE-02's
  account-level cap falls out of the existing token-bucket.
- A pair raising (trip, vault missing) is caught, reported with its error
  class, and the loop continues (D-02). TripDetectedError marks the pair
  SKIPPED-TRIPPED and, honestly, also predicts the next pairs will trip —
  report says so, but the loop still runs (cheap refusals, clean stops).

## 4. `pushframe schedule` (TMR-01..03)

- `schedule add <job> --pair <name> --every <N><unit>|--at "…OnCalendar…"`
  (and the directory-sync variant `--sync-dir <path> --frame <frame>`):
  writes `~/.config/systemd/user/pushframe-<job>.service` + `.timer`.
- ExecStart (google-sync pair): `pushframe google-sync <album> --frame
  <frame> --apply --yes --batch-size <N>` — every token comes from stored
  config; NOTHING interactive (phase-24 contract guarantees no prompts).
- Unit invariants: `Type=oneshot`, `Restart=no` (the next tick is the
  retry — never a restart loop against a trip), `OnCalendar` per `--every`
  mapping (`--every 30min` → `OnCalendar=*:0/30`), `RandomizedDelaySec=15m`
  to de-sync multiple jobs, `StandardOutput=append:~/.local/state/pushframe/
  <job>.log` (TMR-03's per-job log; systemd ≥ 240 append: support) with a
  fallback to a wrapped `sh -c '… >> log 2>&1'` when `append:` is
  unsupported.
- SAFE-02 in scheduled context: already skip-and-log? — VERIFY: today the
  threshold gate with `yes=True` **proceeds**! For scheduled runs that is
  wrong per TMR-03. Fix: a `--scheduled` flag on google-sync (set by the
  generated unit) flips the threshold gate from "proceed" to "skip with a
  logged reason".
- `schedule list` parses `systemctl --user list-timers pushframe-*` (name,
  next run, unit state); `schedule remove <job>` stops+disables the timer,
  removes both unit files (daemon-reload), never touches pair state.
- Preflight for `schedule add`: `systemctl --user` works (session bus) —
  named error + `loginctl enable-linger` remedy otherwise (the docs also
  cover linger; the tool never runs it — it needs the user's session).

## 5. Test strategy

- Pairs CRUD: config file-content tests (schema, duplicates, list output).
- `--all`: container journey (roadmap criterion 1) + offline runner test —
  two pairs, shared budget instance asserted identical, failing first pair
  recorded, second still runs, exit 1, report lines exact.
- Sharding: two pairs' manifests/caches land in distinct per-pair paths
  (file-tree assertions).
- Units: render functions pure → unit-content assertions offline; a
  systemd-available container journey round-trips add/list/remove (criterion
  2).
- Prompt-free: scheduled google-sync with expired token non-TTY →
  SessionExpiredError logged, unit fails, no hang (phase-24 inheritance).

## 6. Risks / open points

- `append:` in StandardOutput needs systemd ≥ v240 (Ubuntu 20.04+ fine);
  the sh -c wrapper fallback keeps older hosts working.
- `systemctl --user` over SSH requires the user bus (lingering or an
  active session) — the preflight names it instead of failing obscurely.
- The `--scheduled` threshold flip changes google-sync's gate semantics —
  gated behind the explicit flag, default behavior untouched (tested).

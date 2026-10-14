---
phase: quick-261014-nim
plan: 01
subsystem: schedule, pairs, google-sync, cli resolution
tags: [security, systemd, path-safety, manifest-drift, name-resolution]
requires: []
provides:
  - "pushframe/utils/names.py safe_name_problem: one pair/job name rule"
  - "schedule._systemd_arg + exec_start_argv: systemd-correct ExecStart quoting and its inverse"
  - "google/cache.is_safe_media_id: media ids never become unsafe paths"
  - "gsync manifest drift: names items, --apply evicts, next run self-heals"
  - "cli resolve_album/resolve_frame: exact name first"
affects: [pushframe/schedule.py, pushframe/pairs.py, pushframe/gsync.py, pushframe/google/cache.py, pushframe/cli.py]
tech-stack:
  added: []
  patterns: ["render-all-before-write", "per-item failure instead of path construction from untrusted ids", "three-tier name resolution"]
key-files:
  created: [pushframe/utils/names.py]
  modified:
    - pushframe/schedule.py
    - pushframe/pairs.py
    - pushframe/cli.py
    - pushframe/gsync.py
    - pushframe/google/cache.py
    - tests/test_schedule.py
    - tests/test_pairs.py
    - tests/test_google_cache.py
    - tests/test_gsync_engine.py
    - tests/test_gsync_execute.py
    - tests/test_cli_google_album.py
    - tests/test_cli_inspect.py
    - tests/test_cli_reconcile.py
    - tests/test_cli_sync.py
    - docs/ERRORS.md
    - docs/CLI.md
decisions:
  - "ExecStart elements go through one helper; ordinary values stay byte-identical to 5.1.33 (pinned by a characterization test)"
  - "pair_remove stays unvalidated so the remedy works on a hand-edited config.json"
  - "Manifest drift still fails the run; only an --apply run evicts the drifted entries (dry-run never mutates)"
status: complete
metrics:
  duration: "single session"
  completed: 2026-10-14
actuals:
  tokens: 16750
  tasks: 3
  commits: 6
plan_head_before: 9610c3056b2ef7e3d1688275b230e9ed49ad7cb8
plan_head_after: 2c2979a014fc22b3c482e9d7c5262da57c8ee482
---

# Phase quick-261014-nim Plan 01: Review hardening batch 2 Summary

Five findings from the 2026-10-14 review are fixed with red tests first: systemd-correct ExecStart quoting, one safe-name rule for pairs and jobs, media ids that can no longer become unsafe paths, a self-healing manifest-drift path, and exact-name-first album/frame resolution.

## Tasks

| Task | Name | Commits (red, green) |
|------|------|----------------------|
| 1 (tracer) | ExecStart quoting + name rule (NIM-S1, NIM-S2) | 5f1057c, 3e6559d |
| 2 | Unsafe media ids + manifest drift self-heal (NIM-S3, NIM-B4) | 6049224, 56ba74c |
| 3 | Exact name beats substring (NIM-B5) | b942bd0, 2c2979a |

## What changed

- NIM-S1: `schedule._systemd_arg` is the single renderer of an ExecStart element (escapes `\`, `"`, doubles `%` and `$`, refuses control characters; `$` in the executable path is refused because systemd never expands the program name). `schedule.exec_start_argv` is the inverse and now backs `pushframe status`. `schedule add` renders all three unit texts before the first write: any refusal prints `schedule: ...`, returns 1, writes nothing, calls systemctl zero times. Units are written as UTF-8. The two pinned ordinary ExecStart literals are unchanged (characterization test passed before and after).
- NIM-S2: `pushframe/utils/names.py` (`[A-Za-z0-9][A-Za-z0-9._-]{0,63}`, fullmatch) is enforced at `pair_add`, `pair_state_paths`, `schedule_add`/`schedule_remove`, `render_service`/`render_timer` and `_rotate_job_log_if_large`. `pair_remove` stays unvalidated. The `--pair`/`--all` loop records a hand-edited unsafe pair as FAILED and continues (D-02).
- NIM-S3: `is_safe_media_id` (`[A-Za-z0-9_-]{1,255}`) gates `_download_one`, the orphan cleanup and `prune_cache`; `default_cache_dir` refuses an unsafe token; failure lines print `(unsafe media id)` via `_gid_label`. The victim-file regression test proves nothing outside the cache is written or unlinked.
- NIM-B4: `SafeSyncError.drifted_ids` + `manifest_drift(drifted_ids)` name the count and redacted ids; `run_google_sync_plan` and `compute_plan` stay pure. A dry-run leaves the manifest byte-identical; an `--apply` run evicts exactly the drifted entries through `GoogleManifest.save` (0600) and the next `--apply` run re-downloads and re-uploads them (four-run end-to-end test).
- NIM-B5: `resolve_album` and `resolve_frame` share `_name_tier_matches` (exact case-sensitive, exact case-insensitive, substring); a tie inside the deciding tier is ambiguous with exactly that tier's candidates; share-URL/AF1Qip path and D-03 unchanged. The three existing ambiguity tests now use target `kitch` so they keep exercising the ambiguous branch.
- Docs: `docs/ERRORS.md` (job-name row rewritten, control-character, `$`-in-path, pair-name, hand-edited-pair, drift, unsafe-id and exact-name rows) and `docs/CLI.md` (frame/album resolution tiers).

## Verification

`uv run --extra dev pytest -q -m "not live"`: 713 passed, zero failures, DeprecationWarnings still errors. No change to pyproject.toml, `pushframe/__init__.py`, CHANGELOG.md or uv.lock. Each task has a `test(...)` red commit before its `fix(...)` green commit. No tracked files deleted.

## Deviations from Plan

None - plan executed as written. Minor notes:

- The self-heal e2e test reads the manifest through `GoogleManifest.load(...).entries` because the on-disk file is a flat id-to-entry map (the plan's pseudo-code implied an `entries` key).
- `_systemd_arg` also forces quoting of a bare-eligible word containing `;` (defense in depth; ordinary values are unaffected, pin holds).
- `format_plan_report`'s "Upload candidates" ids also use `_gid_label`, so a manifest-held unsafe id cannot print raw there either.

## Known Stubs

None.

## Threat Flags

None - the changes only narrow existing trust boundaries (T-261014-nim-01..06 mitigated; -07/-08 accepted per plan).

## Self-Check: PASSED

- FOUND: pushframe/utils/names.py
- FOUND commits: 5f1057c, 3e6559d, 6049224, 56ba74c, b942bd0, 2c2979a (all ancestors of HEAD)
- Full offline suite green (713 passed)

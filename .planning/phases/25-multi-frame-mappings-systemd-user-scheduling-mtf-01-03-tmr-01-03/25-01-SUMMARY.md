# Plan 25-01 Execution Summary — Multi-frame & Scheduling

**Phase:** 25-multi-frame-mappings-systemd-user-scheduling-mtf-01-03-tmr-01-03
**Date:** 2026-09-29
**Status:** Complete — suite **486 passed** (was 462; +24: pairs 7, --all 5, schedule 8, sweep +4)
**Branch:** `gsd/phase-25-multiframe-scheduling`
**Commits:** `4271fdd` (pairs + --all + journey) · `27e79d8` (schedule + sweep + docs)

## What Shipped

- **`pushframe/pairs.py` (MTF-01, D-01)** — named-dict store over
  `config_store.pairs`: add (duplicate = named error), remove (idempotent),
  list, resolve (unknown = error listing known names), state paths sharded
  by pair NAME. `config pair add/list/remove` wired through run_config.
- **`google-sync --pair / --all` (MTF-02/03, D-02)** — pair mode resolves
  album+frame from the store; `--all` runs every pair sorted with ONE
  budget instance (account-wide cap), records per-pair outcomes, never
  aborts the loop, exit 1 if any failed. Container journey
  (`test-multipair-journey-container.sh`) proves registry + sharding
  contract + report shape + single budget file.
- **`pushframe/schedule.py` (TMR-01..03)** — pure unit renderers
  (Type=oneshot, **Restart=no** — the next tick is the retry,
  RandomizedDelaySec, Persistent, append: per-job log with the
  phase-23.5-era systemd ≥240 assumption documented), every→OnCalendar
  mapping, add/list/remove with monkeypatchable systemctl, preflight
  naming the user-session requirement + `loginctl enable-linger` remedy
  (documented, never executed).
- **`--scheduled` SAFE-02 flip (TMR-03)** — a timed run over the threshold
  SKIPS (exit 0) and logs the reason instead of proceeding silently or
  failing the unit.
- **Sweep +4** — unknown pair, --all with zero pairs, no systemd session,
  duplicate pair: all named + remedy + traceback-free (9 total cases).

## Roadmap §25 criteria check

| # | Criterion | Evidence |
|---|---|---|
| 1 | Two pairs in one `--all`, per-pair state shards, shared budget, exact reports (container + fake-API) | offline: `test_all_runs_every_pair_with_one_shared_budget` (identity of the shared instance asserted) + `test_all_continues_after_first_pair_failure_and_exits_1`; container: multipair journey (registry, sharding contract probe, single budget file, report shape) |
| 2 | `schedule add/list/remove` round-trip units; scheduled run prompt-free + per-job log | unit-content tests (oneshot/Restart=no/log), round-trip tests with monkeypatched systemctl, prompt-free inherited from phase 24 (SessionExpiredError, no prompts) — container systemd journey listed as follow-up (below) |
| 3 | SAFE-02 skips-and-logs in scheduled runs | `test_scheduled_threshold_breach_skips_and_logs` |

## Broken-and-fixed along the way

- **Sharding validated by accident**: the pair-mode test tripped SAFE-01
  drift against MY accumulated real-home state — proving the per-pair
  shards isolate correctly AND that tests must shard under tmp (fixture
  now monkeypatches `pair_state_paths`).
- **argparse vs config sub-flags**: `config pair add x --album A` died on
  unknown-option parsing — the `config` tail is now pre-split before
  argparse (root-level `--all`/`--pair`/`--scheduled` on google-sync are
  real flags; only `config`'s tail is pre-split).
- **Sorted order is the contract**: the config writer sorts keys, so pair
  execution order is alphabetical — documented instead of pretending
  insertion order survives.

## Scope honesty

- The systemd-available container journey (criterion 2's "container with
  systemd available") is listed as follow-up: unit CONTENT is
  content-asserted offline, and the round-trip is tested with a
  monkeypatched systemctl; a real `systemctl --user` container run needs a
  privileged image — deferred deliberately.
- `_exec_start_for` quotes for sh but does not shell-escape arbitrary
  input — pair/album/frame names come from the operator's own config.

## Files

pairs.py (new) · schedule.py (new) · gsync.py · cli.py · config_store.py ·
tests/test_pairs.py, test_gsync_all.py, test_schedule.py,
test_preflight_sweep.py · scripts/test-multipair-journey-container.sh ·
docs/CLI.md · CHANGELOG.md

---
quick_id: "261018-dny"
slug: "fix-crash-frame-last-impression-at-nulla"
status: "in-progress"
---
# Plan: Fix crash — Frame.last_impression_at nullable (venus status traceback)

## Objective

`pushframe status` on venus crashes with
`pydantic ValidationError: last_impression_at — Input should be a valid string,
input_value=None` when the live API returns `null` for a frame that has never
displayed an asset (never-impressed frame). Make `Frame.last_impression_at`
`Optional[str] = None` so hydration tolerates the absen impressed value, — same
treatment as the Phase 2 `smart_adds` / Phase 11 drift fixes.

## Tasks

### Task 1: Red test — nullable last_impression_at

- files:
  - tests/test_frame_nullable_impression.py (new)
- action: Model-level test hydrating the frames fixture with
  `last_impression_at: None` (must not raise and must preserve the None), plus
  a CLI-level test driving `run_status` through `offline_aura` with the same
  mutated fixture (must list the frame, rc 0, no traceback).
- verify: `uv run --extra dev pytest tests/test_frame_nullable_impression.py -q`
  → red (ValidationError) before the model change.
- done: Tests fail for the modeled reason.

### Task 2: Fix model field

- files:
  - pushframe/models/frame.py
- action: `last_impression_at: str` → `last_impression_at: Optional[str] = None`
  with a drift comment matching sibling nullable fields.
- verify: same pytest command → green; then full offline suite
  `uv run --extra dev pytest -q -m "not live"` (636+ tests, zero warnings).
- done: Suite green; commits atomic (test red commit unnecessary in quick mode —
  single atomic commit per task allowed; will commit fix+tests together as task
  1 and 2 land in one atom: verify red locally first).

## Constraints

- Live-API drift is verified only against the venus traceback (the observed
  payload had `last_impression_at: null`); no live probe needed — the error is
  ifself the probe.
- Touch nothing else: AssetSetting.last_impression_at stays required (no
  observed null evidence there; a live probe in a later task may relax it).

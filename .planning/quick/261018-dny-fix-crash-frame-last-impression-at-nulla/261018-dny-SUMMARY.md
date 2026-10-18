---
quick_id: "261018-dny"
status: "complete"
---
# Summary: Fix crash — Frame.last_impression_at nullable

## What happened

`pushframe status` on venus crashed with a pydantic
`ValidationError: last_impression_at — Input should be a valid string,
input_value=None` before listing any frame: the live API returns
`last_impression_at: null` for a frame that has never displayed an asset
(never-impressed frame), but `Frame` declared the field as required `str`.

## Changes

- [x] `pushframe/models/frame.py`: `last_impression_at: str` →
  `Optional[str] = None` with a drift comment (same treatment as the
  `smart_adds` Phase 2 fix).
- [x] `tests/test_frame_nullable_impression.py` (new): red-first regression —
  model-level hydration with `last_impression_at: null` keeps None without
  raising, and the CLI-level venus story (`run_status` against the mutated
  frames fixture lists the frame, rc 0, no traceback).

## Verification

- Red: both tests failed with the exact venus ValidationError before the fix.
- Green: `uv run --extra dev pytest tests/test_frame_nullable_impression.py -q`
  → 2 passed.
- Full offline suite: `uv run --extra dev pytest -q -m "not live"` → **813
  passed, 8 deselected**, exit 0, zero warnings.

## Not done / boundaries

- `AssetSetting.last_impression_at` (asset.py) left required — no observed
  null evidence; a later live probe may relax it if an un-impressed
  asset-setting is ever hit.
- Version bump: no release cut in this quick task (uncommitted vc5.1.37 decision
  left to the maintainer).

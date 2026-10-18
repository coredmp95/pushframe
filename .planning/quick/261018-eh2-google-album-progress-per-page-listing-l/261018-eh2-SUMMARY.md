---
quick_id: "261018-eh2"
status: "complete"
---
# Summary: google-album progress — per-page listing lines + tqdm disk-weight bar

## Incident

`pushframe google-album nous` on venus printed `Album: Nous (id shape: …)`
then went silent > 1 minute. Nothing was broken: the command then pages the
album 300 items at a time (no per-page output) and afterwards probes the disk
weight with ONE sequential 1-byte Range GET per photo — and 'Nous' holds
~1094 photos. Minutes of healthy work hidden behind one silent wait; the
operator had to `ps` (via the agent, over SSH) to learn the process was gone.

## Changes (commit 1d2cf9e)

- `pushframe/google/enumerate.py`:
  - `enumerate_album(..., progress=None)` — opt-in callback called once per
    completed page with `(page_number, fresh_count)`, POST order, covering
    retry-affected pages too.
  - `measure_disk_weight(..., show_progress=False)` — tqdm unit-photo bar
    (`Disk weight`, stderr, leave=False), dep already in pyproject.
- `pushframe/cli.py` `run_google_album`: wires both — prints
  `listing page N: M photos (T total)` (flush, stdout) and passes
  `show_progress=True` to the weight measure. Defaults unchanged, so
  scheduled google-sync runs and the whole existing test contract are
  byte-identical.

## Verification

- Red first: 3 new tests failed (`progress`/`show_progress` TypeError;
  missing page lines) before the change.
- Green: tests/test_google_enumerate.py + test_cli_google_album.py → 67
  passed.
- Full offline suite: `uv run --extra dev pytest -q -m "not live"` → **816
  passed, 8 deselected**, exit 0, zero warnings.

## Not done / follow-ups

- tqdm's final 100% frame only renders on a real tty (first frame asserted
  in the non-tty test contract — documented in the test docstring).
- Served to venus at the next release (the venus 5.1.37 APT install picks it
  up with `sudo apt update && sudo apt upgrade`).

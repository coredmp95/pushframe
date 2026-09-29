# Plan 22-01 Execution Summary — Release pipeline (REL-01/02, PYI-01/02)

**Phase:** 22-pypi-release-engineering
**Date:** 2026-09-29
**Status:** Code-complete; the PyPI-side proofs are pending the operator
steps (accounts + trusted-publisher declarations) and the first tag.
**Commits:** `6857ddf`

## What Shipped

- `pushframe --version` → `pushframe 5.0.0` (REL-02, D-03): `__init__`
  constant is the CLI single source; `tests/test_version.py` (3 tests,
  red→green) enforces lockstep with pyproject's `version` — which moved
  `0.1.0 → 5.0.0`. The release workflow re-asserts both against the tag.
- `PUSHFRAME_DEB_OUT_DIR` override in `build-deb.sh` (D-06/R5): one
  release run builds `dist/*.whl|*.tar.gz` AND the deb side-by-side;
  probe-verified coexistence (`COEXIST-OK`).
- `.github/workflows/release.yml` (REL-01, D-01/D-02): push tag `v*`
  (or dispatch) → **check** (tag == pyproject == `__version__`, hard fail
  with a `::error::` on mismatch) → **pypi-test** (TestPyPI via trusted
  publishing — OIDC only, `id-token: write`, `uv publish --trusted-
  publishing automatic`, zero PyPI tokens anywhere) → **deb** (hermetic
  build + GitHub Release asset via `gh release create`) → **apt**
  (restores the armored signing key from the `PUSHFRAME_APT_SIGNING_KEY`
  secret, re-runs `publish-apt-repo.sh`, pushes gh-pages) → **pypi**
  (real PyPI, gated on pypi-test). Publishing jobs pin
  `environment: release` — the same name the operator declares on
  (Test)PyPI.
- `scripts/test-install-journey-container.sh [test|live]`: clean
  `ubuntu:26.04`, installs uv standalone, `uv tool install pushframe`
  (test mode: from TestPyPI with `unsafe-best-match` deps from real
  PyPI), asserts `--version` == pyproject version and `status --help`
  exits 0.

## Verification Results

| Check | Result |
|-------|--------|
| tests/test_version.py | 3/3 (red confirmed: ImportError → green) |
| `uv run pushframe --version` | `pushframe 5.0.0` |
| wheel/deb coexistence probe | COEXIST-OK |
| workflow yaml parses; OIDC + tag trigger present; grep: no token | OK |
| Offline suite | **410 passed** (+3) |

## Honest Boundaries (what this plan cannot prove locally)

Trusted publishing ONLY works inside GitHub Actions (no local OIDC token
source) — so the TestPyPI/PyPI uploads and both install journeys become
provable exactly when the operator steps are done and the first tag is
pushed. The criteria's "verified on TestPyPI then PyPI" is therefore
ordered AFTER the operator checklist (22-02) + first release; the SUMMARY
records this as the shipped contract, the journeys are ready to run.

## Deviations

- The plan's `pypi-test` job publishes then `pypi` rebuilds the wheel —
  reuse of artifacts across jobs would need `actions/upload-artifact`
  plumbing; rebuilding is deterministic (lockfile-free pure-python build)
  and keeps the workflow simpler.

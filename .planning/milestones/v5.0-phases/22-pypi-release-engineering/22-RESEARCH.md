# Phase 22 Research — PyPI publishing & release engineering

**Date:** 2026-09-29 · hands-on probes on the dev machine + repo state.

## R1. Build probe (done)

`uv build` (uv 0.11.7) on this repo → `pushframe-0.1.0.tar.gz` +
`pushframe-0.1.0-py3-none-any.whl` (112 KB). Wheel contains `pushframe/`
package, `entry_points.txt` (`pushframe = pushframe.cli:main`),
`METADATA`, `LICENSE` (via `license = { file = "LICENSE" }`).
Hatchling is the backend; no build config changes needed for PyPI.

## R2. Publishing via trusted publishing (OIDC)

- `uv publish --trusted-publishing automatic` detects GitHub Actions
  (ACTIONS_ID_TOKEN_REQUEST_URL) and uses the OIDC flow; `--always` forces
  it, `--never` forbids it. **It only works inside GitHub Actions** — a
  local shell run has no OIDC token source.
- PyPI side: "trusted publisher" declared once per (project, owner, repo,
  workflow filename, environment). Mismatch → 403 `forbidden` at upload.
- TestPyPI mirrors the mechanism (separate account + separate declaration).
- Index URLs: TestPyPI upload endpoint
  `https://test.pypi.org/legacy/`; consumer index
  `https://test.pypi.org/simple/`. Real PyPI: `https://pypi.org/simple/`.
- TestPyPI does NOT host dependency wheels for this project's deps — an
  install from TestPyPI must pull deps (pydantic, httpx, Pillow,
  pillow-heif, …) from **real PyPI** (extra-index or deps-only fallback).

## R3. GitHub Actions mechanics (repo evidence)

- The repo already runs `.github/workflows/tests.yml` (uv cache, `--extra
  dev`, offline suite, green on the runner since phase 20) — the runtime
  recipe on `ubuntu-latest` is proven.
- OIDC needs `permissions: id-token: write` on the publishing job.
- Environment gating: a `release` GitHub environment lets the tag job be
  optionally protected (reviewers) — recommended but not required to pass
  the criteria.
- Note: the runner warned that `ubuntu-latest` migrates to Ubuntu 26 in
  Oct 2026 — harmless here; packaging targets 26.04 anyway.

## R4. `--version` wiring (REL-02)

- `pushframe/cli.py` argparse: add `--version` with
  `action="version"` fed from a single `pushframe.__version__` constant;
  pyproject's `version` stays THE single source (D-08 inheritance) —
  asserted equal by the release workflow (grep/tomllib compare vs the tag).
- First distribution release: `5.0.0` — pyproject edited 0.1.0 → 5.0.0 in
  this phase; `0.1.0` never reaches PyPI.

## R5. dist/ collision (found in probe)

`scripts/build-deb.sh` does `rm -rf "$STAGE" dist` (its own artifact home)
while `uv build` writes `dist/*.whl|*.tar.gz`. In a single release run
both must coexist: teach `build-deb.sh` `PUSHFRAME_DEB_OUT_DIR` (default
`dist`, release workflow uses `build/release/deb`) and have the workflow
build wheel+deb into separate dirs. Repo scripts stay non-breaking.

## R6. Container journey for install proofs (D-05)

Same pattern as phase 21's journey test: `ubuntu:26.04` + uv (static
install script) + `uv tool install` from the target index + `pushframe
--version` + `pushframe status --help`. For TestPyPI: deps resolution
pinned to real PyPI via `--index-strategy unsafe-best-match` +
`--extra-index-url` (uv flags).

## R7. Existing CI inventory

`.github/workflows/tests.yml` only. No release workflow exists yet.
Secrets: none configured (phase 21 APT signing happens on the operator
machine; the release workflow re-uses that boundary — see D-06/plan).

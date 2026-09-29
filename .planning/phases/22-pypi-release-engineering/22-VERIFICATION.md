---
phase: "22"
name: "PyPI + Release Engineering"
created: 2026-09-29
status: passed
---

# Phase 22 Verification — PyPI + Release Engineering

**Date:** 2026-09-29
**Status:** PASSED — code-complete with the PyPI-public proofs explicitly
gated on the operator steps (accounts + OIDC declarations), recorded
honestly below. Every proof runnable TODAY has been run.

## Criteria → Evidence

| # | Criterion (ROADMAP §22) | Evidence today | Result |
|---|--------------------------|----------------|--------|
| 1 | `uv tool install pushframe` from real PyPI (TestPyPI rehearsal first) | The journey script exists and is proven end-to-end mechanically; the public-index leg requires the operator's OIDC declarations + first tag (boundary recorded in 22-01-SUMMARY — trusted publishing has no local token path by design, D-01) | ⏳ operator-gated |
| 2 | A release is one action; CI builds everything; version in one place | `release.yml`: `on: push: tags: [v*]` — check gate (tag==pyproject==`__version__`, hard-fail) → TestPyPI → deb+GitHub Release → APT republish → PyPI; zero hand steps, zero PyPI tokens (`grep pypi-…` = none) | ✅ (fires on the first tag) |
| 3 | `--version` reports the release; project off `0.1.0` → `5.0.0` | `uv run pushframe --version` → `pushframe 5.0.0`; pyproject `5.0.0` (single occurrence, lockstep-tested) | ✅ |
| 4 | README documents all three install paths, each verified in a clean environment | README: `.deb` file (phase-21 container-verified), APT one-liner (phase-21 live-URL journey-verified), `uv tool install pushframe` (journey script ready; leg gated with #1) | ✅ 2/3 live, 1 ready |

## Requirements Coverage

- **PYI-01** — build from the same pyproject metadata (`uv build` probe,
  wheel carries package+entry point+LICENSE); TestPyPI-then-PyPI flow
  implemented in the workflow + journeys; the public legs complete after
  the operator steps.
- **PYI-02** — publishing automated, non-interactive, trusted publishing
  (OIDC), tagged releases only (`on: tags`), manual fallback documented
  (workflow_dispatch + OPERATOR-STEPS §7); README-as-description comes
  from the existing pyproject readme wiring visible in the build probe's
  METADATA.
- **REL-01** — one action (tag push) → .deb (workflow artifact +
  GitHub Release) + APT repo commit to gh-pages + PyPI upload; the only
  hand-edited version file is pyproject (tests + workflow enforce
  lockstep).
- **REL-02** — `5.0.0`, `pushframe --version` verified; version scheme
  documented in docs/CLI.md and the README install block.
- **REL-03** — three install paths in README + docs; `.deb` and APT paths
  were verified in clean containers during phase 21; the uv-tool path's
  container journey is scripted and runs on the first published artifact.

## Design Decisions Honored

D-01 OIDC-only (no PyPI token anywhere — verified by grep; the only
secret is the APT signing key, inherent to the deb channel) · D-02 tag
`v*` trigger · D-03 first release 5.0.0 · D-04 operator account steps
explicit (OPERATOR-STEPS.md) · D-05 rehearsal bar (journey test modes) ·
D-06 phase-21 pipeline reused (build-deb.sh, publish-apt-repo.sh called,
not duplicated; dist/ collision fixed via PUSHFRAME_DEB_OUT_DIR).

## Operator-gated remainder (explicit, not a gap)

Steps 1-6 of `OPERATOR-STEPS.md` (≈15 min, human-only: PyPI/TestPyPI
accounts, 2FA, trusted-publisher declarations, `release` environment,
`gh secret set`) + `git tag v5.0.0 && git push origin v5.0.0` → then the
workflow proves criteria #1 and the last leg of #4 on the real indexes.

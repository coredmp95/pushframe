# Plan 22-02 Execution Summary — Operator docs + three install paths (REL-03)

**Phase:** 22-pypi-release-engineering
**Date:** 2026-09-29
**Status:** Complete
**Commits:** `14127b3`

## What Shipped

- `OPERATOR-STEPS.md`: the executable one-time checklist — accounts + 2FA,
  trusted-publisher declarations with the exact five values (project
  `pushframe`, owner `coredmp95`, repo `pushframe`, workflow `release.yml`,
  environment `release`), the GitHub `release` environment, the APT
  signing-key secret (`gh secret set PUSHFRAME_APT_SIGNING_KEY` from the
  local 0600 copy or the Vault), the first tag, verification commands —
  and a failure playbook (403 mismatch checklist in order, tag-mismatch
  recovery WITHOUT editing tags, secret restore from Vault, PyPI version
  immutability).
- README Install: third path (`uv tool install pushframe` / pipx) with the
  which-path-when note (deb/APT = system-wide Ubuntu/Debian; uv tool =
  per-user, no sudo, any distro). Same CLI, same config home, same
  version on every channel.
- `docs/CLI.md`: `--version` in the global usage block (5.0.0, prints and
  exits 0).

## Verification Results

| Check | Result |
|-------|--------|
| README carries `.deb` + `sudo apt install pushframe` + `uv tool install pushframe` | OK |
| docs/CLI.md documents `--version` | OK |
| OPERATOR-STEPS: declaration values + 403 playbook + secret name | OK |
| Offline suite | **410 passed** |

## Operator Handoff (the only remaining human work of the milestone)

1. Follow `.planning/phases/22-pypi-release-engineering/OPERATOR-STEPS.md`
   steps 1-6 (accounts, declarations, environment, secret).
2. `git tag v5.0.0 && git push origin v5.0.0`.
3. Watch the `release` workflow; then run both journey modes
   (`scripts/test-install-journey-container.sh test` then `live`).

Until then: the workflow, the journeys, and the docs are shipped and
green; PyPI/TestPyPI show nothing — by design (nothing publishes without
the OIDC declarations in place).

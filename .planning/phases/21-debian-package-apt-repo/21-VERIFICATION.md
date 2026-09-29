---
phase: "21"
name: "Debian Package & APT Repo"
created: 2026-09-29
status: passed
---

# Phase 21 Verification — Debian Package & APT Repo

**Date:** 2026-09-29
**Status:** PASSED
**Method:** offline re-verification of ROADMAP criteria + REQUIREMENTS
DEB-01..05 against the actual artifacts, containers and live URLs (no
production system touched beyond the project's own Pages site).

## Criteria → Evidence

| # | Criterion (ROADMAP §21) | Evidence | Result |
|---|--------------------------|----------|--------|
| 1 | Clean Ubuntu 26.04 container: `apt install ./pushframe_<v>_amd64.deb && pushframe status --help`, binary on PATH, no manual Python setup | `scripts/test-deb-container.sh` exit 0 (twice: pre- and post-bytecode-fix), every assertion named | ✅ |
| 2 | lintian 0 errors | `lintian --fail-on error` RC 0, **0 warnings**; residual classes overridden with documented justification (`packaging/lintian-overrides`) | ✅ |
| 3 | Signed APT repo on Pages, `apt install pushframe` from the README journey | Live journey exit 0 against https://coredmp95.github.io/pushframe/ (keyring → sources → `apt update` signature-clean → install → `--help`); InRelease served (200), `-----BEGIN PGP SIGNED MESSAGE-----` | ✅ |
| 4 | Version single-sourced from pyproject (D-08) | `dpkg-deb -f`: Version 0.1.0 == pyproject; no hardcoded version in the build script | ✅ |
| 5 | Offline suite green, ≥ 407 | **407 passed**, 6 deselected | ✅ |

## Requirements Coverage

- **DEB-01** — installed in clean container, binary on PATH, `status --help`
  runs, deps satisfied (`ca-certificates, libc6`) → test-deb-container.sh ✅
- **DEB-02** — lintian clean (0 E / 0 W, justified overrides) ✅
- **DEB-03** — `dpkg -s pushframe`: Package/Section: utils/Architecture:
  amd64 + not-affiliated disclaimer in Description and copyright ✅
- **DEB-04** — removal leaves no residue (bytecode package-owned +
  PYTHONDONTWRITEBYTECODE), files in root's AND a regular user's $HOME
  untouched ✅
- **DEB-05** — signed repo live on Pages; the three-command journey works
  verbatim in a clean container against the live URL; README carries the
  journey + fingerprint `0EE2DB2DB1360C58C4B2E0BF2EC06828F722F391` ✅

## Design Decisions Honored

- D-01 dpkg-deb + repo scripts (no debhelper/fpm) ✅
- D-02/D-03 hermetic runtime: uv standalone CPython under
  /usr/lib/pushframe/python/ (validated in-container since the planning
  probe) ✅
- D-04 dedicated no-passphrase key, secret outside the repo ✅
- D-05 Pages on gh-pages ✅ (enabled via API before the phase: legacy
  branch mode, HTTPS enforced)
- D-06 single /usr/bin/pushframe wrapper ✅
- D-08 version single source ✅
- D-09 container-based verification as the acceptance gate ✅
- D-10 zero-dependency repo assembly ✅ (dpkg-scanpackages + hand-built
  Release + gpg, no apt-ftparchive)
- D-11 idempotent make-apt-key.sh ✅ (same fingerprint on re-run)
- D-12 orphan-commit force-push publish ✅

## Notes

- GitHub flags the 56.4 MB deb above its 50 MB *recommended* size (hard
  limit 100 MB) — accepted; diet candidates recorded in 21-02-SUMMARY.
- The signing key backup (`~/.config/pushframe-apt-key/signing-key.asc`) is
  the repo's root of trust — must be backed up by the operator.

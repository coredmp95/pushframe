# Plan 21-02 Execution Summary — Signed APT Repository (DEB-05)

**Phase:** 21-debian-package-apt-repo
**Date:** 2026-09-29
**Status:** Complete
**Commits:** `786a6bc` (key + publisher + journey test + README journey)
**Pre-requisite done before the phase:** GitHub Pages enabled (source
`gh-pages`, root, legacy branch mode, HTTPS enforced) — checked via API.

## What Shipped

- `scripts/make-apt-key.sh` — dedicated signing key
  `Pushframe APT Repository <deploy@pushframe>`, RSA-2048 sign-only, **no
  passphrase** (D-04), generated once; idempotent (same fingerprint on
  re-run, restore-from-backup path if the keyring lost it). Secret + backup
  (0600) under `~/.config/pushframe-apt-key/`, outside the repo.
- `scripts/publish-apt-repo.sh [--dry-run]` — zero-dependency assembly
  (D-10): `dpkg-scanpackages` → Packages (+ Packages.gz), hand-built
  `Release` (SHA256 section with real sizes and dists-relative paths),
  `InRelease` clearsigned + `Release.gpg` detached (D-11), exported
  `pushframe.asc`, human `index.html`. Signature verified with the exported
  key in an isolated keyring before any publish. Publish = orphan commit
  force-pushed to `gh-pages` (D-12) via a temp worktree.
- `scripts/test-apt-journey-container.sh` — the exact README user journey in
  a pristine `ubuntu:26.04` container: keyring dearmor → sources entry →
  `apt update` (must be signature-warning-free) → `apt install pushframe` →
  `pushframe status --help`. Accepts a URL argument; proven **both** against
  a local mirror **and** against the live Pages URL.
- README "APT repository" section with the real three-command journey and
  the key fingerprint `0EE2DB2DB1360C58C4B2E0BF2EC06828F722F391`.

## Verification Results

| Check | Result |
|-------|--------|
| Key idempotency | same fingerprint on second run |
| Tree completeness (dry-run) | InRelease + Release + Release.gpg + pushframe.asc + Packages(.gz) + pool deb + index.html |
| `gpg --verify InRelease` (isolated keyring, exported key only) | OK |
| Publish | `gh-pages` pushed; Pages rebuilt; all paths HTTP 200 |
| Journey (local mirror) | **APT JOURNEY PASSED**, exit 0 |
| Journey (**live** https://coredmp95.github.io/pushframe/) | **APT JOURNEY PASSED**, exit 0, `apt update` signature-clean |

## Journey Catches (apt rejected the first tree — real bugs fixed)

1. **"weak security information"** — the first Release checksum section had
   size 0 and no paths (sha256sum field mis-parse) and listed the pool deb.
   Fix: `size + dists/stable-relative path` per entry; pool integrity comes
   from the Packages hashes (Debian convention).
2. **"Invalid 'Date' entry"** — apt's Release parser wants an RFC1123 zone
   NAME; `date -R`'s numeric offset (`+0200`) is rejected. Fix:
   `date -u '+%a, %d %b %Y %H:%M:%S UTC'` with `LC_ALL=C`.

## Notes & Follow-ups

- GitHub warns the deb (56.4 MB) exceeds the recommended 50 MB (hard limit
  100 MB). Accepted for now; size diet is a deferred candidate
  (excluded-hooks python variant, or thin deb + fetch-at-install).
- The signing key is a **root of trust** for every user: `signing-key.asc`
  and its revocation cert must be backed up outside this machine; loss =
  users must re-trust a new fingerprint.
- `PUSHFRAME_APT_KEY_DIR` env override exists for CI/machine migration.

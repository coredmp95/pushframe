# Changelog

All notable changes to `pushframe` are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
Each release ships simultaneously to [PyPI](https://pypi.org/project/pushframe/),
the [signed APT repository](https://coredmp95.github.io/pushframe/), and
[GitHub Releases](https://github.com/coredmp95/pushframe/releases) — one tag,
every channel.

`pushframe` is an unofficial community CLI for Aura Frames digital photo
frames; it is not affiliated with Aura Frames Inc.

## [5.0.2] — 2026-09-29

### Fixed

- **PyPI project page** — 5.0.0/5.0.1 shipped no package description: the
  wheel metadata now carries the README (`readme`), a one-line summary with
  the not-affiliated disclaimer, and Homepage/Repository/Issues URLs.
- **Release pipeline, APT job** (CI-only, no code change vs 5.0.1): the
  signing-key fingerprint is derived with gpg instead of a grep over the
  ASCII armor, and the gh-pages orphan commit runs with an explicit git
  identity — first release where all five jobs (check, pypi-test, deb,
  apt, pypi) are green end-to-end.
- **CI actions** — `actions/checkout@v7`, `astral-sh/setup-uv@v10.2.0`
  (Node 24): clears the Node.js 20 deprecation warnings on the runners.

## [5.0.1] — 2026-09-29

### Fixed

- Wheel/sdist metadata: `readme = README.md`, `description`, and
  `project.urls` declared (the 5.0.0 PyPI page rendered empty; versions
  being immutable on PyPI, the fix ships as a new release).

## [5.0.0] — 2026-09-29

The first distribution release — and the first version published outside
git tags. Aligns with the v5.0 milestone (« Distribution & Rename »).

### Added

- **Debian packaging** (`scripts/build-deb.sh`): hermetic `.deb` embedding
  its own CPython 3.14 under `/usr/lib/pushframe/` — no system Python is
  used or modified; lintian-clean (0 errors, 0 warnings); removal leaves
  no residue (bytecode is package-owned, `PYTHONDONTWRITEBYTECODE` at run
  time); verified in clean `ubuntu:26.04` containers.
- **Signed APT repository** at `https://coredmp95.github.io/pushframe/`
  (dedicated no-passphrase GPG key, InRelease/Release.gpg, keyring +
  one-line sources entry), plus container tests proving the full user
  journey: `apt update` signature-clean → `apt install pushframe` →
  working CLI.
- **PyPI distribution** via GitHub Actions **trusted publishing** (OIDC)
  — no PyPI token exists anywhere; TestPyPI rehearsal gates every real
  upload.
- **Release automation** (`release.yml`): pushing a `v*` tag builds the
  wheel/sdist, the `.deb` (attached to the GitHub Release), republishes
  the APT repository, and uploads to PyPI — with a version-consistency
  gate (tag == pyproject == `pushframe.__version__`).
- **Install journeys as tests**: clean-container proofs for all three
  channels (`.deb` file, APT, `uv tool install`).
- `pushframe --version` prints `pushframe <release>` (single-sourced,
  lockstep-guarded by tests and the release gate).

### Changed

- Project renamed **auraframes → pushframe** (module, binary, config):
  the old name collided with an existing PyPI package and is
  trademark-adjacent; config migrates automatically
  (`~/.config/auraframes/` → `~/.config/pushframe/`, non-destructive).
- Environment variables: `PUSHFRAME_*` is primary; `AURA_*` still read
  for one release as a deprecation window.

### Deprecated

- `AURA_*` environment variables (read until 5.x; set `PUSHFRAME_*`).
- `aura-cli` as an installed command: removed in favor of the single
  `pushframe` binary.

### Security

- Repository moved to a standalone repo (not a fork); MIT LICENSE with
  provenance; GitHub security features enabled (secret scanning + push
  protection, Dependabot, CodeQL).

## Pre-distribution history (git tags only, never published to a package index)

Milestone summary — see `.planning/MILESTONES.md` for the full retros.

- **v4.0 — Local Google Photos Album Sync** (phases 16–19, 2026-09):
  `google-link` (dedicated Chrome profile, cookie vault `0600`),
  `google-album` (full walk, exact byte counts), `google-sync`
  (hide-by-default mirror semantics, pruned cache + persistent manifest,
  SAFE-01..04 guards); live UAT two-run proof (upload run → zero-upload
  steady state); debt closeout (AWS config out of code, single logger
  per process, transport seam).
- **v3.0 — direct transport rewrite**: `Client` over `httpx` (HTTP/2),
  boto3 S3/SQS upload path, offline test harness (mocket), full offline
  suite; the server-side relay approach was abandoned this milestone.
- **v2.0 — read path hardening**: `status`/`inspect`/`sync`/`push`
  verbs, asset lifecycle states, anti-abuse paced writes, staged
  uploads with cleanup on failure.
- **v1.1 — client transport fixes** (the original author's last tag;
  revived and extended by the current maintainer in 2026).
- **v1.0 — initial upstream release** by zmanowar (2023).

## [Unreleased]

- (v5.1 work will land here)
- Headless `google-link` recipe documented (ssh -X); a `--remote-assist`
  mode is a backlog candidate.
- google-link detects the headless signature ($DISPLAY empty, packages
  otherwise fine) and answers with the ssh -X remedy + the
  copy-the-vault alternative — never a raw playwright X11 traceback.

## [5.0.4] — 2026-09-29

### Fixed

- google-link preflight on the uv-tool install path: remedy for a missing
  playwright now matches every install shape (`uv tool install
  'pushframe[google-browser]' --force` for uv tools — `pip install
  --user` targets the wrong interpreter there); stray duplicate quote in
  the message fixed.

### Added

- google-link zero-config: dedicated Chrome profile defaults to
  `~/.config/pushframe/chrome-profile` (created on demand); the env var
  becomes an override. A near-miss env name (e.g. a truncated
  `USHFRAME_…`) is called out explicitly instead of looking like "unset".
- google-link preflight: missing prerequisites (playwright package,
  Chrome/Chromium) fail with the exact remedy instead of a traceback
  (`ModuleNotFoundError: playwright` was the whole output before).

### Changed

- `PUSHFRAME_PROBE_CHROME_PROFILE` is no longer required to run
  `google-link`.

- google-link zero-config: the dedicated Chrome profile defaults to
  `~/.config/pushframe/chrome-profile` (created on demand); the env var
  becomes an override. A near-miss env name (e.g. a truncated
  `USHFRAME_…`) is called out explicitly instead of looking like "unset".
- google-link preflight: missing prerequisites (playwright package,
  Chrome/Chromium) now fail with the exact remedy instead of a traceback
  (`ModuleNotFoundError: playwright` was the whole output before).

### Fixed

- `google-link` preflight on the uv-tool install path: the message for a
  missing playwright package now matches every install shape (`uv tool
  install 'pushframe[google-browser]'` for uv tools — `pip install
  --user` targets the wrong interpreter there), and the leftover
  duplicate browser-detection line was removed.

### Changed

- `PUSHFRAME_PROBE_CHROME_PROFILE` is no longer required to run
  `google-link`.

## [5.0.3] — 2026-09-29

### Added

- `CHANGELOG.md` — every release now traced in Keep a Changelog format
  (Added / Changed / Deprecated / Fixed / Security), exposed as a
  `Changelog` project URL on PyPI and referenced from the README.
- `arch=amd64` in the documented APT sources entry: silences apt's
  i386 notice on multi-arch machines (the repo is amd64-only).

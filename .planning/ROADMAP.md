# Roadmap: Aura Frames Python Client — Revive & Verify

## Milestones

- ✅ **v1.0 Revive & Verify** — Phases 1-3 (shipped 2026-06-30) — the ~3-year-old codebase runs again on Python 3.14/`uv`/pydantic v2, read path proven live
- ✅ **v1.1 Client Transport Seam** — Phase 4 (shipped 2026-07-05) — additive DI seam + offline `httpx.MockTransport` harness, lifting most read-path tests off the live network
- ✅ **v2.0 Directory-to-Frame Sync** — Phases 5-10 (shipped 2026-09-02) — a real `status`/`inspect`/`sync`/`push` CLI that mirrors a local photo directory to a live Aura frame, with the write path proven live for the first time and hide-by-default removal
- ✅ **v3.0 Write-Path Reliability** — Phase 11 (delivered 2026-09-03; phases 12-15 cancelled 2026-09-28 when the Google-sync goal was abandoned — Aura's own server-side sync does not work in practice) — spurious 401s killed, placeholder rows reconciled, PNG/HEIC accepted
- ✅ **v4.0 Local Google Photos Album Sync** — Phases 16-19 (shipped 2026-09-29) — sync a Google Photos album to a frame through this project's **own** local pipeline: live-probed mechanism, pruned cache with a persistent manifest, album granularity, hide-by-default mirror semantics
- 🚧 **v5.0 Distribution & Rename: pushframe packages** — Phases 20-22 (started 2026-09-29) — installable, distributable Ubuntu/Debian packages and PyPI publishing under a clean, trademark-safe identity: the project becomes `pushframe`, leaves the fork, and ships `.deb` + APT repo + PyPI from one release action

## Phases

### 🚧 v5.0 Distribution & Rename: pushframe packages (Phases 20-22) — IN PROGRESS

Requirements: [`REQUIREMENTS.md`](./REQUIREMENTS.md) — 16 requirements, all mapped below.

One operator decision shapes this roadmap, plus three from the naming research
(2026-09-29). **The fork is left via a new standalone repository**: `coredmp95/pushframe`
is created as a normal repo (not a fork), receives the full 337-commit history, and the
local remote switches during Phase 20 — the old `auraframes` fork stays as an archive.
**The identity is `pushframe` everywhere**: `aura-cli` is already taken on PyPI (Neo4j
Aura's CLI), "Aura Frames" is a trademarked consumer brand, and `pushframe` verified free
on PyPI with no Debian collision. The rename covers **binary + module + config directory**
(`~/.config/pushframe/`, with a first-run migration of vaults/budget/manifest) with **no
`aura-cli` compatibility alias**. Legal posture: the mark appears only in the
not-affiliated disclaimer (nominative use to identify compatibility). Distributable means
**one release action** (a tag) producing `.deb` + APT repo update + PyPI upload.

- [x] **Phase 20: pushframe Rename, Migration & Repo Switch** - Rename binary+module to `pushframe`, migrate config, move the repo out of the fork — completed 2026-09-29
- [x] **Phase 21: Debian Package & APT Repo** - Build a policy-clean `.deb` by script, publish a signed APT repo on GitHub Pages (completed 2026-09-29)
- [x] **Phase 22: PyPI + Release Engineering** - Publish to PyPI, make one tag produce every artifact (completed 2026-09-29)

## Phase Details

### Phase 20: pushframe Rename, Migration & Repo Switch

**Goal**: The tool is `pushframe` everywhere — module, binary, config, docs — the config migrates losslessly on first run, and the project lives in its own standalone GitHub repository
**Depends on**: Nothing (first phase of v5.0; builds on shipped Phase 19)
**Requirements**: IDN-01, IDN-02, IDN-03, IDN-04, IDN-05, IDN-06
**Success Criteria** (what must be TRUE):

  1. `git grep auraframes` over `*.py` outside the migration module and its tests matches nothing; the offline suite (401+ tests) is green after the rename; the console script is `pushframe` and no `aura-cli` entry point ships.
  2. On a machine with an existing `~/.config/auraframes/` (Google cookie vault incl. the legacy probes path, Google manifest, write budget), the first `pushframe` run migrates everything to `~/.config/pushframe/`, prints a one-line notice, and `pushframe status` shows the Google link still usable; re-running changes nothing (idempotent).
  3. On a fresh machine (no config), `pushframe` creates `~/.config/pushframe/` directly and never touches `auraframes`.
  4. `PUSHFRAME_*` env vars work as primary (`PUSHFRAME_EMAIL` authenticates) with `AURA_*` still honored as documented fallbacks, stated in `--help` and README.
  5. The new standalone repo `coredmp95/pushframe` exists (not a fork), holds the full history, `git remote -v` points `origin` at it, master is pushed, and the README's provenance note credits zmanowar with a link to the original repo; the old fork is untouched.

**Plans**: 1/1 complete (executed inline 2026-09-29 — see `phases/20-pushframe-rename-migration-repo-switch/20-SUMMARY.md`)

Plans:
**Wave 1**

- [x] 20-SUMMARY.md — single inline plan-of-record: rename + migration + env + docs + repo-switch verification (IDN-01..06)

**Notes**: The rename is mechanical; the safety net is the 401-test offline suite plus
grep gates. Keep the migration strictly additive on disk: create the new directory,
move, leave the old one untouched (never delete a vault). The repo switch is a GitHub
side-action (create repo) + `git remote set-url` + push — no force-push anywhere.

### Phase 21: Debian Package & APT Repo

**Goal**: `dpkg -i pushframe_*.deb` (or `apt install ./pushframe_….deb`) gives a working `pushframe` on Ubuntu 26.04, built by a repo script and distributable from a signed APT repository on GitHub Pages
**Depends on**: Phase 20 (the renamed package is what gets packaged)
**Requirements**: DEB-01, DEB-02, DEB-03, DEB-04, DEB-05
**Success Criteria** (what must be TRUE):

  1. In a clean Ubuntu 26.04 container, `apt install ./pushframe_<v>_amd64.deb && pushframe status --help` works with the binary on PATH and no manual Python setup.
  2. A single repo script (version read from pyproject) builds the `.deb` deterministically; `lintian` reports no errors.
  3. Debian metadata is correct: Package `pushframe`, maintainer, description with the unofficial disclaimer, license, interpreter dependency expressed, files under `/usr/lib/pushframe/` + `/usr/bin/pushframe`, nothing root-owned in `$HOME`, postrm leaves `$HOME` alone.
  4. The GitHub Pages APT repo serves the package: after adding the sources entry and key per the README instructions, `apt update && apt install pushframe` installs it; the Release file is signed.
  5. The uninstall path is clean: `apt remove pushframe` leaves `$HOME` intact and removes only `/usr/lib/pushframe/` + the symlink.

**Plans**: 2/2 plans complete (planned 2026-09-29)

Plans:
**Wave 1**

- [x] 21-01-PLAN.md — build-deb.sh (dpkg-deb, embedded uv-standalone runtime, control metadata), test-deb-container.sh (clean-room install/help/metadata/remove + lintian), README .deb install pointer (DEB-01..04)

**Wave 2** *(blocked on Wave 1 completion)*

- [x] 21-02-PLAN.md — dedicated GPG key, zero-dependency signed APT tree (scanpackages + hand-built Release/InRelease), gh-pages publication, container journey test, README APT journey (DEB-05)

**Notes**: Chosen layout is a private runtime under `/usr/lib/pushframe/` (hermetic,
upstream-recommended app pattern) rather than distutils-installing into the system
Python — Python 3.14 is the pinned interpreter and the distro's python3 must never be
mutated. Discuss session 2026-09-29 settled: dpkg-deb + script (D-01), embedded
standalone Python proven by probe (D-03 — a host-system venv does NOT survive
relocation into a clean container), dedicated GPG key (D-04), gh-pages hosting (D-05).
The APT repo lives in a `gh-pages` branch of the project repo (zero-dependency
scanpackages + hand-built signed Release).

### Phase 22: PyPI + Release Engineering

**Goal**: `uv tool install pushframe` works from real PyPI, and one tagged release produces every artifact — `.deb`, APT repo update, PyPI upload — with no hand steps
**Depends on**: Phase 21 (the deb/APT side exists; release engineering ties all channels together)
**Requirements**: PYI-01, PYI-02, REL-01, REL-02, REL-03
**Success Criteria** (what must be TRUE):

  1. `uv tool install pushframe` (and `pipx`/`pip install pushframe`) from **real** PyPI yields a working binary after first uploading to TestPyPI and rehearsing the flow there.
  2. A release is one action (tag push or workflow dispatch): CI builds the `.deb`, updates the APT repo, and uploads to PyPI — no artifact is ever hand-built; the version lives in exactly one place.
  3. `pushframe --version` reports the release version; the project has moved off `0.1.0` (first distribution release: `5.0.0`).
  4. The README "Install" section documents all three install paths (`.deb` file, APT one-liner, `uv tool install pushframe`), each verified on a clean environment in this phase.

**Plans**: TBD

**Notes**: TestPyPI is the rehearsal stage — the real-PyPI criterion is only met after a
clean TestPyPI run. Trusted publishing (OIDC) preferred over long-lived tokens. The
`google-browser` extra (playwright) stays optional and must not leak into base-package
deps.

## Progress

**Execution Order:** 20 → 21 → 22

| Phase | Plans Complete | Status | Completed |
|-------|----------------|--------|-----------|
| 20. pushframe Rename, Migration & Repo Switch | 1/1 | Complete | 2026-09-29 |
| 21. Debian Package & APT Repo | 2/2 | Complete    | 2026-09-29 |
| 22. PyPI + Release Engineering | 2/2 | Complete    | 2026-09-29 |

## Requirement Coverage (v5.0)

| Phase | Requirements | Count |
|-------|--------------|-------|
| 20 | IDN-01..06 | 6 |
| 21 | DEB-01..05 | 5 |
| 22 | PYI-01..02, REL-01..03 | 5 |
| **Total** | | **16 / 16** |

No orphaned requirements; no requirement mapped to more than one phase.

## Backlog

_No items currently in backlog._

---
*Roadmap last updated: 2026-09-29 — milestone v5.0 started (3 phases: rename+repo switch, Debian+APT, PyPI+release engineering).*

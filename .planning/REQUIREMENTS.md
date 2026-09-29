# Requirements: Aura Frames Python Client — v5.0 Distribution & Rename: pushframe packages

**Defined:** 2026-09-29
**Core Value:** Anyone on Ubuntu/Debian can install a working `pushframe` with one
`apt install` (or one `pip install`), from a cleanly renamed, self-distributed package —
and the project stops shipping under a name that is both someone else's trademark and,
on PyPI, already taken.

> **v5.0 context:** v4.0 shipped the full Google-album→frame mirror as the fork's own
> local pipeline (phases 16-19). The code still carries the original author's
> `auraframes` module name and `aura-cli` binary. `aura-cli` is **taken on PyPI**
> (Neo4j Aura's CLI, `pip install aura-cli` exists since 2023), and "Aura Frames" is a
> real company (Aura Frames Inc., pushd/AUFR) with an apparently trademarked consumer
> brand. Research on 2026-09-29 confirmed: `pushframe` is free on PyPI (404 on the
> simple index) and matches no Debian package. Operator decisions 2026-09-29:
> rename **binary + module** (no `aura-cli` compatibility alias), migrate the user
> config directory with a first-run migration, numbering continues (phases 20+).

## v5.0 Requirements

### Identity & Rename (IDN)

<!-- The rename is mechanical but blast-radius-wide: imports, entry points, config
     paths, docs, tests. The offline suite is the safety net (401 tests). -->

- [x] **IDN-01**: The Python package is renamed `auraframes` → `pushframe` everywhere (every module path, every import, entry points, pyproject name) and the full offline suite passes green after the rename with no import of the old name left anywhere (`grep -r "auraframes" --include="*.py" .` outside migrations/tests-of-migration matches nothing)
- [x] **IDN-02**: The single console-script binary is `pushframe` (no `aura-cli` alias shipped); every user-facing string, help text, and doc references `pushframe`
- [x] **IDN-03**: First run migrates `~/.config/auraframes/` → `~/.config/pushframe/` automatically (Google cookie vault incl. legacy probes path, Google manifest, write budget) and prints a one-line notice; a fresh machine creates `~/.config/pushframe/` directly; the migration is idempotent and never loses the 0600 vault
- [x] **IDN-04**: Environment variables gain the `PUSHFRAME_` spelling as the documented primary form (`PUSHFRAME_EMAIL`/`PUSHFRAME_PASSWORD`/`PUSHFRAME_COUNTRY`/`PUSHFRAME_STATE_DIR`/`AURA_AWS_*` → `PUSHFRAME_AWS_*` etc.) with the `AURA_*` spellings still read as fallbacks (one release of grace), documented in README and `--help`
- [x] **IDN-05**: README, docs/CLI.md, VERIFICATION-REPORT.md and all planning-visible surfaces say the tool is `pushframe`, with an up-front "unofficial community client for Aura Frames hardware — not affiliated with or endorsed by Aura Frames Inc." disclaimer (nominative use of the mark only to identify compatibility)
- [x] **IDN-06**: The project leaves the fork: a new standalone GitHub repository `coredmp95/pushframe` (created as a normal repo, **not** a fork) receives the full history (337 commits, preserving upstream attribution in the log), the local `origin` remote switches to it, and the README's first line carries a provenance note crediting the upstream author (zmanowar) with the link to the original repository — the old `coredmp95/auraframes` fork stays in place untouched as an archive. *(Operator decision 2026-09-29: the repo switch happens in Phase 20, in the same movement as the code rename, so the first tagged release lands in the new repo.)*

### Debian Packaging (DEB)

<!-- Build native .deb artifacts a user can install without knowing Python exists.
     Python 3.14 is the pin; Ubuntu 26.04 (resolute) ships python3.14. The package
     carries its own venv (opt-in layout /opt or /usr/lib) rather than fighting
     distutils — the modern, hermetic, upstream-recommended pattern for apps. -->

- [x] **DEB-01**: `dpkg -i pushframe_<version>_amd64.deb` (or `apt install ./pushframe_….deb`) installs a working `pushframe` on Ubuntu 26.04: binary on PATH, `pushframe status --help` runs, dependencies satisfied — verified in a clean container/schroot, not just the dev machine
- [x] **DEB-02**: The package is built reproducibly by a repo script (e.g. `scripts/build-deb.sh` or `fpm`/`dpkg-deb` via pyproject metadata — version read from the single source of truth) and emits the `.deb` as a CI/release artifact; building requires no Debian packaging expertise
- [x] **DEB-03**: Correct Debian metadata: Package `pushframe`, Section `utils`, Maintainer, Description (with the unofficial disclaimer), License, Depends expressing the interpreter requirement (e.g. `python3 (>= 3.14)` or the bundled-runtime equivalent), and Conflicts/Replaces/Provides for the never-shipped `aura-cli` name avoided (no conflict needed — the name was never packaged; documented decision)
- [x] **DEB-04**: Install/uninstall is clean per Debian policy as observed by `lintian` (no errors): files under `/usr/lib/pushframe/` (private venv) + `/usr/bin/pushframe` symlink, config strictly under `$HOME` at runtime (no root-owned files in `~`), postrm removes nothing from `$HOME`
- [x] **DEB-05**: An APT repository layout is published for distribution: `dists/`+`pool/` structure (reprepro or dpkg-scanpackages based), Release/InRelease signing key documented, and the repo served from GitHub Pages with the one-line user instructions (`curl … | apt` sources entry + `apt install pushframe`)

### PyPI Publishing (PYI)

<!-- The second distribution channel: uv tool install / pipx for non-Debian systems
     and for users who prefer Python tooling. -->

- [x] **PYI-01**: `pip install pushframe` (or `uv tool install pushframe`) yields the same working `pushframe` binary; the sdist/wheel build is driven from the same pyproject metadata (name `pushframe`, version single-sourced) — verified against TestPyPI first, then PyPI
- [x] **PYI-02**: Publishing is automated and non-interactive from CI/release (trusted publishing or token in secrets), tagged releases only, with a documented manual fallback; the PyPI project description is the README (with the disclaimer visible on the project page)

### Release Engineering (REL)

<!-- "Distributable easily" means a repeatable release: one tag → all artifacts. -->

- [x] **REL-01**: A release is one action (git tag or workflow dispatch) producing: versioned `.deb` artifact(s), an updated APT repo commit/branch for GitHub Pages, and a PyPI upload — no hand-built artifacts, version numbers never edited by hand in more than one place
- [x] **REL-02**: The version scheme is set and documented (project moves off `0.1.0`; first distribution release is `5.0.0` to align with the milestone), and `pushframe --version` reports it
- [x] **REL-03**: The release docs (README "Install" section + docs/CLI.md) show all three install paths end-to-end: `.deb` file, APT repo one-liner, and `uv tool install pushframe` — each verified on a clean environment during the phase that ships it

## Future Requirements

Deferred, tracked, not in this roadmap.

- **RPM/openSUSE/Fedora packaging** — same tooling could emit `.rpm`; deferred until someone asks
- **Homebrew formula** — macOS distribution; the client is Linux-focused today
- **Docker image** — useful for servers/unattended sync; folds naturally into GSF-02 (scheduled sync)
- **PPA (Launchpad) publication** — the APT repo on GitHub Pages covers distribution; a PPA adds review overhead without adding reach

## Out of Scope

| Feature | Reason |
|---------|--------|
| An `aura-cli` compatibility alias binary | Operator decision 2026-09-29: single `pushframe` binary; the old name is trademark-adjacent and PyPI-taken |
| Renaming upstream's API client behavior or Pushd endpoints | The rename is identity-level only; behavior is frozen this milestone |
| Debian archive (packages.debian.org) inclusion | Requires a Debian maintainer + ITP process; the self-hosted APT repo + PyPI cover distribution now |
| Windows/macOS native packages | No operator demand; the client targets the user's Ubuntu host today |
| Breaking config format changes during migration | Migration moves files as-is; formats unchanged (IDN-03) |

## Traceability

Populated during roadmap creation. Every v5.0 requirement maps to exactly one phase;
phase numbering continues from v4.0's Phase 19.

| Requirement | Phase | Status |
|-------------|-------|--------|
| IDN-01..06 | Phase 20 | Complete |
| DEB-01..05 | Phase 21 | Complete |
| PYI-01..02 | Phase 22 | Complete |
| REL-01..03 | Phase 22 | Complete |

**Coverage:**

- v5.0 requirements: 16 total
- Mapped to phases: 15 ✓ (100% — no orphans, no duplicates)

| Phase | Requirements | Count |
|-------|--------------|-------|
| Phase 20 — pushframe Rename, Migration & Repo Switch | IDN-01..06 | 6 |
| Phase 21 — Debian Package & APT Repo | DEB-01..05 | 5 |
| Phase 22 — PyPI + Release Engineering | PYI-01..02, REL-01..03 | 5 |

---
*Requirements defined: 2026-09-29 — name research: aura-cli taken on PyPI (Neo4j Aura CLI); Aura Frames Inc. trademark risk; `pushframe` verified free on PyPI, no Debian collision; operator chose rename binary+module with config migration.*

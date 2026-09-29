# Plan 21-01 Execution Summary — Debian Package (DEB-01..04)

**Phase:** 21-debian-package-apt-repo
**Date:** 2026-09-29
**Status:** Complete
**Commits:** `38ef0f2` (build + packaging metadata), `a8b9404` (container proof + README)

## What Shipped

- `scripts/build-deb.sh` — one command, version single-sourced from
  pyproject.toml (D-08). Stages uv's standalone CPython 3.14 under
  `/usr/lib/pushframe/python/`, installs pushframe + deps into it
  (`uv pip install --target`), writes the POSIX-sh `/usr/bin/pushframe`
  wrapper (D-06), assembles `DEBIAN/` from `packaging/control-template`
  (D-07) + postinst + DEP-5 copyright + changelog + lintian overrides, and
  builds `dist/pushframe_<v>_amd64.deb` (~57 MiB, ~13 s).
- `scripts/test-deb-container.sh` — the clean-room proof of D-09 (DEB-01..04),
  every assertion named; fresh container per run (idempotent).
- `packaging/` — control-template (amd64, `Depends: ca-certificates, libc6`,
  unofficial disclaimer in the Description), postinst (one-line hint, never
  touches $HOME), pushframe.copyright (DEP-5, dual MIT line), lintian-overrides
  (each tag justified).
- README "Install (Ubuntu/Debian)" section with the `.deb` journey; APT
  subsection lands with 21-02.

## Verification Results

| Check | Result |
|-------|--------|
| `lintian --fail-on error` | **0 errors, 0 warnings** (RC 0) |
| install → `pushframe status --help` | exit 0, binary on PATH |
| dpkg metadata | Package/Version/Section: utils/Architecture: amd64 + disclaimer |
| remove | `/usr/bin/pushframe` **and** `/usr/lib/pushframe` gone |
| HOME integrity (DEB-04) | markers in root's **and** a regular user's home survive |
| Offline suite | **407 passed** |
| Version single source (D-08) | grep: no hardcoded version in the script |

## Clean-Room Findings That Hardened the Package

1. **`Architecture: all` was wrong** — the embedded interpreter is an x86-64
   ELF; ~200 lintian errors (`arch-independent-package-contains-binary-or-object`).
   → `amd64`; the one remaining error (`missing-dependency-on-libc`) →
   `Depends: libc6`.
2. **Build-path leak in shebangs** — uv writes console-script shebangs as the
   absolute staging path (`/home/…/build/deb/…`); broken after relocation and
   flagged `unusual-interpreter`. → rewritten to the runtime path.
3. **Permissions** — uv leaves 775 dirs + exec bits on plain `.py`;
   normalization now runs **last** (an earlier pass ran before `usr/share/`
   existed and flattened exec bits off tk demos) with shebang-aware 755.
4. **`no-changelog`** (error for a native-style package) → real
   Debian-format `changelog.gz`, generated at build time.
5. **Runtime residue broke removal** — a root run wrote root-owned
   `__pycache__` into the embedded tree; dpkg then abandoned
   `/usr/lib/pushframe` on removal (found by Task 2's script, invisible in
   one-shot manual runs). → bytecode pre-compiled and **package-owned**
   (`compileall` at build) + `PYTHONDONTWRITEBYTECODE=1` in the wrapper;
   `package-installs-python-pycache-dir` overridden with justification.
6. **`.gitignore` upstream pattern `[Ss]cripts`** silently ignored the whole
   repo `scripts/` dir → re-included with a documented `!scripts/`.

## Lintian Residuals

None at error or warning level. Overrides in use (all documented in
`packaging/lintian-overrides`): embedded-library, unstripped-binary-or-object,
custom-library-search-path, unusual-interpreter, package-contains-timestamped-gzip,
hardening-no-pie, package-installs-python-pycache-dir, no-manual-page
(manpage deferred to phase 22 release engineering).

## Deviations

- Plan said `Architecture all` / `_all.deb` and lacked the shebang/perms/
  changelog/bytecode hardening — all updated in-place in the plan with the
  container evidence before this SUMMARY.

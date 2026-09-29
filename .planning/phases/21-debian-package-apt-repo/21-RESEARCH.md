# Phase 21 Research: Debian Package & APT Repo

**Researched:** 2026-09-29 (discuss session + hands-on packaging probe in Docker)
**Confidence:** high for the layout (probe-proven end-to-end in a clean
`ubuntu:26.04` container), medium for the APT-repo UX details (gh-pages serving
is standard but not yet exercised here).

## Summary

The phase's only real risk was "does an embedded Python runtime survive
relocation into a machine that has never seen this project?" The probe settled
it: the **uv standalone Python 3.14.4** (python-build-standalone, 32 MB binary)
runs from an arbitrary mount path in a clean `ubuntu:26.04` container; a venv
built against the **host** `/usr/bin/python3.14` does **not** (its interpreter is
a symlink into the host — `not found` after relocation). Decision D-03
(standalone, provisioned by uv at build time) is therefore load-bearing and
evidence-backed, not preference.

## Probe log (2026-09-29, `/tmp/pf-packaging-probe`)

1. **System-venv relocation FAILS**: `uv venv --python 3.14` against the host
   → `pyvenv.cfg: home = /usr/bin` → copied tree in container:
   `/mnt/venv/bin/python: not found`.
2. **Standalone works**: `uv python install 3.14` (108 MB tree) → copied under
   a fake `/usr/lib/pushframe/python` + project installed into its
   site-packages (probe total **195 MB**) → in `ubuntu:26.04` container:
   `python3.14 --version` → **Python 3.14.4**. The `python3` name is a symlink
   chain to the 32 MB static `python3.14` binary — relocation-safe.
3. **Entry point**: `python -m pushframe.cli` (module form, no console-script
   path games inside the tree); wrapper `/usr/bin/pushframe` will `exec` it.
4. **Tooling on the host**: `dpkg-deb` ✓, `dpkg-scanpackages` ✓, `gpg` ✓ (0
   secret keys — key generation is part of the phase, D-11), Docker 29.8.1 ✓,
   `ubuntu:26.04` image pulled ✓. `fpm` absent (and rejected, D-01).
   `apt-ftparchive` NOT present on the host — either `apt install apt-utils`
   (provides it) during setup, or use `dpkg-scanpackages` + manually-generated
   `Release` (hashes via `sha256sum`, fields via a heredoc) which avoids the
   dependency; the plan picks this zero-dependency path and signs with gpg.

## Size budget

- Standalone python tree: ~108 MB (prunable: `idle*`, `pip` seeds, test dirs →
  ~-15 MB), site-packages with the project + deps: ~93 MB (prunable
  `__pycache__`: 48 dirs). Target `Installed-Size` ≤ ~180 MB; the `.deb`
  compresses to roughly 60-70 MB (xz). Accepted (D-06 rationale: hermetic
  beats svelte for an app package; noted as a deferred-idea to slim later).

## Debian metadata specifics (DEB-03)

- `Architecture: all` is honest about the *control* files; the embedded
  interpreter is amd64 ELF — Debian convention would call that `amd64`. The
  operator-accepted call (D-06): ship `all` + document "amd64 runtime built
  by this package" in the description; revisit multi-arch later (deferred).
  (Alternative rejected: `Architecture: amd64` without a real cross-arch story
  adds lintian noise for zero gain today.)
- `Depends: ca-certificates` only. httpx certifi is already bundled via
  site-packages; system CA still preferred as trust anchor for TLS to
  api.pushd.com/photos.google.com — keep certifi AND ca-certificates (cheap,
  robust).
- Maintainer scripts: `postinst` prints a short "installed — run `pushframe
  status`" hint; `prerm`/`postrm` do NOT touch `$HOME` (DEB-04).
- `Built-Using`/`Built-For-Profiles` unnecessary; `Section: utils`,
  `Priority: optional`.

## APT repo specifics (DEB-05)

- Zero-dependency repo assembly: `dpkg-scanpackages --multiversion pool/ /dev/null`
  → `Packages` → hand-built `Release` (Origin/Label/Suite/Codename/Architectures/
  Components/Date + SHA256 sums over Packages/… ) → `gpg --clearsign -o InRelease`
  + detached `Release.gpg`. This avoids `apt-ftparchive` (absent on host) and
  reprepro entirely; ~30 lines of script, fully inspectable.
- gh-pages as an **orphan branch** replaced wholesale per publish (D-12): no
  history growth, the branch is a pure artifact store. GitHub Pages project
  site root serves it at `https://coredmp95.github.io/pushframe/`.
- User instructions (README block): install keyring file to
  `/etc/apt/keyrings/pushframe.gpg`, sources entry
  `deb [signed-by=/etc/apt/keyrings/pushframe.gpg] https://coredmp95.github.io/pushframe stable main`,
  `apt update && apt install pushframe`.
- lintian on the host is absent → install it during phase setup (or run
  lintian inside the container; plan picks: container, keeps host clean).

## Risks / Notes

- **GitHub Pages from gh-pages must be enabled once** in repo settings
  (operator one-click; documented in the publish script's output).
- **Key without passphrase** (D-11) is a real private key on the dev machine —
  gitignored export path `~/.config/pushframe-apt-key/` chosen (consistent
  project state home), README documents backup + rotation.
- **Docker hub rate limits**: `ubuntu:26.04` already pulled; CI reuse comes in
  Phase 22.
- The deb embeds the LICENSE (copyright file) — DEP-5 format keeps lintian quiet.

## Verification Commands (plan-level, deterministic)

- Build: `scripts/build-deb.sh` → `dist/pushframe_<v>_amd64.deb` exists
- Clean-room: `scripts/test-deb-container.sh` → exit 0 (install + --help +
  metadata + removal checks inside `ubuntu:26.04`)
- lintian: containerized `lintian --fail-on error` → no errors
- Repo: `scripts/publish-apt-repo.sh --dry-run` → assembled tree + signatures
  in a workdir; container test `apt update` against a `python3 -m http.server`
  of the tree → `apt install -y pushframe` succeeds

---
*Phase: 21-Debian Package & APT Repo*

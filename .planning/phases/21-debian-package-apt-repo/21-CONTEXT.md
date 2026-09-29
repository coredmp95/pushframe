# Phase 21: Debian Package & APT Repo - Context

**Gathered:** 2026-09-29
**Status:** Ready for planning
**Provenance:** compiled by the planning agent from ROADMAP §Phase 21, REQUIREMENTS
DEB-01..05 verbatim, and a hands-on packaging probe run on 2026-09-29 (Docker
`ubuntu:26.04` + uv standalone Python — see `21-RESEARCH.md` for the probe results).
The operator chose a **discuss session** at the checkpoint and settled, with probe
evidence on the table:

- **D-01 Build tooling = `dpkg-deb` + repo script** (no fpm/ruby, no debhelper chain).
- **D-02 Runtime = private embedded venv** under `/usr/lib/pushframe/` (hermetic;
  the distro's python3 is never mutated or depended upon).
- **D-03 Python inside the package = uv standalone Python** (python-build-standalone),
  provisioned at build time by uv — NOT the host's `/usr/bin/python3.14`: the probe
  proved a system-venv does not survive relocation into a clean container (symlink
  to the host interpreter), while the standalone build runs fine.
- **D-04 APT repo signature = dedicated GPG key generated during the phase**
  (`Pushframe APT Repository <deploy@pushframe>`), private key outside git;
  publish instructions + public key on the Pages site.
- **D-05 APT repo hosting = `gh-pages` branch of `coredmp95/pushframe`**
  (`https://coredmp95.github.io/pushframe/`).

<domain>
## Phase Boundary

Delivers: a repo script that builds `pushframe_<version>_all.deb` (staging tree +
`dpkg-deb --build`), the embedded runtime layout (`/usr/lib/pushframe/python/` +
wrapper `/usr/bin/pushframe`), correct Debian metadata (control, maint-scripts),
lintian-clean output, a containerized clean-room install test (DEB-01), and the
signed APT repository published on GitHub Pages with user-facing install
instructions (DEB-05).

Does NOT deliver: PyPI publishing or tag-driven release automation (Phase 22,
PYI/REL), RPM/Homebrew/Docker artifacts (deferred), Launchpad PPA (deferred),
any behavior change to the tool itself.
</domain>

<decisions>
## Implementation Decisions

### Package layout
- **D-06:** Filesystem layout inside the `.deb`:
  - `/usr/lib/pushframe/python/` — the standalone CPython 3.14 tree **plus** all
    dependency wheels + the `pushframe` package installed into its
    `lib/python3.14/site-packages/` (single tree, one interpreter);
  - `/usr/bin/pushframe` — POSIX-sh wrapper: `exec /usr/lib/pushframe/python/bin/python3.14 -m pushframe.cli "$@"`;
  - `/usr/share/doc/pushframe/` — copyright (DEP-5-ish: LICENSE + provenance),
    changelog.gz.
  No files under `/etc`, nothing root-owned in `$HOME`, cache/manifest stay in
  the user's XDG config at runtime.
- **D-07:** Control metadata: `Package: pushframe`, `Section: utils`,
  `Priority: optional`, `Architecture: all` (pure bytes; the embedded interpreter
  carries the amd64-ness of the build host — documented; an amd64-only target is
  accepted for this phase), `Depends: ca-certificates` (TLS roots for httpx —
  the standalone python uses OpenSSL bundled, but CA certificates come from the
  system), `Installed-Size`, Maintainer `Fabrice DIDIERJEAN <coredmp95@gmail.com>`,
  Homepage, Description with the not-affiliated disclaimer. No Conflicts (the
  `aura-cli` name was never packaged — RESEARCH finding).

### Build pipeline
- **D-08:** `scripts/build-deb.sh` (bash, `set -euo pipefail`), version read from
  `pyproject.toml` (single source of truth). Steps: fresh staging dir →
  `uv python install 3.14` into the staging tree → `uv pip install . --python
  <staged python>` (the project + deps, no dev extra) → prune `__pycache__`,
  `*.dist-info/RECORD` noise kept (needed by pip, harmless), write control
  files from a template with the version interpolated → `dpkg-deb --build
  --root-owner-group` → optional `lintian` when present. Output:
  `dist/pushframe_<version>_all.deb`.
- **D-09:** Clean-room verification (DEB-01) = `docker run ubuntu:26.04` mounting
  the built `.deb`: `apt install /mnt/pushframe_*.deb && pushframe status --help`
  (offline smoke; a full live login is NOT required in the container — `--help`
  plus `--version`-style flags prove the runtime). Script:
  `scripts/test-deb-container.sh`, exit non-zero on any failure. Note: the
  container needs network for `apt install ./file.deb` dependency resolution of
  `ca-certificates` on the base image — or pre-install it; the script handles
  both (`apt-get install -y ca-certificates || true` first).

### APT repository
- **D-10:** Repo layout (reprepro-free, `dpkg-scanpackages` + `apt-ftparchive`
  is enough for one package/family): `dists/stable/main/binary-amd64/Packages`,
  `Release` (+ `Release.gpg` + `InRelease` detached+clearsigned), `pool/main/p/pushframe/…deb`.
  Codename `stable`. Origin/Label `pushframe`.
- **D-11:** Signing: dedicated key per D-04; fingerprint documented in README;
  public key exported to `dists/pushframe.asc` (ascii-armored) so users can
  `curl … | gpg --dearmor -o /etc/apt/keyrings/pushframe.gpg`. The signing key
  is generated **without passphrase** explicitly so CI can sign non-interactively;
  private key never enters the repo (gitignored export path; README documents
  the backup duty).
- **D-12:** Publication = a script (`scripts/publish-apt-repo.sh`) that assembles
  the tree in a workdir, signs, then force-pushes the tree to the `gh-pages`
  branch of `origin` (orphan branch, no history bloat; the repo content IS the
  release). `https://coredmp95.github.io/pushframe/` serves it (project Pages
  from gh-pages branch, root).

### Docs
- **D-13:** README gains an "Install (Ubuntu/Debian)" section ahead of the uv
  instructions: (1) `apt install ./pushframe_…deb` from a release file, (2) APT
  repo one-liner block (keyrings + sources entry + apt install), (3) pointer to
  Phase 22's `uv tool install` as coming-soon.

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

- `.planning/REQUIREMENTS.md` — DEB-01..05 verbatim (the phase contract)
- `.planning/ROADMAP.md` §Phase 21 — success criteria 1-5
- `scripts/` (to be created) + `pyproject.toml` (version single source)
- `LICENSE` (copyright lines for the deb copyright file)
- `pushframe/pyvenv`-relevant facts: the package installs cleanly via
  `uv pip install .` (probe-proven 2026-09-29)
- `README.md` (Install section target location)

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `pyproject.toml` — name/version/license-file already correct (phase 20)
- The package installs with `uv pip install .` (probe: 93 MB venv incl. deps)
- uv standalone Python 3.14.4 (108 MB tree) — probe: runs in a clean
  `ubuntu:26.04` container from an arbitrary mount path
- Docker + docker-pulled `ubuntu:26.04` image on the dev machine (probe)
- `dpkg-deb`, `dpkg-scanpackages`, `gpg` present on the host (probe)

### Established Patterns
- Scripts in `scripts/` with `set -euo pipefail`, version single-sourced from pyproject
- Fail-loud, verify-in-container discipline (the 16-03 live-probe precedent)
- GitHub Pages for hosted artifacts (decision D-05/D-12)

### Integration Points
- Phase 22 consumes `build-deb.sh` output for the tag-driven release job
- `pushframe --version` (REL-02, phase 22) will be added to the same wrapper;
  the wrapper script in this phase should already accept and forward `--version`
  only if it costs nothing (it does not — argparse owns it in 22)

</code_context>

<specifics>
## Specific Ideas

- Prune after install to keep the deb lean: `__pycache__`, `pip`/`setuptools`
  seeds if unused, `*.exe` Windows stubs from pillow — probe shows ~48
  pycache dirs; target < 120 MB installed.
- `Installed-Size` computed from the staging tree (KB) — lintian checks it.
- Wrapper must `exec` (signal pass-through) and stay POSIX sh (no bash Depends).
- Container test asserts: binary on PATH, `pushframe status --help` exit 0,
  `dpkg -s pushframe` shows the metadata, `apt remove` removes `/usr/lib/pushframe`
  and leaves `$HOME` alone.
- gh-pages force-push must be `--force` with an orphan commit — document that
  the branch is disposable; the source branch never sees the repo tree.

</specifics>

<deferred>
## Deferred Ideas

- Multi-arch (arm64) builds via cross-provisioned standalone pythons
- Launchpad PPA with proper signing alignment
- apt-ftparchive `Contents-` and `Sources` indexes
- Delta packages / incremental repo updates
- `Suggests/Recommends` tuning (e.g. Suggests: chrome for google-link)

</deferred>

---

*Phase: 21-Debian Package & APT Repo*
*Context gathered: 2026-09-29*

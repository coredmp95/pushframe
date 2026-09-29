# Phase 22: PyPI + Release Engineering — Context

**Gathered:** 2026-09-29
**Status:** Ready for planning
**Provenance:** compiled by the planning agent from ROADMAP §Phase 22 and
REQUIREMENTS PYI-01..02 / REL-01..03 verbatim, plus a hands-on probe
(2026-09-29): `uv build` produces a clean pure-python wheel (entry point,
LICENSE, METADATA); `uv publish` natively supports trusted publishing;
the GitHub repo, Pages site and signed APT repo from phase 21 are live.
The operator chose, at the planning checkpoint:

## Decisions

- **D-01:** Trusted publishing (OIDC) — no PyPI token is ever created or
  stored (not in GitHub Secrets, not in the Vault). The
  GitHub Actions workflow authenticates to (Test)PyPI via OIDC; this
  requires a ONE-TIME publisher declaration in each of TestPyPI's and
  PyPI's web UI (project name `pushframe`, owner `coredmp95`, repo
  `pushframe`, workflow filename, environment name). Consequence: the
  rehearsal upload MUST itself run in GitHub Actions — locally `uv publish`
  cannot OIDC — so TestPyPI rehearsal is a workflow run, not a shell step.
- **D-02:** Release trigger = git tag `v*` — pushing `v5.0.0` runs the release
  workflow: version consistency check → build (wheel/sdist + deb) →
  TestPyPI-already-rehearsed → PyPI upload → deb publish → APT repo
  publish. One action, no hand-built artifacts (REL-01).
- **D-03:** First distribution release = `5.0.0` (aligns with the v5.0
  milestone; the project leaves `0.1.0`). `--version` reports it (REL-02).
- **D-04:** Operator account steps (2FA) — TestPyPI account,
  PyPI account, then the trusted-publisher declarations on both. The plan
  lists them explicitly with exact field values.
- **D-05:** Rehearsal bar (PYI-01) — a clean `ubuntu:26.04` container
  installs from **TestPyPI** (`uv tool install --index
  https://test.pypi.org/simple/ …` with deps from real PyPI) and runs
  `pushframe status --help`; then the same against **real PyPI**. Both
  proofs live in the journey test script.
- **D-06:** Phase-21 inheritance — the deb/APT pipeline is reused as-is
  (`build-deb.sh`, `publish-apt-repo.sh`) — the release workflow calls
  them; no duplication. `dist/` collision noted: build-deb.sh wipes `dist/`
  that `uv build` fills → the workflow builds into separate output dirs and
  build-deb.sh is taught an output-path override.

## Discretion Areas

- Workflow file layout (single release.yml vs split) and its step ordering.
- The exact `--version` implementation surface in `cli.py` (argparse hook).
- Whether REL-03's "clean environment" proof for `uv tool install` uses the
  same container journey script or a dedicated one.

## Deferred Ideas

- brew/homebrew tap, Windows/macOS packaging — out of scope (REQUIREMENTS
  "non-goals" table already excludes native packages).
- pyproject `dynamic` version from VCS tags — rejected for now: version
  single-source stays pyproject.toml (D-08 of phase 21); the tag must
  MATCH pyproject (workflow asserts it), not derive it.
- Signing the wheel/sdist (Sigstore) — candidate backlog, not required by
  any PYI/REL id.

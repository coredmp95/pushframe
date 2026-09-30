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

## [Unreleased]

### Changed (tests)

- **Network guard**: `httpx.Client` is patched for the whole test session
  so any client built WITHOUT an explicit transport gets a blocking one —
  except when built inside a live-marked test (the only legitimate
  real-network shape). An offline test that reaches the real network now
  fails loudly instead of silently depending on a local .env (the relogin
  masking that kept the tests workflow red from v5.1.0 to 5.1.1), and a
  local .env can no longer masquerade as a live posture.

## [5.1.1] — 2026-09-30

### Changed

- **Credible client identity** (venus 2026-09-30 anti-abuse lesson): the
  user agent is now a setting — `USER_AGENT` (env `PUSHFRAME_USER_AGENT`,
  or `pushframe config set USER_AGENT …`) — whose default tracks a
  current Play build (`Aura/4.7.4271 (Android 36; Client)`) instead of
  the years-stale `4.7.790`; combined with a per-install
  `config set DEVICE_IDENTIFIER "$(uuidgen)"`, an installation stops
  presenting the shared all-zeros fingerprint that kept reads green
  (like the real phone app) while writes were 401-refused for months.
- **Automatic identity provisioning**: the `pushframe config` wizard now
  generates a unique `DEVICE_IDENTIFIER` (uuid4) on a fresh install
  BEFORE its first API call, so the very first login already presents a
  per-install fingerprint — no installation ever ships the all-zeros id
  again. Explicit identities (env or config file) are never overwritten,
  and the generated id is stable across wizard re-runs.
- `config show` warns when the effective `DEVICE_IDENTIFIER` is still the
  shared all-zeros default, naming the anti-abuse rationale and the
  `config set DEVICE_IDENTIFIER "$(uuidgen)"` remedy.

## [5.1.0] — 2026-09-29

### Added (phase 25: Multi-frame & Scheduling)

- **Named pairs** (MTF-01): `pushframe config pair add/remove/list` —
  `pairs` in config.json is a named dict (`{"cadre-venus": {"album": …,
  "frame": …}}`); duplicates and unknown names fail named.
- **`google-sync --pair <name>` / `--all`** (MTF-02/03): per-pair state
  (manifest `~/.config/pushframe/pairs/<name>/`, cache
  `~/.local/state/pushframe/pairs/<name>/cache/`); `--all` runs every pair
  in sorted order with ONE shared write budget (account-level SAFE-02);
  a failing pair is reported and never blocks the others; exit 1 if any
  failed.
- **`pushframe schedule add/list/remove`** (TMR-01..03): systemd USER
  timers (no root) — `pushframe-<job>.service/.timer`, oneshot,
  `Restart=no` (the next tick is the retry), `RandomizedDelaySec`, per-job
  log at `~/.local/state/pushframe/<job>.log`. ExecStart is fully
  non-interactive (token session); **`--scheduled` flips SAFE-02 to
  skip-and-log** (a timed run never mass-hides silently and never fails
  the unit over a safety decision). Preflight names the systemd user
  session requirement with the `loginctl enable-linger` remedy
  (documented; never executed by the tool).

## [5.0.7] — 2026-09-29

### Added (phase 24: Token-First Sessions & Preflight Sweep)

- **One session path for every command** (SEC-01/02): `inspect`,
  `reconcile`, `sync`, `push` and `google-sync` now run from the stored
  token session (0600) with no password and no login call; env vars stay
  the override; a terminal session gets ONE password prompt whose token is
  persisted (SEC-01: the password is never written anywhere — file-content
  tested); non-interactive runs without credentials fail named.
- **Token expiry** (D-01): a 401 while resuming the stored session prompts
  once in a terminal (new token persisted, command continues) and raises
  `SessionExpiredError` with the remedy in scheduled runs. `execute_plan`'s
  401-retry relogin is token-aware (it previously assumed a password).
- **`pushframe logout`** (SEC-03, D-02): deletes the stored token (and only
  the token) — email and settings survive, mode stays 0600, idempotent.
- **Preflights** (PRF-01, D-03): `google-sync` checks the vault up front
  (remedy: `google-link` + the headless ssh -X recipe); `sync`/`push` fail
  named on a nonexistent source directory BEFORE any network call (an
  empty directory remains a friendly "nothing to do").
- **The traceback-free sweep** (PRF-02): a parametrized test over the
  failure-mode inventory proves every foreseeable mode ends named, with a
  remedy, and never a traceback.

### Changed

- DI contract, now enforced everywhere: a call site NEVER re-authenticates
  an injected Aura (tests, `doctor`, pipelines). Login-failure shapes are
  owned by the session path.

## [5.0.6] — 2026-09-29
- **`pushframe doctor`** — the field write-probe the venus regressions
  demanded: one 4×4 test image through the REAL write path (S3 +
  select_asset + batch_update), verify + cleanup, then a GO/NO-GO verdict
  with the 401 body classified. `--no-write` for reads-only. Closes the
  release gap: green tests + green journeys never proved THIS machine can
  write TODAY.

## [5.0.5] — 2026-09-29

### Added

- **`pushframe config`** (v5.1, phase 23): interactive wizard that asks for
  the email + password (hidden input), **verifies the login against the real
  Aura API before writing anything**, and stores email + session token —
  never the password — in `~/.config/pushframe/config.json` (0600, atomic
  writes, schema-versioned). Subcommands: `show` (effective value + source
  per key, secrets masked, env-shadow warnings), `import FILE` (adopts an
  existing `.env`, skipping what env already provides), `set/get/path`.
  With a stored session, `status` runs with **zero environment variables**
  (resumes the session — no login call, no password traffic; proven by a
  pristine-container journey, `scripts/test-config-journey-container.sh`).
- Headless `google-link` recipe documented (ssh -X); a `--remote-assist`
  mode is a backlog candidate.

### Fixed

- **`google-sync`/`sync`/`push` apply feedback was opaque** (venus
  regression): the progress bar sat silent for minutes at a time — budget
  waits and inter-chunk cooldowns never reached the bar — and failures
  showed a bare `upload FAIL` with no cause. The bar now shows a live
  `pacing Ns — budget refill/cooldown, normal` countdown for every wait,
  names the file on each item, escalates failures WITH their cause plus
  the lockout remedy (401 → stop, wait, `pushframe status`), and the run
  announces the pacing contract up front (long waits are normal after the
  30-request burst; interrupts are safe, confirmed items are kept).
  `execute_plan` gained an `on_error(kind, id, ok, reason)` seam so every
  failure site (upload/reshow/delete, including the 401-retry paths)
  reports its reason live.
- The abort message on the 5-consecutive-failures breaker now states what
  to expect: confirmed items ARE on the frame, the next run recognizes
  them and will not upload them twice, wait ~30 min before retrying.
- **Scoped-trip honesty** (second venus regression): `pushframe status`
  staying green does NOT mean writes will work — the anti-abuse trip can
  be scoped to the assets surface (write endpoints AND the
  `asset_for_local_identifier` verify-probe read) while login/frames keep
  answering. The abort message no longer suggests status as an all-clear;
  it prescribes 60+ min from the abort and a single, solitary retry call.
- **"Request Unauthenticated" + logout:true identified as the trip's
  disguise** (fourth venus regression, decisive capture): the body arrived
  on a FRESH token immediately after a successful re-login, one item after
  a doctor probe had WRITTEN fine — so Pushd's "Request Unauthenticated"
  here means "we refuse your writes (trip)", not "your token is bad". The
  classifier now pins this exact body to the wait-60+-min verdict and
  warns against a pointless re-login.
- **First-occurrence trip stop**: a write 401 carrying that proven body
  aborts the run IMMEDIATELY (`TripDetectedError`) — no iterating to the
  5-failure backstop, no re-login, no resend (all feed the trip). Budget
  force-reconciled; the message states what is confirmed on the frame and
  that the next run resumes cleanly. (Also fixed en route: TripDetected
  propagates through every chunk-level handler — the generic per-chunk
  attribution branch would otherwise swallow it as ordinary failures.)
- **`google-sync --batch-size N`**: the trip keys on per-batch volume (a
  1-item doctor write passed seconds before a 50-item chunk was refused),
  so an operator can stay under the detection threshold (e.g. 10).
  Forwarded only when supplied — the historical default (50) otherwise
  rules. The anti-trip drill (doctor → wait → batch-size) is documented in
  docs/CLI.md.
- **401 bodies are captured and classified** (third venus regression): an
  HTTP 401 exception now carries the server's response body (redacted
  through the same filter as request logs, truncated to 300 chars, also
  debug-logged) — so per-item failure reasons, verify-probe errors and the
  consecutive-failures abort all show WHY the write was refused. The abort
  classifies the body: a silent/generic envelope reads as the anti-abuse
  trip (wait 60+ min, single retry); a body naming the token/session reads
  as a token problem (re-login, retry once, do not wait an hour).

### Changed

- Settings now resolve **at use time** with the precedence
  **environment → config file → default** (previously environment → default
  only, frozen at import time). Every `settings.X` reader keeps working
  unchanged; long-running processes now see updated values. This also makes
  `PUSHFRAME_API_BASE_URL` effective for real — it sat in the settings table
  but `client.py` still read its own frozen constant.
- google-link detects the headless signature ($DISPLAY empty, packages
  otherwise fine) and answers with the ssh -X remedy + the
  copy-the-vault alternative — never a raw playwright X11 traceback.

## [5.0.4] — 2026-09-29

### Added

- google-link zero-config: the dedicated Chrome profile defaults to
  `~/.config/pushframe/chrome-profile` (created on demand); the env var
  becomes an override. A near-miss env name (e.g. a truncated
  `USHFRAME_…`) is called out explicitly instead of looking like "unset".
- google-link preflight: missing prerequisites (playwright package,
  Chrome/Chromium) fail with the exact remedy instead of a traceback
  (`ModuleNotFoundError: playwright` was the whole output before).

### Changed

- `PUSHFRAME_PROBE_CHROME_PROFILE` is no longer required to run
  `google-link`.

### Fixed

- google-link preflight on the uv-tool install path: remedy for a missing
  playwright now matches every install shape (`uv tool install
  'pushframe[google-browser]' --force` for uv tools — `pip install
  --user` targets the wrong interpreter there); stray duplicate quote in
  the message fixed.

## [5.0.3] — 2026-09-29

### Added

- `CHANGELOG.md` — every release now traced in Keep a Changelog format
  (Added / Changed / Deprecated / Fixed / Security), exposed as a
  `Changelog` project URL on PyPI and referenced from the README.
- `arch=amd64` in the documented APT sources entry: silences apt's
  i386 notice on multi-arch machines (the repo is amd64-only).

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

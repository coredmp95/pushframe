# Aura Frames Python Client — Revive & Verify

## What This Is

An unofficial, reverse-engineered Python client for the Aura Frames (Pushd) digital
photo-frame cloud API. It authenticates with an Aura *account* and pulls/pushes photos
through the cloud API (`api.pushd.com/v5`) plus AWS S3/SQS — it does not talk to the
frame over the local network. **v1.0 (shipped 2026-06-30)** revived the ~3-year-old
codebase so it runs again on a current toolchain (Python 3.14 + `uv`, pydantic v2) and
verified the core read flow (login → list → download) still works end-to-end against the
live service. **v1.1 (shipped 2026-07-05)** added a `Client`/`Aura` dependency-injection
transport seam and a reusable offline `httpx.MockTransport` test harness, lifting most of
the read-path test suite off the live network while a byte-identical `@live` suite
remains the drift oracle. **v2.0 (shipped 2026-09-02)** turned that foundation into a
real tool: a packaged `aura-cli` with `status`/`inspect`/`sync`/`push`, content-hash
directory-to-frame mirroring, and the **write path proven live for the first time in the
codebase's three-year history** — upload, `remove_asset`, `exclude_asset` (hide) and
`delete_asset` all exercised against a real account and frame, behind a proactive
anti-abuse rate-limiter and a hide-by-default removal mode that costs visibility rather
than photos.

## Core Value

Prove the existing client still works end-to-end (login → list → download) on a current
Python toolchain, so we know exactly what survives before building anything new.

> ✓ **Achieved in v1.0.** The read path is proven live (login → list → 77-asset cursor
> drain → image download with EXIF intact). **v1.1** made that proof cheap to re-run
> (offline, no credentials) without weakening it — the `@live` suite still exists as the
> ground truth. **v2.0** shifts the core value: prove the **write path** the same way,
> and turn that proof into a real usable capability — syncing a local photo directory to
> a frame — rather than another internal-only verification pass.
>
> ✓ **Achieved in v2.0 (2026-09-02).** The write path is proven live (`select_asset` → S3
> → SQS → `batch_update` upload round-trip, `remove_asset`, `exclude_asset`/`select_asset`
> hide-and-re-show, `delete_asset` blast radius measured by before/after inventory diff),
> and it ships as `aura-cli sync`, not as an internal test. **The core value now shifts
> again: from *proving* the write path to making it boringly reliable** — the live runs
> surfaced transient 401s, unremovable placeholder rows, and a server-side pagination
> inconsistency that a tool people actually depend on should absorb rather than expose.

## Current Milestone: v3.0 Google Photos Album Sync

> **⚠ 2026-09-28 — SCOPE CHANGE:** the Google Photos album-sync goal below is
> **abandoned** (user decision). Aura's own restored server-side Google sync — which the
> v3.0 spike phase was going to investigate around — **does not work in practice**, and
> nothing will be built on it. Phases 12-15 are cancelled; any future Google album sync
> will be a **local** sync built from scratch (own mechanism, local cache with disk
> minimisation, periodic re-auth accepted), replanned in a fresh milestone together with
> the carried debt (MOD-02, MOD-04, TEST-01). Phase 11 (write-path reliability, completed
> and verified 2026-09-03) is this milestone's delivered value.

**Goal:** Sync Google Photos albums to Aura frames, on a write path that no longer fails
spuriously.

**Target features:**

- **Write-path reliability (foundation, sequenced first)** — retry once on HTTP 401 with a
  fresh login inside `execute_plan`; reconcile the 58 stuck placeholder rows; triage the 3
  open Phase 8 code-review findings (the hardcoded `data_uti='public.jpeg'` one is now
  load-bearing — Google albums contain `.png`/`.heic`)
- **Link a Google account** — OAuth loopback flow (browser on the same machine as `aura-cli`),
  refresh token persisted alongside the existing credential handling
- **Discover albums** — ⚠️ *mechanism unresolved.* Google restricted the Photos Library API's
  broad read scope around March 2025; enumerating a user's own albums may no longer be
  available to a general app. Research must settle whether this is a true `albums.list`, a
  Picker-API "choose once, remember it" flow, or an allowlist application. This is the
  milestone's single biggest unknown and it may reshape the requirement.
- **Sync album → frame** — download to a local cache dir, then reuse the proven v2.0
  content-hash diff/upload pipeline unchanged; cache pruned once uploads confirm
- **Mirror semantics** — the Google album is source of truth. Photos removed from the album
  are **hidden** on the frame (`exclude_asset`), never deleted by default; real deletion stays
  opt-in and exact-count-gated exactly as in v2.0
- **Many-to-many mapping** — a persisted config file maps N albums to N frames, reconciled in
  a single run
- **Photos only** — videos skipped with a reported count (frame-side `md5_hash` is null for
  every video asset; content-hash diffing cannot see them)
- **Carried debt** — MOD-02 (config-ize AWS pool IDs/bucket), MOD-03 (typed exceptions),
  MOD-04 (loguru sink leak); concurrent downloads on the Google side only, *not* the full
  MOD-01 async migration; finish the lift-tests-off-network slice (candidates #2 + #4)

## Current State

**Shipped:** v2.0 Directory-to-Frame Sync (2026-09-02) — phases 5-10, 17 plans, 160 commits.

`aura-cli` is a real, packaged CLI with four verbs:

- `status` — config/auth health (creds set? login succeeds? which account?) + account frames
- `inspect --frame <name|id>` — frame metadata (name, owner, contributor count, asset count)
  plus the first N photos; resolves frames by case-insensitive name substring or exact ID
- `sync <dir> --frame <name|id>` — content-hash directory mirroring, **dry-run by default**;
  `--apply`/`--yes` executes. Removal defaults to **hide** (`exclude_asset`), with the two
  destructive tiers opt-in and gated by their own destructiveness
- `push` — direct upload path with the anti-abuse budget/geo override flags

Underneath: a `WriteBudget` token bucket (persisted per account, reconciled on real
anti-abuse trips) and a fail-open `check_geo` pre-flight guard protect every `--apply`.

**PR #1 merged 2026-09-03** — the whole v2.0 milestone is landed on `master`.

**Now building (v3.0):** ~~Google Photos album sync, on top of a reliability pass~~ —
reliability delivered (Phase 11, 2026-09-03); the Google-sync goal was abandoned
2026-09-28 (see the scope-change note under Current Milestone). A replan of the remaining
scope as a fresh local-sync milestone is pending. See
`.planning/REQUIREMENTS.md` for what was cancelled vs carried forward.

## Requirements

### Validated

<!-- Inferred from existing code (April 2023). Built and shipped previously; working
     status against the *current* stack/API is what this milestone verifies. -->

- ✓ Authenticate to the Aura cloud API via email/password token auth — existing
- ✓ List and fetch frames and their assets (cursor-based pagination) — existing
- ✓ Download frame images via the image proxy with EXIF (datetime + GPS) injection — existing
- ✓ Upload images mimicking the device flow (select_asset → S3 → SQS → batch_update) — existing
- ✓ AWS Cognito anonymous auth for S3/SQS access — existing
- ✓ Pydantic DTO model layer hydrating API responses — existing
- ✓ Environment-variable based configuration — existing
- ✓ Project installs and imports on Python 3.14 managed by `uv` — Validated in Phase 1: Toolchain Revival
- ✓ Dependencies resolve and build on Python 3.14 (pydantic v1→v2 migration; pillow 12, httpx 0.28, boto3 1.43) — Validated in Phase 1: Toolchain Revival
- ✓ Dependency manifest migrated from the UTF-16 `requirements.txt` to `pyproject.toml` + `uv.lock` — Validated in Phase 1: Toolchain Revival
- ✓ Login verified against the live API with real account credentials (READ-01) — Validated in Phase 2: Live Read-Path Verification
- ✓ Listing frames verified against the live API (READ-02) — Validated in Phase 2: Live Read-Path Verification
- ✓ Fetching a frame's assets with cursor pagination verified live — 77 assets across multiple pages (READ-03) — Validated in Phase 2: Live Read-Path Verification
- ✓ Downloading one image with EXIF (datetime + GPS) read back from disk verified live (READ-04) — Validated in Phase 2: Live Read-Path Verification
- ✓ Documented `uv` setup/run commands + env vars and a repo-root VERIFICATION-REPORT.md recording read-path status and API drift (ENV-04, DOC-01) — Validated in Phase 3: Run Docs & Verification Report
- ✓ `Client`/`Aura` dependency-injection transport seam (`Client(transport=...)`, `Aura(client=...)`) plus a reusable offline `httpx.MockTransport` test harness and sanitized fixtures, lifting most of `test_read_path.py`'s assertions off the live network while leaving the `@live` suite untouched as the drift oracle (R4-SEAM-CLIENT, R4-SEAM-AURA, R4-FIXTURES, R4-FIXTURE-VALIDITY, R4-HARNESS, R4-OFFLINE-TESTS, R4-LIVE-UNCHANGED) — Validated in Phase 4: Client Transport Seam for Offline Testability
- ✓ Packaged `aura-cli` entrypoint distinct from `main.py`, with a `status` subcommand reporting config/auth health, login result, and the account's frames — quiet by default with an opt-in `--debug` flag for verbose loguru output (CLI-01, CLI-02) — Validated in Phase 5: CLI Skeleton + Status
- ✓ `inspect --frame <name|id>` resolves a frame by case-insensitive name substring or exact ID, displays its photos and metadata (name, owner, contributor count, asset count), and gives a clear disambiguation error on ambiguous name matches; `--debug` promoted to a root-level flag (CLI-03, CLI-04) — Validated in Phase 6: Inspect + Frame Resolution
- ✓ `sync <dir> --frame <name|id>` computes and prints a full upload/delete/unchanged dry-run plan by content-hash diffing (never filename), with zero mutating call reachable from the command — structurally dry-run only, no `--apply`/`--yes` path exists yet (SYNC-01, SYNC-02) — Validated in Phase 7: Sync-Diffing Engine (Dry-Run Only)
- ✓ `sync --apply`/`--yes` executes the computed plan for real — uploads new local files (`select_asset` → S3 → `batch_update`) and removes gone-locally frame photos via `remove_asset`; prints upload/delete/unchanged counts before applying and exits non-zero on any execution failure (SYNC-03, SYNC-04) — Validated in Phase 8: Destructive Execution (Upload + Delete Verification)
- ✓ Image upload round-trip verified live against a real account/frame (WRITE-01) — Validated in Phase 8: Destructive Execution (Upload + Delete Verification)
- ✓ `remove_asset`'s real behavior verified live — disassociates from the frame only (WRITE-02) — Validated in Phase 8: Destructive Execution (Upload + Delete Verification)
- ✓ `delete_asset`'s real behavior verified live — asset-scoped `DELETE /assets/{id}.json`, broader than `remove_asset`, correctly left unwired from `--apply` (WRITE-03) — Validated in Phase 8: Destructive Execution (Upload + Delete Verification)
- ✓ Hardcoded frame ID in the SQS upload-confirmation lookup fixed and confirmed live for an arbitrary frame (WRITE-04) — Validated in Phase 8: Destructive Execution (Upload + Delete Verification)
- ✓ Fail-loud error handling extended to the write/delete endpoints (WRITE-05) — Validated in Phase 8: Destructive Execution (Upload + Delete Verification)
- ✓ Proactive client-side write rate-limiter (`WriteBudget` token bucket, persisted per-account + reconciled on real anti-abuse trips) that waits/stops before tripping the Pushd limit, plus a configurable geo pre-flight guard (`check_geo`, fail-open by default) that refuses writes when the exit-IP country differs from the account's country — wired into `execute_plan`/`run_sync`/CLI as a true no-op when unconfigured, 100% offline-tested (ANTI-01..ANTI-07) — Validated in Phase 9: Proactive Write Rate-Limiter & Geo Guard
- ✓ `sync --apply` defaults to **hiding** removed photos rather than deleting them: `exclude_asset` (widened to the batch shape) marks them invisible while they remain on the frame, `select_asset` re-shows a restored file without re-uploading it, `get_assets` sends `filter=all` and joins the parallel `asset_settings` array so the diff engine classifies every frame asset re-show / unchanged / removal-candidate / already-hidden, and the two destructive tiers are mutually-exclusive opt-in flags gated by an exact-count confirmation (HIDE-01..HIDE-08) — Validated in Phase 10: Hide-instead-of-delete sync mode
- ✓ `delete_asset` re-verified asset-scoped by before/after live inventory diff (158 → 157, exactly the target, no drift since Phase 8) (HIDE-07) — Validated in Phase 10: Hide-instead-of-delete sync mode

### Active

<!-- v1.0, v1.1, v2.0 and v2.x (Phase 9 anti-abuse, Phase 10 hide-by-default) all
     validated — see Validated above. The items below are v3.0's scope, ordered by
     sequence: reliability first, then the Google Photos integration, then debt. -->

- _All v1.0, v1.1, v2.0 and v2.x requirements validated — see Validated above._

**Part 1 — write-path reliability (sequenced first; the integration lands on top of it)**

- **[reliability, highest value] Retry once on HTTP 401 with a fresh login inside `execute_plan`** — Phase 10 UAT measured ~4 of ~10 live `sync --apply` runs failing with a 401 that cleared on an immediate re-run, with no config/geo/credential change. Not endpoint-specific and not the geofence; the client has no retry, so users see spurious failures and a non-zero exit. Same signature as the Phase 8 mid-batch token-expiry incident.
- **[correctness] Placeholder-row reconciliation** — 58 rows created by `select_asset` calls whose upload never completed (no `uploaded_at`/`file_name`/`md5_hash`) are permanently stuck on the live frame: `delete_asset` returns 200 and removes nothing, `remove_asset` returns 404. These are also the cause of the `num_assets` (171) vs paginated-drain (149) mismatch that fails `tests/test_read_path.py::test_read_03_pagination` — measured as a server-side pagination inconsistency, not a client bug. Operational lesson already learned: never call `select_asset` with a `local_identifier` you do not intend to upload.
- **[correctness] Triage the 3 unresolved Phase 8 code-review findings** (see `08-REVIEW.md`): `AssetPartialId`'s cross-field validator is a no-op for the common construction path; `batch_update`'s partial-success response isn't validated against the requested id list; hardcoded `data_uti='public.jpeg'` will silently mis-tag/fail `.png`/`.heic` uploads (Pillow has no HEIC decoder in this environment). The `data_uti` finding is promoted from nice-to-have to load-bearing by this milestone — Google Photos albums routinely contain `.png` and `.heic`.

**Part 2 — Google Photos album sync (the headline)**

- **[integration] Link a Google account** — OAuth loopback flow with a browser on the same machine as `aura-cli`; refresh token persisted, secrets out of version control (same posture as `AURA_EMAIL`/`AURA_PASSWORD`).
- **[integration, ⚠️ unresolved mechanism] Discover Google Photos albums** — Google restricted the Photos Library API's broad read scope around March 2025, so enumerating a user's *own* albums may not be available to a general app. Research must settle the actual mechanism (true `albums.list` / Picker-API "choose once, remember it" / allowlist application) before this requirement can be written precisely. Highest-uncertainty item in the milestone.
- **[integration] Sync an album to a frame** — download the album to a local cache dir, then reuse the v2.0 content-hash diff/upload pipeline unchanged; prune the cache once uploads confirm. Reusing the proven pipeline is deliberate: almost all the risky code is already live-verified.
- **[integration] Mirror semantics** — the Google album is source of truth. Photos removed from the album are **hidden** on the frame via `exclude_asset`; re-adding to the album re-shows without re-uploading. Real deletion stays opt-in and exact-count-gated, exactly as v2.0 shipped it.
- **[integration] Many-to-many album↔frame mapping** — a persisted config file maps N albums to N frames and reconciles them in a single run.
- **[integration] Photos only, skipped videos reported** — frame-side `md5_hash` is null for every video asset, so content-hash diffing cannot see them. Skip video, print the count; never silent.

**Part 3 — carried debt**

- **[testing] Finish the "lift tests off the live network" slice** — candidates #2 (authenticated value) and #4 (injected config), carried since v1.1.
- **[hardening] Deferred code smells** — MOD-02 config-ize AWS pool IDs/bucket, MOD-03 typed exceptions, MOD-04 `Aura._init_logger()` loguru sink leak on repeated construction. Plus concurrent downloads on the **Google side only** — MOD-01's full async migration of the Aura client stays out of scope (see Out of Scope).

### Out of Scope

- Device-on-LAN / MITM traffic capture — deferred to a later reverse-engineering milestone
- Reversing the frame's own rendering process / firmware — later milestone
- SQS push-flow deep-dive (the TODO to map the real SQS behaviour) — later milestone
- Async migration of the HTTP client (MOD-01) — still not required; the sync client remains
  adequate even under batched writes, which are paced deliberately
  (`WRITE_CHUNK_DELAY_SECONDS`) rather than parallelised. Tracked as debt, not a goal.
  **v3.0 exception (user decision):** concurrent *downloads on the Google Photos side* are in
  scope, because that is the one place in this milestone where concurrency actually pays — it
  does not pull the Aura write client into an async rewrite.
- Video sync — `md5_hash` is null for all video assets on the live frame, so content-hash
  diffing cannot see them; would need a local-manifest fallback. Photos only, by design.
  Reaffirmed for v3.0: Google albums do contain video, and it is skipped with a reported count
  rather than silently dropped.
- Streaming Google Photos straight to S3 without touching disk — rejected for v3.0 in favour of
  a pruned local cache dir, so the already-live-verified v2.0 content-hash pipeline is reused
  unchanged rather than replaced by a new diff strategy.
- Removing the accumulated placeholder rows via the existing primitives — measured in Phase 10
  as impossible with `delete_asset`/`remove_asset`; needs a different mechanism (see Active).

## Context

### Current state (after v2.0, 2026-09-02)

- **Shipped v2.0** — `aura-cli` (`status`/`inspect`/`sync`/`push`), write path proven live,
  hide-by-default removal, proactive anti-abuse guard. **7,676 LOC Python** (`auraframes` +
  `tests`); 208 tests passing, 1 pre-existing failure (see below).
- **Shipped v1.0** — read path proven live against `api.pushd.com/v5`. ~1,760 LOC Python.
- **Shipped v1.1** — `Client`/`Aura` DI transport seam + offline `httpx.MockTransport`
  harness; ~1,942 LOC Python (`auraframes` + `tests` + `main.py`).
- **Tech stack:** Python 3.14 + `uv` (`pyproject.toml` + committed `uv.lock`), pydantic v2,
  httpx 0.28, boto3 1.43, Pillow 12. Dependency manifest migrated off the broken UTF-16
  `requirements.txt`.
- **Verification:** credential-gated pytest live suite (READ-01–04) that skips cleanly
  without creds; repo-root `VERIFICATION-REPORT.md` from a live run; `main.py` facade-only
  read-path demo that loads `.env`.
- **Post-verification hardening:** `main.py` loads a local `.env`
  (`python-dotenv` promoted to a runtime dep), and `Aura.login` now resolves credentials at
  call time rather than import time — fixing an HTTP 475 caused by Python's early-bound
  default arguments evaluating `os.getenv` before `load_dotenv()` ran.
- **Phase 4 (2026-07-04):** Landed architecture-review candidate #1 — an additive DI seam
  (`Client(transport=...)`, `Aura(client=...)`, closing the old `# TODO: Can probably use DI`)
  plus a reusable offline test harness (`tests/offline.py`, `httpx.MockTransport`-backed) and
  5 sanitized JSON fixtures. Most of `test_read_path.py`'s assertions (login headers, frame
  hydration, pagination drain, both error-raise mechanisms) now run offline with zero
  credentials/network; the live `@live` suite is byte-identical and remains the drift oracle.
  Code review flagged one pre-existing bug as a warning (not fixed here): `Aura._init_logger()`
  leaks loguru sinks/log files on repeated `Aura()` construction, amplified by the new
  per-test `offline_aura()` pattern. Candidates #2 (authenticated value) and #4 (injected
  config) remain open for a future phase to complete the "lift tests off the live network" slice.
- **Known still-open tech debt (deferred, not blocking):** hardcoded AWS pool IDs / bucket
  name, unguarded post-login state, silent `pass` on some API `error` fields, sync-only HTTP.
- **Phase 5 (2026-07-06):** Shipped the packaged `aura-cli` entrypoint with a `status`
  subcommand (config health, login, frame listing), offline-tested via the v1.1 DI seam.
  A live UAT pass flagged verbose loguru request/response noise leaking to stderr; closed
  in the same phase (05-02, gap closure) with a quiet-by-default `_configure_cli_logging()`
  helper and an opt-in `--debug` flag, re-confirmed live. Threat register (6 threats,
  T-05-01–05 + T-05-SC) fully mitigated/accepted — see `05-SECURITY.md`. A todo carries
  forward the idea of promoting `--debug` to a global flag once Phase 6 designs `inspect`.
- **Phase 6 (2026-07-06):** Shipped `aura-cli inspect --frame <name|id>` (frame resolution
  by name or ID, metadata + first-N photo listing), and folded the `--debug`-promotion todo
  into it (`--debug` is now a root-level `aura-cli` flag). Live spike (Success Criterion 4,
  hard Phase 7 dependency): ran `inspect --debug` against a real frame (106 paginated assets)
  and inspected the logged asset JSON — `md5_hash` is **populated** (non-null base64) for
  101/101 pre-existing photo (`.jpg`) assets, but **not populated** (null) for 5/5 video
  (`.mp4`) assets. Consequence for Phase 7: content-hash diffing via `md5_hash` is viable for
  photos with no fallback needed; a local-manifest/alternate-hash fallback is only required
  scope if video sync ever enters scope.
- **Phase 7 (2026-07-07):** Shipped the dry-run sync-diffing engine (`auraframes/sync.py`
  + `aura-cli sync <dir> --frame <name|id>`), structurally incapable of mutating (no
  `--apply`/`--yes` flag exists yet). Live validation (SYNC-02 success criterion 3, D-09
  precedent from Phase 6's `md5_hash` spike): ran `aura-cli sync ./data/ --frame "Cadre de
  Fabrice"` against a real frame — one local file with a matching original already on the
  frame was correctly classified "Unchanged" while a second, non-matching local file was
  correctly classified "To upload". This confirms the base64-MD5 convention is **byte-identical**
  between local `S3Client.get_md5(original_bytes)` hashing and the frame's reported
  `md5_hash`, making the dry-run diff engine's core content-hash matching assumption sound.
  Unblocks Phase 8 (the write/upload/delete path) to trust the diff without re-deriving
  the hash convention.
- **Phase 8 (2026-07-08) — v2.0 milestone complete:** Shipped `sync --apply`/`--yes`,
  the first mutating path in this codebase's ~3-year history. `execute_plan()` (the
  mutating counterpart to `compute_plan()`) uploads new local files (`select_asset` → S3
  → `batch_update`, via a new `AssetPartial` identity model) and removes gone-locally
  photos via `remove_asset`, with uploads-before-deletes ordering and per-item
  continue-past-failure. Fixed the hardcoded SQS frame-id bug (WRITE-04) and extended
  fail-loud error handling to all write/delete endpoints (WRITE-05). Live-verified
  against "Cadre de Fabrice": the upload round-trip, `remove_asset`, and a standalone
  `delete_asset` probe against a disposable asset all confirmed working as designed —
  `delete_asset` is asset-scoped (`DELETE /assets/{id}.json`, broader than `remove_asset`'s
  frame-scoped disassociation) and remains structurally unreachable from `--apply` (D-06).
  A live-verification incident (an operator run against a near-empty local directory
  triggered a 72-item delete plan against the standing test frame; 25 of 72 deletes hit a
  mid-batch auth-token expiry) validated WRITE-05/SYNC-04's fail-loud, continue-past-failure,
  non-zero-exit design under a real partial-failure condition — no data was lost (photos
  independently backed up) and a fresh re-run completed cleanly. Code review flagged 3
  unresolved critical findings (see Active, future-milestone candidates) that do not block
  this milestone's must-haves but should be triaged before further write-path work.
- **Phase 9 (2026-07-09):** Shipped `auraframes/ratelimit.py` — an injected-clock
  `WriteBudget` token bucket with JSON persistence (per account, reconciled against real
  anti-abuse trips) plus a fail-open `check_geo` pre-flight guard, wired into `execute_plan`
  and both `push --apply` and `sync --apply` as a true no-op when unconfigured. Root cause
  reframed during this phase: the persistent 401 write-lockout was diagnosed at the time as a
  VPN geo mismatch on top of a real ~42-write/~40-min request limit measured live.
  **Superseded by Phase 10's evidence — see below.** 19 new offline tests; a code-review
  blocker (the bucket over-refilled after a wait) was caught and fixed pre-completion.
- **Phase 10 (2026-08-25) — v2.0 milestone complete:** Shipped hide-by-default removal.
  A live 8-step probe against "Cadre de Fabrice" with disposable throwaways confirmed
  `exclude_asset` hides non-destructively (the asset REMAINS in `get_assets?filter=all`) and
  `select_asset` re-shows it — and **corrected the design mid-phase**: the visibility flag is
  `asset_settings[asset_id].selected`, a parallel array in the same response, **not**
  `Asset.selected`, which stayed `true` through every hide/re-show cycle. `compute_plan` became
  a 4-way classifier (re-show / unchanged / removal-candidate / already-hidden); `execute_plan`
  hides by default and can only reach the irreversible primitive if a caller names it; the CLI
  names the verb it will actually run and gates real deletion behind an exact-count prompt.
  Live drift fixed: the API stopped returning `Frame.smart_adds`, breaking hydration for every
  verb — patched to `Field(default_factory=list)` per the Phase 2 drift convention.
  **The "geofence" was disproven:** from a French residential IP the first `push --apply` still
  401'd, then the identical call succeeded minutes later and 11 subsequent writes all returned
  200. The 401 is transient auth-token expiry, not geo — remedy is retry with a fresh login.
  33 new offline tests; suite at 208 passed / 1 failed.
- **Known open defects (non-blocking, carried into the next milestone):** intermittent write
  401s (~4 in 10 runs, clears on retry — fails loud and safe, never silently skips work);
  58 unremovable placeholder rows (fails toward *not* deleting, so the destructive direction
  is safe); `test_read_03_pagination` asserting `drained == num_assets`, two counts the server
  itself does not keep consistent.

### Original baseline

- Codebase last touched April 2023; project mapped 2026-06-29 (see `.planning/codebase/`).
- Development machine runs Python 3.14.4; no virtualenv exists yet.
- `requirements.txt` is UTF-16 encoded, which can break tooling; pins are 2022-era
  (`pydantic~=1.10.4`, `pillow~=9.5.0`, `httpx==0.23.1`, `boto3==1.26.38`) and several
  will not build on Python 3.14.
- The model layer uses pydantic v1 `BaseModel` plus an `AllOptional` metaclass
  (`auraframes/models/meta.py`), so a v2 migration is non-trivial but bounded.
- Known code smells from the map: hardcoded AWS pool IDs / bucket name, unguarded
  post-login state, and silent error handling (`pass` on API `error` fields) — relevant
  because silent errors can mask API drift during verification.
- A live Aura account (credentials ready) is available to test against. An Aura frame is
  present on the local network but is not required for this cloud-API milestone.

## Constraints

- **Tech stack**: Python 3.14 with `uv` for env/dependency/interpreter management — user decision.
- **API**: Unofficial, reverse-engineered Aura/Pushd cloud API (`api.pushd.com/v5`) — undocumented and may change without notice; verification is inherently against a moving target.
- **Auth**: Requires live Aura account credentials (`AURA_EMAIL`/`AURA_PASSWORD`); secrets must stay out of version control.
- **Modernization scope**: Pragmatic — change only what's needed to run on 3.14 and prove the read path; avoid broad refactors.

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| Adopt `uv` as package manager | Fast, manages venv + deps + interpreter via `pyproject.toml`; replaces broken UTF-16 `requirements.txt` | ✓ Good — 37 packages resolved on 3.14, `uv.lock` committed for reproducible installs |
| Target Python 3.14 (not pin an older interpreter) | Stay on the installed runtime; accept the dep upgrades it forces | ✓ Good — all deps resolved to cp314 wheels, no sdist builds |
| Accept pydantic v1→v2 migration as a consequence of 3.14 | pydantic 1.10.4 won't build on 3.14; v2 is the supported path | ✓ Good — `AllOptional` → `make_partial` factory, `@validator` → `@field_validator`, guarded by an import smoke test |
| Done bar = read path only (login → list → download) | Smallest proof the client is alive; upload deferred | ✓ Good — read path proven live end-to-end; upload cleanly deferred to next milestone |
| Pragmatic modernization, not full cleanup | Goal is "verify where we are," not a rewrite | ✓ Good — fixed only what blocked running + masked drift (fail-loud transport, secret redaction); broad refactors left as tracked debt |
| Resolve login creds at call time, not import time | Early-bound default args evaluated `os.getenv` before `load_dotenv()`, sending null creds (HTTP 475) | ✓ Good — None-sentinel pattern + offline regression guard (debug `login-475-null-creds`) |
| Additive `Client(transport=...)` / `Aura(client=...)` DI seam, zero-arg-compatible | Closes the old DI TODO without breaking any existing caller (`main.py`, live tests) | ✓ Good — both constructors stay zero-arg; live suite byte-identical after the change |
| Fixture JSON authored entirely synthetic, not recorded from the live API | Safer sanitization posture — no real secret ever exists in a fixture to leak | ✓ Good — 5 fixtures pass model-hydration + fixture-validity tests |
| `run_status()` returns an int exit code, never calls `sys.exit`; `main()` is the sole `sys.exit` boundary | Mirrors the v1.1 `Aura(client=...)` DI seam so CLI handlers stay synchronously testable via `capsys` without invoking `load_dotenv()` or process exit | ✓ Good — Phase 5's offline test suite drives all three exit paths (missing creds / success / login failure) without subprocess spawning |
| Fix the verbose-loguru-stderr UAT gap from the CLI boundary, not `aura.py` | `Aura._init_logger()`'s `logger.remove()` is commented out and frozen (D-04); the CLI reconfigures loguru's sinks after `Aura()` construction instead of editing the frozen file | ✓ Good — quiet by default, `--debug` opt-in restores verbosity, file sink preserved in both modes; re-verified via real subprocess in 05-VERIFICATION.md |
| Safety-first roadmap ordering: phases 5-7 touch only already-live-verified read endpoints; all new write-path risk sequenced into Phase 8 | The write path had never run in three years; concentrate the risk where it can be prepared for, rather than spreading it | ✓ Good — by the time Phase 8 ran, the diff engine and frame resolution were already live-proven, so a write failure could only be a write failure |
| Dry-run is a *structural* default — separate `compute_plan()` / `execute_plan()` functions, not an `if apply:` branch | A flag can be inverted by a bug; a function that contains no mutating call cannot mutate | ✓ Good — Phase 7 shipped with no reachable mutating primitive at all (T-07-04), and the seam made `execute_plan` fully offline-testable with injected S3/SQS fakes |
| Content-hash diffing on `md5_hash`, never filename | Filenames are not stable identity; the API already exposes a hash | ✓ Good — confirmed live byte-identical to local `S3Client.get_md5` base64-MD5. ⚠️ Scoped to photos: `md5_hash` is null for all video assets |
| `remove_asset` (frame-scoped) is `--apply`'s destructive primitive; `delete_asset` (asset-scoped) left structurally unwired | Live probe proved `delete_asset` is broader — it removes the asset entirely, not just from the frame | ✓ Good — blast radius measured twice by inventory diff (Phase 8, re-confirmed Phase 10 at 158 → 157) |
| Proactive client-side `WriteBudget` token bucket + geo pre-flight, rather than reacting to 429/475 | An anti-abuse trip had already locked writes once; the same trip does not reliably announce itself with a distinguishable status code | ⚠️ Revisit — the budget is sound and the status-agnostic consecutive-failure backstop is the real win, but Phase 10 disproved the geo half of the diagnosis: the lockout was transient token expiry, not a geofence. `check_geo` is fail-open and harmless, but it solves a problem that turned out not to exist |
| Hide (`exclude_asset`) becomes `--apply`'s default removal mode; real deletion is opt-in and count-gated | The frame has no photo-count limit, so preservation is strictly safer — a mistaken sync should cost visibility, never photos (user decision) | ✓ Good — restoring a local file re-shows the photo without re-uploading it; reaching the irreversible tier now requires reading a number back |
| Trust live probes over model assumptions — verify the mechanism before building on it | Phase 10-01's probe caught that visibility lives in `asset_settings[].selected`, not `Asset.selected`, before 10-02 built on the wrong signal | ✓ Good — this is the third time a cheap live spike (Phase 6 `md5_hash`, Phase 7 hash format, Phase 10 visibility flag) redirected a design before it cost a rewrite |

## Evolution

This document evolves at phase transitions and milestone boundaries.

**After each phase transition** (via `/gsd-transition`):
1. Requirements invalidated? → Move to Out of Scope with reason
2. Requirements validated? → Move to Validated with phase reference
3. New requirements emerged? → Add to Active
4. Decisions to log? → Add to Key Decisions
5. "What This Is" still accurate? → Update if drifted

**After each milestone** (via `/gsd-complete-milestone`):
1. Full review of all sections
2. Core Value check — still the right priority?
3. Audit Out of Scope — reasons still valid?
4. Update Context with current state

---
*Last updated: 2026-09-03 after starting the **v3.0 Google Photos Album Sync** milestone.
v2.0 shipped 2026-09-02 as a verified closeout (6 phases, 17 plans, 160 commits, 51 days;
28/28 requirements, 0 open artifacts) and PR #1 merged to `master` on 2026-09-03. v3.0
sequences write-path reliability first, then Google account linking → album discovery →
album-to-frame mirroring via a pruned local cache, then the carried debt. Biggest open
unknown: whether Google's post-March-2025 Photos API still permits enumerating a user's own
albums, or whether album selection has to go through the Picker API.*

**Current Milestone:** v5.1 — Operations: Config, Sessions, Multi-frame & Scheduling (started 2026-09-29)

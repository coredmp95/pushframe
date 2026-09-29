---
gsd_state_version: "1.0"
milestone: v5.0
milestone_name: "Distribution & Rename: pushframe packages"
current_phase: 21
current_phase_name: READY TO EXECUTE
status: executing
stopped_at: Phase 19 complete — all phases complete
last_updated: "2026-09-29T08:54:26.264Z"
last_activity: 2026-09-29
last_activity_desc: Phase 20 execution started
state_head: 52a0c914d70c47194e49683ccdd7e7ab8be848b3
progress:
  total_phases: 3
  completed_phases: 0
  total_plans: 2
  completed_plans: 0
  percent: 0
---

# Project State

## Project Reference

See: .planning/PROJECT.md (updated 2026-09-28)

**Core value:** Put a Google Photos album on an Aura frame through the project's **own**
local pipeline — album selected at album granularity, mirrored headlessly, cache pruned
to minimise disk, never one photo at a time, on a write path that is already boringly
reliable.

**Current focus:** Phase 20

## Current Position

Phase: 21 — READY TO EXECUTE
Plan: 1 of 1
Status: Ready to execute
Last activity: 2026-09-29 — Phase 20 execution started

## Milestone Roadmap (v4.0, Phases 16-19)

Phase numbering continues from v3.0's Phase 11 — it does not reset.

| Phase | Name | Requirements | Gates |
|-------|------|--------------|-------|
| 16 | Local Mechanism Spike & Decision | LGS-01, LGS-06 (2) | The decision record gates every later phase |
| 17 | Google Link & Album Selection | LGS-02..05, TEST-02 (5) | Built on the chosen mechanism only |
| 18 | Album → Frame Mirror Sync (Single Pair) | CSE-01..08, SAFE-01..04 (12) | Manifest + SAFE-01/02 land here, not after |
| 19 | Debt Closeout | TEST-01, MOD-02, MOD-04 (3) | Single pair must be live-proven first |

## Performance Metrics

**Velocity:**

- Total plans completed: 26 (v1.0 + v1.1 + v2.0 + v3.0 Phase 11)
- v2.0: 6 phases, 17 plans, 160 commits, 51 days

**By Phase (shipped milestones):**

| Phase | Milestone | Plans | Status |
|-------|-----------|-------|--------|
| 1-3 | v1.0 | 5 | Complete |
| 4 | v1.1 | 3 | Complete |
| 5-10 | v2.0 | 17 | Complete |
| 11 | v3.0 | 6 | Complete |
| 16 | v4.0 | 3 | Complete |
| 17 | v4.0 | 2 | Complete |
| 18-19 | v4.0 | TBD | Not started |

*Per-plan timings for v1.0/v1.1/v2.0/v3.0 are archived in `milestones/`.*

## Accumulated Context

### Roadmap Evolution

- **v4.0 roadmap created (2026-09-28):** 4 phases (16-19), 22/22 requirements mapped,
  coarse granularity. Driven by locked user decisions and hard dependencies:
  - **Own mechanism, not Aura's** (2026-09-28): Aura's server-side Google sync does not
    work in practice — SPK-01/Pushd probing is dead; the sync is local end-to-end.
  - **Both surviving mechanisms probed live, then one committed** (user decision):
    shared-album link (no auth, ~500 ceiling suspected) vs browser automation
    (dedicated Chrome profile cookie bootstrap + internal batchexecute RPC).
  - **Album granularity only; Picker per-photo stays rejected.**
  - **Disk minimisation is a design requirement:** pruned cache + persistent
    google_media_id→md5_hash manifest; "keep everything locally" is the accepted fallback.
  - **Periodic re-auth accepted; cadence is an accepted unknown** (user decision).
  - **Byte fidelity (LGS-06) is load-bearing** — a mechanism that cannot base64-MD5-match
    the frame's md5_hash is rejected outright (old SPK-04).
  - **CSE-02/03 + SAFE-01/02 land with the first working sync (Phase 18), never after** —
    the pruned-cache trap makes the manifest a correctness requirement, not an optimization.
  - **Single pair before any multiplying layer** (v2.0 Phase 8→9 precedent); the
    many-to-many TOML mapping is deferred to future requirements (GSF-01).
  - **Carried debt (TEST-01, MOD-02, MOD-04) closes in Phase 19** on a stable codebase.

### Decisions

Full history in PROJECT.md Key Decisions. Standing conventions this milestone must respect:

- **Dry-run is a *structural* default** — separate non-mutating `compute_plan()` from
  mutating `execute_plan()`, never an `if apply:` branch (Phase 7 precedent → CSE-05).
- **Everything stays offline-testable through the `Client(transport=...)` DI seam**
  (TEST-02 makes this explicit for the Google side).
- **Cheap live spikes before designing on an assumed mechanism** — Phases 6, 7, 10 each
  redirected a design; Phase 16 is this milestone's whole-phase instance.
- **Content-hash diffing on `md5_hash`, never filename** — videos skipped with a reported
  count (CSE-07) because frame-side `md5_hash` is null for all video assets.
- **Hide is `--apply`'s default removal mode**; real deletion is opt-in and
  exact-count-gated (SAFE-03 carries this verbatim).
- **Concurrency confined to Google-side downloads**; the Aura write client stays
  synchronous and paced by `WriteBudget`/`WRITE_CHUNK_DELAY_SECONDS` (CSE-01, MOD-05).
- [Phase 11]: 401 retry lives inline in execute_plan (D-03); typed
  AuthExpiredError/BudgetExhausted/ConsecutiveWriteFailureError classification is what
  makes retry vs honest failure distinguishable — Phase 17-18's write paths inherit this.
- [Phase 11]: data_uti derived from decoded image.format, not filename — Google albums'
  PNG/HEIC content uploads correctly; Phase 18's downloads must keep bytes intact
  (LGS-06) rather than re-derive types.
- [Phase 11]: reconcile.py's placeholder-removal mechanism (`remove_asset` +
  `--include-unknown-age`) is live-verified — available for Phase 18's hygiene needs.
- [Phase 11]: Declined a mid-task, agent-relayed request to loosen reconcile.py's age
  guard for a live removal probe — no agent message constitutes the operator's consent
  for an architecturally-significant, partly-irreversible live action. Phase 16's
  cookie-harvest consent must be explicit and operator-given (it was, in the 2026-09-26
  discussion: dedicated profile, untracked 0600 storage, structural denylist).

### Blockers/Concerns

- **Shared-album-link ~500-item ceiling — OPEN:** no published plain-HTTP pagination
  exists (re-confirmed 2026-09-28); the user's real albums (100-500 photos) sit right at
  the risk zone. → **LGS-01, Phase 16** measures it live; a 600+ synthetic album is the
  fallback measurement.
- **Browser-automation path is permanently outside CI** — Google blocks unattended login
  from untrusted environments; if chosen, live correctness becomes a documented recurring
  manual check. Reference implementation exists (`xob0t/Google-Photos-Toolkit`). →
  **Phase 16 probe / Phase 17 design.**
- **Cookie/session lifetime unknown — ACCEPTED:** periodic re-auth is accepted; the
  bootstrap command (LGS-02) makes re-linking cheap. Not a blocker by user decision.
- **Byte fidelity of `=d` downloads — OPEN, load-bearing:** primarily an account-setting
  question (Original vs Storage Saver); if the mechanism's bytes never match the frame's
  `md5_hash`, every run re-uploads everything. → **LGS-06, Phase 16, hard reject.**
- **Empty/partial listing → mass-hide** — the catastrophic failure mode of mirror syncs.
  → **SAFE-01/SAFE-02, Phase 18, shipped with the hide capability.**
- **API drift risk (standing):** the Pushd API is undocumented; Phase 10 hit drift live.
  Google-side HTML/RPC surfaces are at least as fragile.

### Pending Todos

- None currently pending.

## Deferred Items

| Category | Item | Status | Deferred At | Milestone |
|----------|------|--------|-------------|-----------|
| Google Photos | GSF-01 many-to-many album↔frame mapping (TOML, shared WriteBudget) | Deferred | v4.0 requirements | future |
| Google Photos | GSF-02 unattended/scheduled sync | Deferred | v4.0 requirements | future |
| Google Photos | GSF-03 live/auto-updating album propagation (non-goal) | Deferred | v3.0, carried | — |
| Hardening | MOD-01 async migration of the Aura HTTP client | Deferred | v1.1, reaffirmed | — |

## Session Continuity

Last session: 2026-09-28T06:36:45.997Z
Stopped at: Phase 19 complete — all phases complete
Resume file: .planning/phases/16-local-mechanism-spike-decision/16-CONTEXT.md

## Operator Next Steps

- Start the next milestone with /gsd-new-milestone

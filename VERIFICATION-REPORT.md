# Aura Frames Python Client — Read-Path Verification Report

**Fresh run:** 2026-06-29
**Account:** «redacted» (email only — password / auth tokens never recorded)
**Verdict:** **Read path PROVEN end-to-end against `api.pushd.com/v5`** (login → list → fetch → download).

This is a standalone, developer-visible snapshot of what the revived client provably does
today. It **consolidates and cites** the raw evidence captured in
[`.planning/milestones/v1.0-phases/02-live-read-path-verification/02-LIVE-EVIDENCE.md`](https://github.com/coredmp95/pushframe/blob/master/.planning/milestones/v1.0-phases/02-live-read-path-verification/02-LIVE-EVIDENCE.md)
rather than replacing it.

---

## How this was verified

The four credential-gated live read-path tests were run against the live Aura API with real
account credentials (loaded from a local `.env` via python-dotenv; shell vars override):

```
uv run --extra dev pytest -m live -s
# 4 passed, 9 deselected in 5.53s   (2026-06-29)
```

`-s` surfaced the per-test detail used as evidence below:

```
«frame-name-redacted» («uuid-redacted»)
READ-03: drained 77 assets across pages of 38 (total=77)
READ-04: selected asset «uuid-redacted» via 'image+location_name' branch
READ-04: GPS IFD readable for location '«location-redacted»'
```

This 2026-06-29 Phase 3 refresh reproduces the Phase 2 result exactly (same "4 passed,
9 deselected" shape), confirming the read path still works today with no new drift. The
human-runnable equivalent is `uv run python main.py`. Raw evidence (request/response
detail) lives in
[`02-LIVE-EVIDENCE.md`](https://github.com/coredmp95/pushframe/blob/master/.planning/milestones/v1.0-phases/02-live-read-path-verification/02-LIVE-EVIDENCE.md).

---

## Per-step status (READ-01..READ-04)

| Req | Step | Status | Evidence |
|-----|------|--------|----------|
| READ-01 | `test_read_01_login` — login | **WORKING** | Login injected `x-token-auth` + `x-user-id` onto the shared httpx session |
| READ-02 | `test_read_02_list_frames` — list frames | **WORKING** | Listed frame **"«frame-name-redacted»"** (`«uuid-redacted»`) |
| READ-03 | `test_read_03_pagination` — fetch assets | **WORKING** | `get_all_assets(limit=38)` drained **77 assets across multiple pages**; `len(assets) == total == 77` (cursor branch exercised) |
| READ-04 | `test_read_04_download_exif` — download + EXIF | **WORKING** | Downloaded asset `«uuid-redacted»` via the `image+location_name` branch; `DateTimeOriginal` read back from disk; **GPS IFD readable** for location "«location-redacted»" |

---

## Live API drift discovered & repaired

Live verification surfaced four schema drifts versus the 2023-era models. All were fixed
pragmatically (Phase 2, commit `93409f5`) under the milestone's "change only what's needed
to prove the read path" constraint:

1. **`User` Optional fields** — the Phase 1 "add `= None`" fix reached `Frame`/`Asset` but
   missed `user.py`; the live API now omits `has_frame`, which a required-but-nullable field
   rejected. Added `= None` to all seven Optional `User` fields.
2. **`Feature` enum outgrown** — the live API returned
   `text_to_frame_four_hour_reminders`, absent from the closed enum. `Feature` now maps
   unknown values to **`UNKNOWN`** via `_missing_`, so a newly-added flag can't break
   hydration.
3. **`Asset.unglacierable` nullable** — the live API returns `null`; changed to
   `Optional[bool] = None`.
4. **Asset count moved** — `total_asset_count` is gone from the top level of
   `/frames/{id}.json`; it now lives at **`frame.num_assets`**. `get_frame` reads the new
   location with a fallback to the legacy key.

---

## Drift recorded but NOT fixed (deferred)

- **GPS lat/long swap** in `exif.build_gps_ifd` (accepted, deferred): the GPS IFD is still
  *readable* so READ-04 passes, but the written latitude/longitude coordinates are
  transposed (swapped). Fixing the swap is deferred to a later milestone; this milestone
  documents it only.

---

## Silent-error masking removed (trustworthiness — criterion 4)

The read path was hardened in Phase 2 so a real failure cannot hide behind a green result:

- **`raise_for_status`** added to all 4 `Client` methods (GET/POST/PUT/DELETE) — a failed
  HTTP response now raises instead of returning a falsely-green empty body.
- **Secret redaction** — a centralized recursive `_redact()` masks `password`,
  `auth_token`, and `x-token-auth` in all request/response logs. Verified against the fresh
  2026-06-29 live log: **`***REDACTED***` marker present; 0 unredacted `auth_token`
  lines; 0 plaintext password occurrences** (counts only — no raw log lines reproduced
  here).
- **EXIF-write failure now raises** — the bare-`except`-returning-empty-`BytesIO` path was
  removed, so a write failure can no longer slip through as a 0-byte saved image.
- **`frameApi.get_assets` raises on the `error` key** (no silent `pass`), so a drifted
  asset page can't return green.
- **Geocode failure tolerated + logged** — a Nominatim miss is logged and returns `None`
  (GPS simply absent) rather than crashing the download; the **Nominatim User-Agent** was
  made ToS-compliant.
- **`logs/` auto-created** so the loguru file sink does not raise `FileNotFoundError` on a
  clean checkout.

---

## Caveats

- **Pagination cursor** was exercised by passing a `limit` (38) strictly below the frame's
  total (77) — small accounts otherwise return a single page, so a deliberate small `limit`
  forces the cursor branch to run.
- **No retry/backoff** exists. Transient network errors or a Nominatim rate-limit/miss are
  **not** schema drift — distinguish a one-off network/geocode failure (re-run) from a
  Pydantic `ValidationError` (genuine model drift to repair).
- The **upload / device flow is unverified** this milestone (documented from code only).

---

## What is NOT verified

- **Upload round-trip (UP-01)** — `select_asset` → S3 → SQS → `batch_update` is documented
  from the 2023 code but not exercised against the live API.
- **Async migration** of the HTTP client (MOD-01) — the sync client is unchanged.
- **AWS pool / bucket configuration** (MOD-02) — hardcoded pool IDs untouched.
- **Typed exception hierarchy** (MOD-03) — not introduced.
- **The GPS lat/long swap fix** — deferred (see above).

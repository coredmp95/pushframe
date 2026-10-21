---
status: complete
kind: index
note: "Not a debug session. This is the durable knowledge-base index of RESOLVED sessions, read by gsd-debugger at Phase-0 recall. The `status` key exists only so scanDebugSessions() does not mis-report this index as an open session at milestone close."
---

# GSD Debug Knowledge Base

Resolved debug sessions. Used by `gsd-debugger` to surface known-pattern hypotheses at the start of new investigations.

---

## inspect-asset-null-fields — Asset model rejects mid-processing placeholder assets from the live API
- **Date:** 2026-07-08
- **Error patterns:** pydantic ValidationError, data_uti, file_name, taken_at, uploaded_at, height, width, upload_priority, good_resolution, field required, input should be a valid string, input should be a valid integer, Asset model, inspect crash, mid-processing, placeholder asset, unprocessed upload
- **Root cause:** The live Aura/Pushd API returns freshly-uploaded assets that are still mid-server-side-processing as placeholder records — source_id/local_identifier/user/selected populated, but processed content metadata (data_uti, file_name, dimensions, dates, upload_priority) null and good_resolution omitted entirely. The Asset pydantic model declared those 8 fields as required, so hydrating such an asset raised 8 ValidationErrors and crashed `aura-cli inspect`.
- **Fix:** Made the 8 affected fields (data_uti, file_name, good_resolution, height, width, upload_priority, taken_at, uploaded_at) Optional[...] = None on the Asset model; guarded the taken_at_dt property to return None instead of crashing on parse_aura_dt(None).
- **Files changed:** auraframes/models/asset.py, tests/fixtures/asset_unprocessed.json, tests/test_asset_unprocessed.py, tests/test_cli_inspect.py
---

## select-asset-401-unauthorized — Aura/Pushd anti-abuse trip on write bursts, escalating to login-475; plain-401 trips evade status-code-based detection
- **Date:** 2026-07-08
- **Error patterns:** 401 Unauthorized, select_asset, remove_asset, HTTP 475, "The email or password was incorrect", RateLimitError, Retry-After, consecutive write failures, account lockout, anti-abuse throttle, execute_plan, write burst, rate limit, 429
- **Root cause:** The Aura/Pushd API's anti-abuse layer trips on abnormal write/destructive request volume (first-ever live burst of select_asset/remove_asset/delete_asset in this codebase's history). It responds in stages: (1) 401 on all write (POST) endpoints while reads (GET) keep working, (2) escalates to reject even login with a non-standard HTTP 475 ("The email or password was incorrect.") despite valid credentials. Critically, the trip does NOT reliably announce itself with a distinguishable status code — a later live regression showed it as a run of plain HTTP 401s (indistinguishable from a genuine isolated per-item auth failure) rather than 429/475.
- **Fix:** Two-layer defense in `execute_plan`/`Client`. (1) Pace every write call (`WRITE_THROTTLE_SECONDS`, default 0.5s, injectable/disable-via-0) so bursts don't trip the layer. (2) Classify HTTP 429 and the custom 475 into a typed `RateLimitError` (parsed Retry-After, server message) that aborts the whole batch immediately with one clear message instead of N per-item failures. (3) BACKSTOP for trips that don't surface as 429/475: a status-code-agnostic consecutive-failure-run counter in `execute_plan` (`MAX_CONSECUTIVE_WRITE_FAILURES`, default 5, injectable/disable-via-0) spanning both the upload and delete loops, incrementing on ANY caught per-item write failure and resetting on any success — reaching the threshold raises a distinct `ConsecutiveWriteFailureError` and aborts, while a single isolated failure (surrounded by successes) never trips it.
- **Key lesson:** Do not rely on HTTP status code alone to detect a rate-limit/lockout condition — the same underlying anti-abuse trip can manifest as different status codes across attempts. A consecutive-failure-run heuristic (reset on success) is a useful status-agnostic backstop that composes cleanly below a status-code-based fast path.
- **Files changed:** auraframes/client.py, auraframes/sync.py, auraframes/cli.py, tests/test_client_rate_limit.py, tests/test_write_throttling.py, tests/test_cli_apply.py, tests/test_execute_plan.py
---


## cli-status-verbose-logging — duplicate loguru stderr sinks leak DEBUG request/response bodies to the CLI
- **Date:** 2026-07-06 (diagnosed) / 2026-09-02 (closed at v2.0)
- **Error patterns:** verbose CLI output, loguru, stderr noise, duplicate log lines, request/response bodies leaked, headers in terminal, cookies in terminal, logger.remove, _init_logger, double handler, --debug flag
- **Root cause:** `Aura._init_logger()` adds a formatted INFO stderr sink on every `Aura()` construction but never removes loguru's auto-registered default stderr handler (`logger.remove()` present but commented out). Two active stderr handlers then coexist: loguru's default (unrestricted level, passes the DEBUG full-body logs from `Client.*`) and the `_init_logger()` one (duplicates every request line and surfaces headers/query_params/data via `{extra}`). Any CLI command constructing a real `Aura()` renders both, interleaved with the CLI's own stdout `print()`s.
- **Fix:** Delivered by plan 05-02, not by the debug session (goal was find_root_cause_only). `_configure_cli_logging(debug)` in `auraframes/cli.py` drops every accumulated handler by default and restores a single WARNING-level stderr sink; `--debug` on the root parser is a no-op leaving `_init_logger()`'s sinks in place. Wired into `run_status`, `run_inspect`, `run_sync`. `_init_logger()` itself left unchanged per D-04.
- **Key lesson:** A library that configures loguru at object-construction time stacks a new sink per instance and never removes loguru's default — so log verbosity is a property of the *entry point*, not the library. Fix it at the CLI seam (drop-and-restore) rather than mutating shared library init. Tests asserting only on `capsys.readouterr().out` are blind to this entire class of bug; assert on `.err` too.
- **Files changed:** auraframes/cli.py, tests/test_cli_status.py

## breaker-counts-per-item-errors — lockout breaker tripped by per-item content failures (GIF) that never reached the network
- **Date:** 2026-10-21
- **Error patterns:** Aborted after 5 consecutive write failures, ConsecutiveWriteFailureError, Unsupported image format, GIF, WEBP, unreadable image, account lockout message, wait 60+ min, google-sync apply aborted, 0 items confirmed, _prep_upload, execute_plan, note_failure, breaker, per-item vs systemic
- **Root cause(s):** execute_plan's per-file prep loop fed every _prep_upload exception to the status-agnostic consecutive-failure breaker, including the LOCAL decode/format rejection raised before any S3/Pushd call; because a chunk's successes are only credited after its batch_update, any 5 unsupported items inside one 50-item chunk (adjacency not required) tripped it; AND (data) Google albums contain GIFs (Google Photos animations) outside JPEG/PNG/HEIF
- **Fix:** Error-classification seam in pushframe/sync.py — classify_write_failure(exc) -> FAILURE_PER_ITEM | FAILURE_SYSTEMIC via the _FAILURE_RULES table (unknown = systemic); _prep_upload wraps its whole local phase in ItemContentError(ValueError); per-item failures go to ExecutionResult.upload_skipped and neither increment nor reset the run; GIF reduced to first-frame JPEG with MPO (_FIRST_FRAME_AS_JPEG_FORMATS); gsync/cli/report name the skipped items ('item(s) SKIPPED' ERROR marker)
- **Files changed:** pushframe/sync.py, pushframe/gsync.py, pushframe/cli.py, pushframe/doctor.py, pushframe/report.py, tests/test_breaker_item_failures.py, tests/test_prep_upload_formats.py, tests/test_retry_401.py, tests/test_cli_apply.py, tests/test_gsync_execute.py, tests/test_report.py
- **Why not caught:** no gate existed for this class — the breaker's tests (tests/test_write_throttling.py) only fed it write-surface failures, and the select-asset-401-unauthorized design explicitly listed ValueError as counted, before Google items (content pushframe does not choose) entered the pipeline; tests/test_prep_upload_formats.py checked unsupported/undecodable files one at a time, never ≥MAX in one chunk
- **Recurrence guard:** tests/test_breaker_item_failures.py (test_unsupported_items_in_one_chunk_never_trip_the_breaker[MAX-1..2*MAX+2], test_interleaved_unsupported_items_in_one_chunk_never_trip, test_skips_neither_increment_nor_reset_the_systemic_run, test_run_of_plain_401_still_trips_with_unsupported_items_present, test_s3_upload_failures_stay_systemic, test_classify_write_failure_seam) + tests/test_gsync_execute.py::test_unsupported_items_are_skipped_named_and_never_abort_the_apply; key lesson: a status-agnostic backstop must still only count failures that REACHED the surface it protects — classify by "did this touch the network", never by exception type alone
---

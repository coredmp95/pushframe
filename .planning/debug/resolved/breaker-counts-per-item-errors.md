---
slug: breaker-counts-per-item-errors
status: resolved
trigger: "google-sync apply lockout breaker aborts on 5 consecutive per-item 'Unsupported image format: GIF' errors — 0 written, wrong lockout message"
created: 2026-10-21
updated: 2026-10-21
---

# Debug: lockout breaker fed by per-item format errors

## Symptoms

- **Expected:** a per-item, non-systemic failure (e.g. a Google album item in GIF format that pushframe cannot upload) is skipped, listed in the end-of-run report as a per-item failure, and the run continues. Only systemic signals (HTTP 401/475 runs, rate-limit cut-offs) feed the "N consecutive write failures" lockout breaker.
- **Actual:** run 2026-10-20T00:11:03+02:00 on host venus (pushframe 5.1.38, `google-sync "Nous" --frame "Cadre Salon" --apply --yes --scheduled`, plan 2231 upload / 287 re-show / 186 hide) aborted after 1004s with 0 items confirmed.
- **Error:** `google-sync failed: apply aborted: Aborted after 5 consecutive write failures -- this is the signature of an account lockout or a systemic cut-off (...) Last error: Unsupported image format: GIF (AF1QipNZ5dNKCtprVLfKMeV4J4ipujWDoQrByEeykx1K)` + the "wait 60+ min" lockout advice — wrong for this cause.
- **Timeline:** any album containing ≥5 GIFs adjacent in plan order. The next night's run (same album) did not abort on GIFs (ordering/plan differed), so it is order-dependent.
- **Repro (offline):** plan with 5 consecutive GIF items through the apply path → abort with lockout message.

## Expected fix shape

- Classify errors: per-item/content errors (unsupported format, unreadable image, etc.) → skip + record, reset nothing systemic, never trip the breaker; systemic errors → breaker as today.
- Consider converting GIF → JPEG (first frame) if cheap and consistent with D-09 (original bytes untouched for HEIC); otherwise skip+list clearly so the user knows which items were not mirrored, and make sure the skipped items don't make every future run re-try/re-report forever in a noisy way (or document that they will).
- Keep offline suite green: `uv run --extra dev pytest -q -m "not live"`.
- NOTE: a sibling session `s3-expiredtoken-long-run` touches the same breaker/error-classification area (ExpiredToken must not feed the breaker either). Coordinate: share one classification seam if natural.

## Current Focus

status: RESOLVED -- fix applied, offline guardrail accepted (user pre-approved; offline verification is the acceptance gate)
bug_class: Bohrbug (deterministic given the plan's sorted-path chunk layout)
next_action: none for this session -- sibling s3-expiredtoken-long-run extends the classification seam (see Resolution.seam)

reasoning_checkpoint:
  hypothesis: "execute_plan's per-file prep except-branch feeds EVERY _prep_upload exception to note_failure(); a ValueError('Unsupported image format: GIF') is raised by the LOCAL decode step before any S3/Pushd call, so it carries zero information about the account, yet 5 of them inside one chunk's prep window (successes are only credited after the chunk's batch_update, so they need not even be adjacent) raise ConsecutiveWriteFailureError with the lockout wording."
  confirming_evidence:
    - "scratchpad repro: 5 GIFs + 1 JPEG -> ConsecutiveWriteFailureError count=5, 0 S3 uploads, message identical to venus ('Last error: Unsupported image format: GIF (AF1Qip04)')"
    - "scratchpad repro2: G J G J G J G J G in ONE chunk -> ABORT count=5, 4 JPEGs already S3-uploaded but never select_asset'd, 0 succeeded -- adjacency is not required, 5 unsupported items within one 50-item chunk suffice"
    - "code: sync.py L863-867 `except Exception as e: ... note_failure(str(e))`; KB select-asset-401-unauthorized documents ValueError as deliberately counted"
  falsification_test: "If after the fix 5+ unsupported items in one chunk still raise ConsecutiveWriteFailureError, or if a systemic failure (select_asset 401 run / S3 upload_file raising) no longer trips the breaker, the fix is wrong."
  fix_rationale: "The breaker exists to stop hammering the WRITE SURFACE. The root cause is the absence of a classification between 'the item's own bytes could not be prepared (never reached the network)' and 'a write failed'. A single predicate classify_write_failure(exc) -> 'per-item' | 'systemic' fixes it at the one place exceptions are item-attributable (the prep loop): per-item failures are recorded in a new ExecutionResult.upload_skipped list, reported by name, and neither increment nor reset the run counter. GIF -> first-frame JPEG (existing MPO path) removes the dominant real-world trigger."
  blind_spots: "Why the next night's run (same album) did not trip on GIFs is not verified offline (likely album changes shifting the 50-item chunk boundaries so <5 unsupported items share a chunk). GIF transparency is flattened by convert('RGB') (palette colour of the transparent index), not composited. Local `pushframe sync` of a mislabeled GIF/MPO '.jpg' re-uploads every run (pre-existing MPO behaviour: no manifest on that path)."
  candidate_causes:
    - "code: breaker counts pre-network content failures (CONFIRMED)"
    - "data: Google albums legitimately contain GIF (Google Photos 'animations') and other formats outside JPEG/PNG/HEIF -- a contributing condition pushframe does not control"
    - "config: MAX_CONSECUTIVE_WRITE_FAILURES=5 / WRITE_BATCH_SIZE=50 -- not a cause; the threshold is right for systemic runs, the chunk-deferred success crediting only widens the window"
  and_gate: "yes -- the abort needs (a) the classification gap in code AND (b) >=5 unsupported items inside one chunk's prep window (data). Fixing (a) alone is sufficient and necessary; GIF conversion reduces (b) but is not relied upon for correctness (WebP/BMP/TIFF/corrupt files remain per-item skips)."

## Evidence

- timestamp: 2026-10-21
  checked: knowledge-base.md (Phase 0)
  found: KB match select-asset-401-unauthorized -> the breaker (MAX_CONSECUTIVE_WRITE_FAILURES=5) was DESIGNED status-agnostic, "incrementing on ANY caught per-item write failure (plain 401, 5xx, network error, ValueError)". The ValueError inclusion was an explicit design choice, made before Google items (whose content pushframe does not choose) entered the pipeline.
  implication: not a regression of a guard; a classification gap in the original design. The fix must keep the status-agnostic backstop for anything that touched the network, only exempting failures that provably never reached Pushd/S3.

- timestamp: 2026-10-21
  checked: pushframe/sync.py _prep_upload (L540-583) + execute_plan upload loop (L853-867)
  found: _prep_upload raises ValueError('Unsupported image format: {fmt} ({name})') for any decoded format outside JPEG/PNG/HEIF (MPO is reduced to first-frame JPEG). The decode + format check happen BEFORE s3_client.upload_file -- the failure is purely local. The except Exception branch records it, then note_failure(str(e)) increments consecutive_failures; 5 in a row raise ConsecutiveWriteFailureError(count, last_error, result).
  implication: matches the venus message exactly ("Last error: Unsupported image format: GIF (AF1Qip...)": staged google cache files are named by media id, no suffix).

- timestamp: 2026-10-21
  checked: pushframe/gsync.py run_google_sync except Exception branch (L903-922) + end-of-run summary (L968-979)
  found: any execute_plan exception prints "apply aborted: {e}" + the generic 60-min anti-abuse wait advice. The normal end-of-run summary only prints a COUNT "N upload(s) FAILED -- no manifest entry written; they will retry next run" -- no item names, and "will retry" is misleading for content that can never upload.
  implication: content skips need their own named listing in the summary (and the report.py ERROR marker must still fire).

- timestamp: 2026-10-21
  checked: pushframe/google/cache.py download_to_cache/prune_cache
  found: only manifest-backed ids are skipped/pruned; an id with no manifest entry is re-downloaded every run (overwritten in place).
  implication: a permanently-unsupported item is re-downloaded + re-decoded + re-reported every run (Google bandwidth only; zero Pushd/S3 writes since the check precedes upload_file). Converting GIF removes the main recurring case.

- timestamp: 2026-10-21
  checked: MPO precedent in _prep_upload (5.1.10) + tests/test_gsync_execute.py::test_mpo_upload_persists_the_uploaded_bytes_md5_not_the_container
  found: a reduced container (MPO -> first frame re-encoded JPEG q95, data_uti public.jpeg) is already supported end to end: result.uploaded_md5_by_path carries the re-encoded md5 and gsync persists THAT md5 in the manifest so the next run does not trip drift.
  implication: GIF -> first-frame JPEG reuses an existing, live-proven path (no new write surface: honest JPEG bytes + public.jpeg). D-09 (HEIC bytes untouched) is not contradicted -- HEIC/JPEG/PNG passthrough stays byte-exact; only formats the frame cannot take are reduced.

- timestamp: 2026-10-21
  checked: offline repro (scratchpad repro.py) -- execute_plan, 5 GIF + 1 JPEG, fakes, no network
  found: ConsecutiveWriteFailureError count=5, S3 uploads=0, message "... Last error: Unsupported image format: GIF (AF1Qip04)" -- identical shape to venus.
  implication: reproduced deterministically; the breaker tripped with ZERO network calls made.

- timestamp: 2026-10-21
  checked: offline repro2 -- one chunk ordered G J G J G J G J G
  found: ABORT count=5; 4 JPEGs S3-uploaded (orphaned, never select_asset'd), 0 succeeded.
  implication: successes are credited only after the chunk's batch_update, so ANY 5 unsupported items within one 50-item chunk trip the breaker -- adjacency is not required. Explains the order/plan dependence across nights (chunk boundaries move when the album changes).

- timestamp: 2026-10-21
  checked: fix applied (pushframe/sync.py seam + GIF reduction; gsync/cli/doctor/report reporting) + full offline suite
  found: tests/test_breaker_item_failures.py 14/14 green; full suite `uv run --extra dev pytest -q -m "not live"` 839 passed, 0 warnings (DeprecationWarning=error). Two pre-existing tests asserted an undecodable/missing file lands in upload_failures; updated to the new contract (upload_skipped) with intent preserved (one bad file costs exactly one file, zero network calls).
  implication: fix verified offline; see Resolution.verification for the per-signal record.

## Eliminated

## Resolution

root_cause: "execute_plan's per-file prep except-branch (pushframe/sync.py) fed EVERY _prep_upload exception to note_failure(), the status-agnostic lockout breaker. _prep_upload's LOCAL decode step rejects decoded formats outside JPEG/PNG/HEIF (GIF here) with a ValueError raised before any S3/Pushd call -- a failure that carries zero information about the account. Because a chunk's successes are only credited after its batch_update, any 5 such items inside one 50-item chunk (adjacency not required) raised ConsecutiveWriteFailureError with the account-lockout wording; the gsync abort path then printed the 60-min anti-abuse advice. AND-gate: (code) no per-item vs systemic classification + (data) Google albums containing >=5 GIFs within one chunk's prep window."

fix: |
  1. Error-classification seam (pushframe/sync.py, right below MAX_CONSECUTIVE_WRITE_FAILURES):
     FAILURE_PER_ITEM / FAILURE_SYSTEMIC constants, ItemContentError(ValueError), the _FAILURE_RULES table and
     classify_write_failure(exc) -> str (first matching rule wins; no match = FAILURE_SYSTEMIC, the conservative default).
  2. _prep_upload wraps its whole LOCAL phase (Image.open, format check, reduction, read_bytes) so every failure there
     is an ItemContentError ('Unsupported image format: X (name)' unchanged; other decode errors become
     'Unreadable image (name): <Type>: <msg>'). s3_client.upload_file errors are NOT wrapped -> stay systemic.
  3. execute_plan's prep loop consults classify_write_failure: FAILURE_PER_ITEM -> ExecutionResult.upload_skipped
     (new field, disjoint from upload_failures), progress(False) + on_error as before, and `continue` -- the run
     counter is neither incremented nor reset. Everything else unchanged (systemic -> upload_failures + note_failure).
  4. GIF -> first frame re-encoded as JPEG q95 / public.jpeg, via the existing MPO path
     (_FIRST_FRAME_AS_JPEG_FORMATS = {'MPO','GIF'}). Cheap (frame 0 only is decoded), no new write surface (honest
     JPEG bytes), D-12 intact (no fallback UTI), D-09 intact (JPEG/PNG/HEIF passthrough stays byte-exact).
     uploaded_md5_by_path carries the re-encoded md5 and gsync already persists THAT in the manifest (MPO contract),
     so the next run sees the GIF as held -- no drift, no re-upload.
  5. Reporting: gsync end-of-run prints '{n} item(s) SKIPPED -- content pushframe cannot upload (nothing was sent;
     NOT on the frame; re-checked and re-reported every run until removed from the album):' + one line per item
     (_gid_label + redact_tokens); report.py gains the 'item(s) SKIPPED' ERROR marker; `pushframe sync` prints a
     'Skipped:' block and still exits 1 (the file did not land); doctor's write-probe error falls back to
     upload_skipped.

seam: |
  Name: classify_write_failure(exc) -> str, in pushframe/sync.py (block "Error-classification seam: per-item vs
  systemic", just below MAX_CONSECUTIVE_WRITE_FAILURES). Classes: FAILURE_PER_ITEM ('per-item'),
  FAILURE_SYSTEMIC ('systemic', default). Rule table: _FAILURE_RULES = ((predicate, class), ...).
  Consulted today at ONE call site: the per-file prep loop of execute_plan's upload chunk (`except Exception as e:`
  right after `_prep_upload`) -- the only place an exception is attributable to a single item.
  How to add a class (e.g. sibling s3-expiredtoken-long-run):
    a. define FAILURE_<NAME> = '<name>' next to the two existing constants;
    b. add a predicate (e.g. _is_expired_aws_credentials(exc): botocore ClientError whose
       exc.response['Error']['Code'] in {'ExpiredToken','ExpiredTokenException','RequestExpired'}) and append
       (predicate, FAILURE_<NAME>) to _FAILURE_RULES BEFORE any broader rule (first match wins);
    c. handle the class at the call site(s): in the prep loop add a branch next to the FAILURE_PER_ITEM one
       (e.g. refresh S3 credentials + retry _prep_upload once; never call note_failure). For the SQS
       receive_message inside the chunk try (whose exceptions currently land in the chunk-level generic
       `except Exception as e:` and attribute ALL prepped files + note_failure), route that branch through
       classify_write_failure too, or catch the poll's error locally (it is best-effort/observational).
    d. pin it with a row in tests/test_breaker_item_failures.py::test_classify_write_failure_seam.
  Invariant to keep: an unknown exception MUST stay FAILURE_SYSTEMIC (mutant M5 below proves the test bites).

gif_and_skip_noise: |
  GIF is converted (not skipped). Remaining per-item skips (WebP -- deliberately deferred by D-12 --, BMP, TIFF,
  RAW, zero-byte/truncated/non-image bytes) get NO manifest entry, so EVERY run re-downloads them from Google,
  re-decodes them locally (no S3, no Pushd write, no breaker effect; the chunk's budget tokens are acquired
  before prep as before) and re-lists them under 'item(s) SKIPPED'. With a scheduled job at report level ERROR
  that means an ATTENTION email every night until the item leaves the album -- the same cadence the pre-existing
  'upload(s) FAILED' marker already had for isolated failures, now honestly worded. Deliberate (the frame is not a
  full mirror and a retry cannot fix it); a quieter 'report once' would need a separate skip-list state (NOT the
  manifest, whose entries MEAN "bytes on the frame" and would trip the drift gate) -- left as a follow-up.

verification:
  target_test: { result: pass, detail: "tests/test_breaker_item_failures.py 14/14; RED before the fix (12 failed: ConsecutiveWriteFailureError 'Last error: Unsupported image format: GIF/WEBP' + missing seam/GIF reduction; the 2 pre-fix passes are guards: plain-401 run still trips, progress/on_error contract)" }
  mutation_check: { result: pass, reason_if_skipped: "no Stryker/mutmut in this Python repo -> 6 manual mutants at the fix site, driving test file only", mutant_killed: "M1 empty _FAILURE_RULES, M2 skip calls note_failure, M3 GIF not reduced, M4 local-phase wrap removed, M5 classifier default per-item, M6 skip resets the run -- all 6 KILLED" }
  no_op_deletion: { result: pass, deletion_justified_by_rca: true, detail: "diff is additive; the only removed lines are the MPO-only branch condition (generalized to a frozenset) and the bare ValueError raise (now its ValueError subclass)" }
  adjacent_tests: { result: pass, suites_run: ["full offline suite -m 'not live': 839 passed, 0 warnings"] }
  revert_and_reconfirm: { result: pass, bug_returned_on_revert: true, fixed_on_reapply: true, detail: "git diff -- pushframe/ saved as a patch, checkout -> 12 failed / 2 passed; git apply -> 14 passed" }
  oracle_type: specified (symptom spec: per-item skip recorded + run continues; systemic runs still abort at exactly MAX), boundary neighbours MAX-1 / MAX / MAX+1 / 2*MAX+2
  guardrail_verdict: accepted

files_changed:
  - pushframe/sync.py
  - pushframe/gsync.py
  - pushframe/cli.py
  - pushframe/doctor.py
  - pushframe/report.py
  - tests/test_breaker_item_failures.py
  - tests/test_prep_upload_formats.py
  - tests/test_retry_401.py
  - tests/test_cli_apply.py
  - tests/test_gsync_execute.py
  - tests/test_report.py

out_of_scope_findings:
  - "Pre-existing: local `pushframe sync` has no manifest, so a reduced container (an MPO -- or now a mislabeled GIF -- named .jpg/.jpeg) uploads every run (local md5 != frame md5 of the re-encoded bytes) and hides the previous copy. Not touched here."
  - "GIF transparency is flattened by convert('RGB') (the transparent palette index takes its palette colour); Google Photos animations are opaque."
  - "CHANGELOG.md [Unreleased] entry not written (this repo commits changelog entries separately)."

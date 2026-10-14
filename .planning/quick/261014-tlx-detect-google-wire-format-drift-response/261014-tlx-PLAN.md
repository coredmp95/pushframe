---
phase: quick-261014-tlx
plan: 01
type: execute
wave: 1
depends_on: []
files_modified:
  - pushframe/google/wire.py
  - pushframe/google/parsers.py
  - pushframe/google/enumerate.py
  - pushframe/gsync.py
  - pushframe/report.py
  - pushframe/cli.py
  - tests/conftest.py
  - tests/test_google_wire.py
  - tests/test_google_enumerate.py
  - tests/test_gsync_execute.py
  - tests/test_gsync_all.py
  - tests/test_gsync_engine.py
  - tests/test_cli_google_album.py
  - tests/test_report.py
  - docs/ERRORS.md
  - docs/CLI.md
autonomous: true
requirements: [TLX-1, TLX-2, TLX-3, TLX-4]
gap_closure: false

estimate:
  tokens: 85000
  raw_tokens: 85000
  tasks: 3
  confidence: low

must_haves:
  truths:
    - "TLX-1: every google-sync run whose album listing passes the SAFE-01 listing gates computes a privacy-safe wire fingerprint with up to four fields — inner (top-level shape of the page-1 snAcKc inner payload, e.g. [null,list,str,list[32-63],null,int]), cursor (path:prefix/length-bucket of the page-1 cursor, e.g. structural:AKFE/len128-255, recorded only when page 1 was a FULL page), item (first media item's parsed slots, e.g. [str,[str,int,int],int]) and albums (the resolved /albums ds:5 row length bucket, e.g. row[8-15]) — built from value kinds, power-of-two length buckets and at most the first 4 cursor characters; it never contains ids, URLs, titles or a full cursor."
    - "TLX-1: the fingerprint of the last good run is stored per sync target under the state dir — ~/.local/state/pushframe/pairs/<pair>/google-wire.json for --pair/--all runs, ~/.local/state/pushframe/google-wire/album-<first 16 hex of sha1(album target)>.json otherwise — as a 0600 file in 0700 directories with an atomic tmp+os.replace write. The first run with nothing stored stores it silently; a later run whose fingerprint differs in a field present in both prints exactly one stdout line starting `WARNING: Google response format changed:` naming each changed field old → new and asking to check for a pushframe update. It warns once per change (the new value is stored), even when the run succeeds, and never changes the exit code."
    - "TLX-2: when a google-sync run fails on an EnumerateError (every kind, including a ProbeParseError converted by _snackc_page) or on a SAFE-01 listing gate (empty / truncated / below-metadata), and when google-album fails on an EnumerateError, the existing `… failed:` line is still printed FIRST, followed by a `Diagnostics (Google wire shape only — no ids, links or titles):` block. The block gives one line per snAcKc answer (item count, cursor shape, note: null-payload retry / tokenless retry / failure kind), the run's fingerprint, the last good fingerprint and the changed fields (google-sync only), and the album metadata count against the listed count with the shortfall tolerance. Every line goes through redact_tokens."
    - "TLX-3: a per-target streak {failure_class, consecutive_failures} is persisted in the same state file. The classes are enumerate:<kind> for EnumerateError and listing:empty / listing:truncated / listing:below-metadata for the SAFE-01 listing gates; frame-side and manifest errors are never counted. A run whose listing passes the gates resets the streak. The first failure of a class prints `note: first \"<class>\" failure in a row …` saying it may be a transient Google answer. The second and later consecutive failure of the SAME class prints `ATTENTION: probable Google protocol change — pushframe likely needs an update: \"<class>\" failed N runs in a row …`, plus the fingerprint diff against the last good run when there is one. Manual and scheduled runs read and update the same key and print the same lines."
    - "TLX-1/TLX-3 report rule: report.py treats `WARNING: Google response format changed` and `probable Google protocol change` as problem markers, so `--report ERROR` sends on them. build_subject reads `FAILED — probable Google protocol change — pushframe likely needs an update` (or ATTENTION when rc == 0) whenever the escalation marker is in the output, at every level. Otherwise, with rc == 0 and the format-change marker present, it reads `ATTENTION — Google response format changed (check for a pushframe update)` at every level. Outputs carrying neither marker get the exact subjects they get today."
    - "TLX-4: a missing, unreadable, malformed or unwritable wire-state file never crashes or alters a sync. It is treated as absent and reported with one `note:` line. check_listing_trust, run_google_sync_plan, build_demand, compute_plan and the format_* helpers stay pure (no wire-state access). The SAFE gates, the retry budgets and every existing message text are unchanged. There are no new dependencies and no edits to the version, CHANGELOG or uv.lock. docs/ERRORS.md and docs/CLI.md document every new line and the subject rule. The full offline suite `uv run --extra dev pytest -q -m \"not live\"` is green (734 pre-existing tests plus the new ones, DeprecationWarnings still errors)."
  artifacts:
    - path: pushframe/google/wire.py
      provides: "STATE_ROOT_OVERRIDE / state_root / state_path_for, render_fingerprint / fingerprint_diff / merge_fingerprint, WireWatch (on_listing_ok / on_failure / last_good, tolerant private atomic state), failure_class, format_diagnostics, FORMAT_CHANGE_MARKER / DIAGNOSTICS_HEADER / ESCALATION_MARKER"
    - path: pushframe/google/parsers.py
      provides: "_len_bucket, wire_shape, media_item_shape, cursor_signature; SnackcPage.inner_shape / item_shape / cursor_shape (defaulted); AlbumSummary.row_shape (defaulted)"
    - path: pushframe/google/enumerate.py
      provides: "PageTrace; AlbumListing.pages / fingerprint; EnumerateError.kind / pages / fingerprint; _snackc_page returns SnackcPage and records traces"
    - path: pushframe/gsync.py
      provides: "run_google_sync wire_state_path kwarg (pair loop passes the pair key), WireWatch calls at the trust gate and at the three Google-failure print sites; SafeSyncError.kind"
    - path: pushframe/report.py
      provides: "two new _PROBLEM_MARKERS entries and the escalation / format-change subject rules"
    - path: tests/test_google_wire.py
      provides: "shape, privacy, state, diagnostics and streak tests for the wire watch"
    - path: tests/conftest.py
      provides: "autouse fixture pointing wire.STATE_ROOT_OVERRIDE at a fresh tmp dir per test (no test touches the real ~/.local/state)"
  key_links:
    - from: pushframe/google/parsers.py parse_snackc_payload
      to: pushframe/google/enumerate.py enumerate_album
      via: "SnackcPage.inner_shape / item_shape / cursor_shape of the FINAL page-1 answer (after any null or tokenless retry) become listing.fingerprint"
    - from: pushframe/gsync.py run_google_sync (after check_listing_trust passes)
      to: pushframe/google/wire.py WireWatch.on_listing_ok
      via: "listing.fingerprint + albums row shape -> compare/store -> printed WARNING line -> stdout -> job log + --report email"
    - from: pushframe/gsync.py run_google_sync failure sites (list_shared_albums / enumerate_album EnumerateError, check_listing_trust SafeSyncError)
      to: pushframe/google/wire.py format_diagnostics + WireWatch.on_failure
      via: "the existing `google-sync failed:` line, then the diagnostics block, then the note / ATTENTION line"
    - from: pushframe/report.py build_subject / should_send
      to: "the WARNING / ATTENTION lines printed by gsync"
      via: "literal markers pinned by tests/test_report.py against the wire.py constants"
    - from: pushframe/gsync.py pair loop
      to: pushframe/google/wire.py state_path_for(pair=name)
      via: "wire_state_path kwarg of the recursive run_google_sync call"
---

<objective>
Make the next silent Google Photos wire-format change obvious from the nightly report alone. The 2026-10-13/14 incident is the model: the snAcKc cursor moved from `AH_…` to `AKFE…` at `inner[2]`. The SAFE gates kept prod safe, but the failure emails said "transient" and diagnosis took live probes.

The user approved three design points. They are locked and cited below as DP-1..DP-3:
- DP-1 (TLX-1): a privacy-safe structural fingerprint for each run. The last good fingerprint is persisted per job/album under the state dir. On a change, one WARNING line lands in the run output, the job log and the report email, even when the run succeeds, and the report level/subject escalates it. The first run stores silently.
- DP-2 (TLX-2): when an EnumerateError / ProbeParseError / SAFE-01 listing gate fires, a compact diagnostics block with no user data follows the failure line.
- DP-3 (TLX-3): a persisted counter of consecutive failures, keyed by failure class. A success resets it. The first failure is described as possibly transient. Two or more consecutive failures of the same class print `probable Google protocol change — pushframe likely needs an update`, in both the message and the email subject. Scheduled runs are the main consumer; manual runs share the key without being noisy.
- Constraints (TLX-4): compute_plan / run_google_sync_plan stay pure. State writes are atomic and private (0600 file, 0700 dir). State corruption never crashes a sync. No new dependencies. Email behavior is unchanged apart from the new lines and the subject escalation. The SAFE gates and retry logic are untouched. Docs are updated. No version or CHANGELOG change.

Claude's discretion, decided here so the executor does not have to:
- Fingerprint encoding: value kinds, a collapsed `item+` run, and power-of-two length buckets (Google's jspb arrays trim trailing nulls, so exact lengths would churn). Lists that hold media items carry no length (page sizes vary). The cursor is reported as path:prefix/bucket, and only for a FULL page 1 (a short single page legitimately has no cursor). Fields are compared only when both the stored and the current value are present, and the stored value is merged field by field.
- State key: the pair name for --pair/--all (the state file lives in the pair's state dir); otherwise sha1 of the stripped album target, so the raw target never appears in a path.
- "Success" means the Google listing passed every SAFE-01 listing gate. That resets the streak and stores the fingerprint, whatever happens later on the frame side.
- Streak model: one {class, count} pair. The same class increments it; a different class restarts it at 1.
- Subject rule: escalation outranks everything. A format change outranks the OK summary when rc == 0, and a failure subject outranks a format change when rc != 0.

Output: the new module `pushframe/google/wire.py`, small extensions to parsers, enumerate, gsync, report and cli, red-first offline tests, and updated docs.
</objective>

<execution_context>
@~/.claude/gsd-core/workflows/execute-plan.md
@~/.claude/gsd-core/templates/summary.md
</execution_context>

<context>
@.planning/STATE.md
@CLAUDE.md
@.planning/quick/261014-swa-read-the-snackc-continuation-cursor-stru/261014-swa-SUMMARY.md
@pushframe/google/parsers.py
@pushframe/google/enumerate.py
@pushframe/gsync.py
@pushframe/report.py

Interfaces the executor builds on (existing, verified 2026-10-14 at HEAD 2dea192):
- `pushframe/google/parsers.py`
  - `_is_media_item(node) -> bool` (line 157).
  - `_has_structural_cursor_slot(inner)` (line 221).
  - `SnackcPage(items, continuation_token)` dataclass (line 230).
  - `parse_snackc_payload(payload_str) -> SnackcPage` (line 244): the structural cursor comes from `inner[2]`, with the `_legacy_cursor` fallback.
  - `AlbumSummary(album_id, title, share_url=None, item_count=None)` (line 331); `_walk_album_summaries` reads `entry` with `len(entry) >= 9` (line 347).
- `pushframe/google/enumerate.py`
  - `EnumerateError(RuntimeError)` (line 97).
  - `AlbumListing(items, page_count, exhausted_cleanly, album_id)` (line 101).
  - `_snackc_page(...)` currently returns `(items, next_token)` (line 163, sole caller `enumerate_album`).
  - `enumerate_album(session, album_id, *, page_key, max_pages, expected_count)` (line 228).
  - `list_shared_albums` (line 309).
  - `listing_shortfall_tolerance(n)` (line 78).
  - `PAGE_SIZE` is read at call time (tests monkeypatch it).
- `pushframe/gsync.py`
  - `SafeSyncError` classmethods (lines 78-137).
  - `check_listing_trust` (line 140).
  - `run_google_sync(... pair=, run_all=, scheduled=)` (line 343): the pair loop is at 437-469; album resolution, enumeration and the trust gate are at 496-540.
- `pushframe/report.py`
  - `_PROBLEM_MARKERS` (line 41).
  - `should_send` (line 125).
  - `build_subject` (line 141).
- `pushframe/cli.py`
  - `run_google_album` (line 1428): its enumerate failure print is near line 1499.
- `pushframe/utils/io.py`
  - `ensure_private_dir(path, *, tighten=False)`.
- `pushframe/utils/names.py`
  - `safe_name_problem(name, *, what)`.
- `pushframe/google/redaction.py`
  - `redact_tokens(text)`.
- Atomic private write pattern to mirror: `GoogleManifest.save` in `pushframe/google/manifest.py` (os.open O_CREAT 0o600 on a `.tmp` sibling, then os.replace, then chmod 0600).
- Test fakes:
  - `tests/test_google_enumerate.py` `_GoogleRouter(pages, wire="legacy"|"akfe", null_positions=, tokenless_positions=, rpc_status=, rpc_body=, token_on_last_page=)`.
  - `tests/test_gsync_execute.py` has its own `_GoogleRouter(metadata_count=, listing_ns=)` serving a legacy `[[items]]` single page plus a 9-element ds:5 row.
  - `tests/test_gsync_all.py` `cfg_path` fixture monkeypatches `pairs.pair_state_paths` into tmp.
  - `tests/test_report.py` constants `OK_OUT`, `FAIL_OUT`, `HIDE_OUT`, `TRIP_OUT`.
- Baseline: `uv run --extra dev pytest -q -m "not live"` gives 734 passed, 8 deselected in ~19 s.
</context>

<tasks>

<task type="tracer" tdd="true">
  <name>Task 1 (tracer): wire fingerprint end to end — parser shapes, listing.fingerprint, per-target state, WARNING line, report ATTENTION (TLX-1, DP-1)</name>
  <files>pushframe/google/parsers.py, pushframe/google/enumerate.py, pushframe/google/wire.py, pushframe/gsync.py, pushframe/report.py, tests/conftest.py, tests/test_google_wire.py, tests/test_google_enumerate.py, tests/test_gsync_execute.py, tests/test_gsync_all.py, tests/test_report.py</files>
  <read_first>
    - pushframe/google/parsers.py lines 157-284 (`_is_media_item`, `_walk_items`, `_has_structural_cursor_slot`, `SnackcPage`, `parse_snackc_payload`) and 331-407 (`AlbumSummary`, `_walk_album_summaries`)
    - pushframe/google/enumerate.py lines 97-117 and 163-306 (`AlbumListing`, `_snackc_page`, `enumerate_album`)
    - pushframe/gsync.py lines 343-541 (pair loop, album resolution, enumeration, trust gate)
    - pushframe/report.py lines 37-70 and 125-154
    - pushframe/google/manifest.py lines 97-113 (atomic private save to mirror) and pushframe/utils/io.py lines 113-147
    - tests/test_google_enumerate.py lines 30-208 (router + akfe helpers); tests/test_gsync_execute.py lines 28-115 and 236-275; tests/test_gsync_all.py lines 25-60; tests/test_report.py lines 85-210
  </read_first>
  <behavior>
    RED first: commit `test(quick-261014-tlx): red tests for the Google wire fingerprint and format-change warning`, after running the new tests and confirming they FAIL. Then GREEN: commit `feat(quick-261014-tlx): fingerprint the Google wire per run and warn on a format change`.

    tests/conftest.py adds an autouse fixture `_isolate_google_wire_state(monkeypatch, tmp_path_factory)`. It imports `pushframe.google.wire` inside the fixture and sets `wire.STATE_ROOT_OVERRIDE` to a fresh `tmp_path_factory.mktemp("wire-state")`, so no test ever writes the real ~/.local/state and no state leaks between tests. Put this fixture in the GREEN commit together with wire.py: at RED the module does not exist yet, and an autouse import would error all 734 existing tests instead of only the new ones.

    tests/test_google_wire.py (new):
    - test_wire_shape_akfe_fixture: parse the snAcKc entry of tests/fixtures/google_rpc_snackc_page_akfe.json with `parse_batchexecute` + `parse_snackc_payload`. Expect `inner_shape == "[null,list,str,list[32-63],null,int]"`, `item_shape == "[str,[str,int,int],int]"` and `cursor_shape == "structural:AKFE/len128-255"`.
    - test_wire_shape_legacy_fixture: tests/fixtures/google_rpc_snackc_page.json gives `inner_shape == "[item+,list[1]]"`, the same item_shape, and `cursor_shape == "legacy:AH_/len32-63"`.
    - test_cursor_shape_end_and_none: a structural inner with `""` at [2] gives `"structural:end"`; a legacy `[[item]]` with no AH_ gives `"legacy:none"`.
    - test_len_bucket, parametrized: 0→"0", 1→"1", 2→"2-3", 3→"2-3", 9→"8-15", 42→"32-63", 226→"128-255".
    - test_snackc_page_positional_construction_keeps_defaults: `SnackcPage([], None)` has inner_shape None, item_shape None and cursor_shape "legacy:none".
    - test_album_summary_row_shape: `parse_album_summaries` over a ds:5 sample with the 9-element entry used by tests/test_gsync_execute.py gives `row_shape == "row[8-15]"`; `AlbumSummary(album_id="x", title="t").row_shape is None`.
    - test_fingerprint_never_carries_user_data: build a live-shape inner `[None, items, CURSOR, META, None, n]`. The items have AF1Qip ids and lh3.googleusercontent.com URLs, META holds the title "Vacances Corse", and CURSOR is "AKFE" + 222 chars. Neither the rendered fingerprint nor any individual shape contains "AF1Qip", "http", "googleusercontent", "Vacances" or CURSOR[4:24].
    - test_render_diff_merge: `render_fingerprint` orders the fields inner, cursor, item, albums, skips None and renders `(none)` for None/empty. `fingerprint_diff` lists only fields present in both and different, as (field, old, new). `merge_fingerprint` keeps the old value where the new one is None.
    - test_state_path_for: with the conftest root R, `pair="cadre"` gives R/pairs/cadre/google-wire.json. `pair="../x"` gives None. `album_target=" Cadre "` and `"Cadre"` give the same R/google-wire/album-<16 lowercase hex>.json, and the name does not contain "Cadre". A falsy target gives None.
    - test_state_root_default_is_home_at_call_time: set `STATE_ROOT_OVERRIDE` to None and HOME to tmp; `state_root()` is tmp/.local/state/pushframe.
    - test_watch_first_run_stores_silently: `WireWatch(path).on_listing_ok(fp)` returns []. The file has mode 0o600, a directory it created has mode 0o700, and the JSON carries version 1 and fp.
    - test_watch_same_fingerprint_is_silent; test_watch_changed_field_warns_once: the second call with a different inner returns exactly one line. It starts with "WARNING: Google response format changed:" and contains "inner [old] → [new]" (the literal old and new values) and "check for a pushframe update". A third call with the new fp returns []. A fresh WireWatch on the same path proves the state was persisted.
    - test_watch_absent_field_not_compared_and_kept: stored has a cursor, the new fp has cursor None. There is no warning and the stored cursor is kept.
    - test_watch_corrupt_state_is_absent_with_one_note, parametrized over file contents: raw bytes b"\xff\xfe", "[]", '{"version": 2}', a fingerprint value containing a newline, consecutive_failures true. `on_listing_ok` returns exactly one line starting "note: Google wire-format state" with no WARNING line, never raises, and leaves a valid version-1 file behind.
    - test_watch_unsaveable_state_never_raises: STATE_ROOT_OVERRIDE points under a regular FILE, so mkdir fails even as root. Exactly one line starts "note: could not save Google wire-format state" and nothing raises.
    - test_watch_disabled: `WireWatch(None).on_listing_ok(fp) == []`.

    tests/test_google_enumerate.py:
    - test_listing_fingerprint_akfe: `_GoogleRouter([300, 300, 157], wire="akfe")` gives `listing.fingerprint == {"inner": "[null,list,str,list[32-63],null,int]", "item": "[str,[str,int,int],int]", "cursor": "structural:AKFE/len128-255"}`.
    - test_listing_fingerprint_legacy: `[300, 300, 194]` gives inner "[list,list[1]]" and cursor "legacy:AH_/len32-63".
    - test_listing_fingerprint_short_single_page_has_no_cursor: `[157]` akfe gives `fingerprint["cursor"] is None`.
    - test_listing_fingerprint_comes_from_the_retry_answer: akfe, tokenless_positions={1}, expected_count=757 gives cursor "structural:AKFE/len128-255", not "structural:end".

    tests/test_gsync_execute.py: add a `wire` knob ("legacy" default | "akfe") to this file's `_GoogleRouter`. akfe answers the same items as the live inner `[None, items, "", META42, None, len(items)]`.
    - test_wire_fingerprint_silent_first_then_warns_once: run 1 (legacy, dry run) gives rc 0, no "WARNING: Google response format changed", and exactly one file matching `album-*.json` under `wire.state_root() / "google-wire"`, mode 0o600. Run 2 (wire="akfe") gives rc 0 and exactly one line starting with the WARNING prefix that names "inner"; "Plan:" still appears after it. Run 3 (akfe) has no WARNING.
    - test_failing_listing_stores_no_fingerprint: metadata_count=757 gives rc 1. Either no state file exists or its fingerprint is empty.
    - test_unsaveable_wire_state_never_changes_the_sync: STATE_ROOT_OVERRIDE points under a regular file. rc 0, the "note: could not save Google wire-format state" line is present and "Plan:" is printed.

    tests/test_gsync_all.py:
    - test_pair_run_keeps_wire_state_in_the_pair_dir: with the `cfg_path` fixture, `_pair("cadre-venus", "Cadre", "Cadre de Fabrice")` and a --pair dry run. `wire.state_root() / "pairs" / "cadre-venus" / "google-wire.json"` exists and no `google-wire/album-*.json` was created.

    tests/test_report.py:
    - test_error_marker_google_format_change: `should_send("ERROR", 0, "WARNING: Google response format changed: inner a → b — …\n" + HIDE_OUT)` is True. One test per marker, the file's convention.
    - test_subject_format_change_is_attention_at_every_level: for DEBUG/INFO/ERROR with rc 0, the WARNING line plus HIDE_OUT gives a subject containing "ATTENTION — Google response format changed". HIDE_OUT alone at INFO still gives "OK — Applied: 111 uploaded, 2 hidden, 0 re-shown". With rc 1 and FAIL_OUT plus the WARNING line the subject still starts "[pushframe] nightly: FAILED — reading frame assets failed: 401".
    - Also pin that the report.py literal equals `wire.FORMAT_CHANGE_MARKER`.
  </behavior>
  <action>
    pushframe/google/parsers.py (DP-1):
    1. Add `_len_bucket(n)`: "0" for 0, "1" for 1, otherwise "lo-hi" with lo the largest power of two that is at most n (`1 << (n.bit_length() - 1)`) and hi = 2*lo - 1.
    2. Add `_kind(node)`. It returns "null", "bool" (checked before int), "int", "float", "str", "dict", or for a list: "item" when `_is_media_item(node)`, "list" (no length) when any direct child is a media item, otherwise "list[<bucket>]".
    3. Add `wire_shape(node)`. A list renders as "[" + comma-joined child kinds + "]", with a run of consecutive "item" tokens collapsed to one "item+"; any other node renders as its kind.
    4. Add `media_item_shape(node)`. Find the first media item depth-first (the same order as `_walk_items`) and return "[k0,[b0,b1,b2],k2]": the kinds of node[0], of base[0..2] (a missing base slot renders "-"), and of node[2]. Return None when there is no media item.
    5. Add `cursor_signature(raw)`: "<prefix>/len<bucket>", where prefix is "AH_" for an AH_ cursor (legacy cursors carry payload from char 4) and raw[:4] otherwise. It is only ever called on a cursor that already passed `_CURSOR_RE` / `AH_TOKEN_RE`, so the prefix is URL-safe.
    6. Give SnackcPage defaulted fields: `inner_shape: str | None = None`, `item_shape: str | None = None`, `cursor_shape: str = "legacy:none"`. `parse_snackc_payload` fills them:
       - `inner_shape = wire_shape(inner)` and `item_shape = media_item_shape(inner)`.
       - cursor_shape is "structural:<signature>" or "structural:end" on the structural path, and "legacy:<signature>" or "legacy:none" on the fallback.
       Cursor selection, validation and error texts are unchanged.
    7. Give AlbumSummary a defaulted field `row_shape: str | None = None`. `_walk_album_summaries` sets it to "row[<bucket of len(entry)>]".

    pushframe/google/enumerate.py:
    8. `_snackc_page` returns the `SnackcPage`, not the tuple. Update the sole caller `enumerate_album` to read `page.items` / `page.continuation_token`, including in the `_tokenless_full_short` closure, with identical semantics. Do not touch `_retry_budget`, the null-payload retry, the tokenless retry, the short-page terminal rule, the page cap or the call-time PAGE_SIZE read.
    9. AlbumListing gains `fingerprint: dict | None = None`. In `enumerate_album`, right after the tokenless-retry block (so it is the FINAL page-1 answer) and before the early returns, set it once when `listing.page_count == 1`. The dict has inner = page.inner_shape, item = page.item_shape, and cursor = page.cursor_shape only when `len(page.items) >= PAGE_SIZE`, else None. Update the module docstring with a short "Wire fingerprint" bullet.

    pushframe/google/wire.py (new, stdlib + pushframe helpers only; no new dependency per TLX-4):
    10. Module docstring: purpose (early warning for silent Google protocol changes), privacy posture (kinds, buckets and a cursor prefix of at most 4 chars only), the state locations and JSON schema, and the tolerance rules.
    11. Constants: `FINGERPRINT_FIELDS = ("inner", "cursor", "item", "albums")`, `FORMAT_CHANGE_MARKER = "WARNING: Google response format changed"`, and `RELEASES_URL = "https://github.com/coredmp95/pushframe/releases"`.
    12. Paths:
       - `STATE_ROOT_OVERRIDE: Path | None = None` (patched by tests).
       - `state_root()` returns the override or `Path("~/.local/state/pushframe").expanduser()`, resolved at call time and expanded on the same line (the B1 tilde guard test).
       - `state_path_for(*, pair=None, album_target=None)`. For a pair: None when `safe_name_problem(pair, what="pair name")` reports a problem, else root/pairs/<pair>/google-wire.json. For an album target: None when it is falsy, else root/google-wire/album-<sha1(target.strip().encode()).hexdigest()[:16]>.json.
    13. Pure helpers `render_fingerprint(fp)`, `fingerprint_diff(old, new)` and `merge_fingerprint(old, new)`, with the semantics pinned in the behavior block.
    14. `class WireWatch(path)`. A path of None means disabled: every method returns [] / None.
       - Lazy tolerant `_load()`. A missing file is fresh state.
       - Any OSError / ValueError / UnicodeDecodeError / schema violation also gives fresh state, plus ONE queued note: "note: Google wire-format state {path} was unreadable ({reason}) — starting fresh; the sync itself is unaffected". The reason is an exception class name or a short schema reason, never file content.
       - The schema is closed. Keys are a subset of {version, fingerprint, failure_class, consecutive_failures, updated}, and version == 1. fingerprint is a dict whose keys are a subset of FINGERPRINT_FIELDS, with str values of at most 200 printable-ASCII chars and no control characters. failure_class is None or a str of at most 64 chars matching [a-z0-9:-]+. consecutive_failures is an int, not a bool, in 0..10**6. updated is a str.
       - `_save(state)`: `ensure_private_dir(path.parent)`, write a `.tmp` sibling via os.open(O_WRONLY|O_CREAT|O_TRUNC, 0o600) + fchmod 0600, os.replace, then chmod 0600, mirroring GoogleManifest.save. Any OSError queues ONE note: "note: could not save Google wire-format state {path} ({ErrClass}) — the sync itself is unaffected". It never raises.
       - `on_listing_ok(fingerprint) -> list[str]`:
         - With no stored fingerprint, store the merge silently.
         - Otherwise compute the diff. When it is non-empty, return one line: "{FORMAT_CHANGE_MARKER}: " + "; ".join("{field} {old} → {new}") + " — the listing still passed every safety gate; check for a pushframe update: {RELEASES_URL}".
         - Store the merged fingerprint, stamp `updated`, save, and return the queued notes followed by the lines.
       - `last_good() -> dict | None`.

    pushframe/gsync.py:
    15. `run_google_sync` gains `wire_state_path=None`.
       - The pair loop forwards `wire_state_path=wire.state_path_for(pair=name)` to the recursive call. pair_state_paths has already validated the name.
       - Outside pair mode, a None value defaults to `wire.state_path_for(album_target=album_target)`.
       - Build `WireWatch(path)` right after the pair branch. Import inside the function like the other google imports.
    16. Directly after `check_listing_trust(...)` passes:
       - Copy `getattr(listing, "fingerprint", None) or {}`.
       - Set "albums" to `getattr(album, "row_shape", None)`. When that is None, borrow it from the listed album with the same album_id, exactly as metadata_count is borrowed.
       - Print every line returned by `watch.on_listing_ok(fp)`.
       check_listing_trust, run_google_sync_plan, build_demand and the format_* helpers stay untouched (TLX-4 purity).

    pushframe/report.py (DP-1: the report escalates the warning):
    17. Append `'WARNING: Google response format changed'` to `_PROBLEM_MARKERS`, with a comment naming wire.py's WireWatch.on_listing_ok as the printer.
    18. In `build_subject`, before the existing `rc != 0 or level == 'ERROR'` branch: when rc == 0 and that marker is in the output, return "{prefix}ATTENTION — Google response format changed (check for a pushframe update)" at every level. Every other path is unchanged.

    19. Run the listed test modules, then the full offline suite.
  </action>
  <verify>
    <automated>uv run --extra dev pytest -q -m "not live" tests/test_google_wire.py tests/test_google_enumerate.py tests/test_google_parsers.py tests/test_gsync_execute.py tests/test_gsync_all.py tests/test_gsync_engine.py tests/test_report.py</automated>
  </verify>
  <acceptance_criteria>
    - `git log --oneline` shows the `test(quick-261014-tlx): …` RED commit before the `feat(quick-261014-tlx): …` GREEN commit, and the new tests failed at the RED commit.
    - `uv run --extra dev pytest -q -m "not live" tests/test_google_wire.py` passes.
    - `uv run --extra dev pytest -q -m "not live" tests/test_gsync_execute.py -k "wire or fingerprint or unsaveable"` passes.
    - `grep -c "STATE_ROOT_OVERRIDE" tests/conftest.py` prints at least 1.
    - `grep -c "WARNING: Google response format changed" pushframe/report.py` prints at least 1 and `grep -c "FORMAT_CHANGE_MARKER" pushframe/google/wire.py` prints at least 2.
    - Every pre-existing test in the seven listed modules passes with its body unchanged.
  </acceptance_criteria>
  <done>A google-sync run stores a privacy-safe wire fingerprint for its pair/album. The first run stores it silently. A later change prints one WARNING line that reaches stdout, the job log and the report email: ERROR level sends, and the subject says ATTENTION at every level. Corrupt or unwritable state prints one note and never changes the sync.</done>
</task>

<task type="auto" tdd="true">
  <name>Task 2: compact wire diagnostics after every Google listing failure (TLX-2, DP-2)</name>
  <files>pushframe/google/enumerate.py, pushframe/google/wire.py, pushframe/gsync.py, pushframe/cli.py, tests/test_google_enumerate.py, tests/test_google_wire.py, tests/test_gsync_execute.py, tests/test_gsync_engine.py, tests/test_cli_google_album.py</files>
  <read_first>
    - pushframe/google/enumerate.py as left by Task 1 (every `raise EnumerateError(` site, `_snackc_page`, `enumerate_album`, `list_shared_albums`)
    - pushframe/gsync.py lines 78-158 (SafeSyncError classmethods, check_listing_trust) and the three Google failure print sites in run_google_sync (list_shared_albums, enumerate_album, check_listing_trust)
    - pushframe/cli.py lines 1428-1530 (run_google_album)
    - tests/test_cli_google_album.py lines 70-160 (router/session helpers); tests/test_gsync_engine.py lines 1-72 (SimpleNamespace listing helpers)
  </read_first>
  <behavior>
    RED first: commit `test(quick-261014-tlx): red tests for Google listing failure diagnostics`. Then GREEN: commit `feat(quick-261014-tlx): print wire diagnostics after a Google listing failure`.

    tests/test_google_enumerate.py:
    - test_enumerate_error_kinds, parametrized. Each case gives the expected `exc.value.kind`, and the existing message assertions still hold:
      - `rpc_status=400` → "http"
      - `rpc_body=")]}'\nnot json at all\n"` → "parse"
      - an rpc_body whose only wrb.fr entry has rpcid "other" → "envelope"
      - akfe `null_positions={1, 2}` → "null-payload"
      - the malformed AKFE cursor body from test_enumerate_malformed_cursor_is_a_named_enumerate_error → "parse"
      - `[300] * 5` with max_pages=2 → "page-cap"
      - album_id "" → "album-id"
    - test_list_shared_albums_http_kind: an inline MockTransport answers GET /albums with 500, giving kind "albums-http".
    - test_listing_pages_trace_akfe: `[300, 300, 157]` akfe gives `[(p.post, p.items, p.cursor) for p in listing.pages] == [(1, 300, "structural:AKFE/len128-255"), (2, 300, "structural:AKFE/len128-255"), (3, 157, "structural:end")]`, all notes empty.
    - test_listing_pages_trace_null_retry: akfe `null_positions={2}` gives 4 entries. Entry 2 has items 0, cursor "-" and a note containing "null payload"; entry 3 has items 300.
    - test_listing_pages_trace_tokenless_retry: akfe `tokenless_positions={1}`, expected_count=757. Entry 1 has 300 items and cursor "structural:end". Entry 2's note contains "tokenless" and its cursor is "structural:AKFE/len128-255".
    - test_enumerate_error_carries_pages_and_fingerprint: akfe `[300, 300, 157]` with `null_positions={2, 3}` raises EnumerateError with kind "null-payload". `exc.pages[0].items == 300`, the last entry's note starts "failed:", and `exc.fingerprint["inner"] == "[null,list,str,list[32-63],null,int]"`.

    tests/test_google_wire.py:
    - test_format_diagnostics_full: pass pages [PageTrace(1, 300, "structural:AKFE/len128-255"), PageTrace(2, 300, "structural:end", "tokenless retry")], a fingerprint, a last_good that differs in cursor, metadata_count=757 and listed=300. Expect:
      - lines[0] == wire.DIAGNOSTICS_HEADER
      - a line "  page 1: 300 item(s), cursor structural:AKFE/len128-255"
      - a page-2 line ending "— tokenless retry"
      - lines starting "  fingerprint:" and "  last good run:"
      - a line starting "  changed since the last good run: cursor "
      - the line "  album metadata: 757 item(s), listed: 300 (shortfall tolerance 16)"
    - test_format_diagnostics_edges:
      - no pages → "  pages: none fetched"
      - last_good None → "(none stored)"
      - equal fingerprints → "  unchanged since the last good run"
      - `show_last_good=False` → no "last good run" line
      - 20 pages → the first 6, a "  … 9 more page(s)" line, then the last 5
    - test_format_diagnostics_redacts: a note carrying a full AF1Qip id and a 226-char AKFE cursor renders neither the id body nor the cursor body.

    tests/test_gsync_engine.py:
    - test_safe_sync_error_kinds: "listing:empty", "listing:truncated", "listing:below-metadata", "frame:empty-listing", "manifest:drift", and "other" for a bare `SafeSyncError("x")`.
    - test_pure_half_never_touches_wire_state (TLX-4): monkeypatch `pushframe.google.wire.WireWatch.__init__` and `pushframe.google.wire.state_root` to raise AssertionError. check_listing_trust and run_google_sync_plan over the file's SimpleNamespace listings still return or raise exactly as today.

    tests/test_gsync_execute.py:
    - test_below_metadata_failure_prints_diagnostics: metadata 757 gives rc 1.
      - The "google-sync failed: album metadata says 757" line comes before the DIAGNOSTICS_HEADER line.
      - The output contains "  page 1: 3 item(s), cursor legacy:none" and "  album metadata: 757 item(s), listed: 3".
      - The output contains neither "googleusercontent" nor the full ALBUM_ID.
    - test_tokenless_twice_failure_diagnostics_show_both_answers: with PAGE_SIZE 3 and metadata 757 there are two page lines, and the second notes the tokenless retry.
    - test_enumerate_failure_prints_diagnostics: a router variant whose snAcKc POST answers HTTP 500 shows the failure line, then the header, then a page line whose note is "failed: http 500".
    - test_frame_side_failure_prints_no_wire_diagnostics: `_FakeAura([])` gives "google-sync failed: frame asset listing is EMPTY" and no DIAGNOSTICS_HEADER.

    tests/test_cli_google_album.py:
    - test_google_album_enumerate_failure_prints_diagnostics: a session whose snAcKc POST answers HTTP 500 gives rc 1, "google-album failed:" followed by the header, a page line noting "failed: http 500", and no "last good run" line.
  </behavior>
  <action>
    pushframe/google/enumerate.py (DP-2):
    1. Add the dataclass `PageTrace(post: int, items: int, cursor: str, note: str = "")`. AlbumListing gains `pages: list[PageTrace]` (default_factory list).
    2. EnumerateError gets an `__init__(self, message="", *, kind="other")` that calls `super().__init__(message)` so `str(exc)` is unchanged. It sets `self.kind`, `self.pages = []` and `self.fingerprint = None`.
    3. Tag every raise site with its kind, changing no message text:
       - `_session_scalars` → "session-scalars"
       - HTTP != 200 → "http"
       - no snAcKc entry → "envelope"
       - persisting null payload → "null-payload"
       - the ProbeParseError conversion → "parse"
       - the page cap → "page-cap"
       - a missing album_id → "album-id"
       - the list_shared_albums HTTP failure → "albums-http"
    4. `_snackc_page` gains a keyword `trace: list | None = None` and appends one PageTrace per POST answer, with `post = len(trace) + 1`:
       - a null answer that will be retried → items 0, cursor "-", note "null payload (retried)"
       - the final parsed page → `len(page.items)` and `page.cursor_shape`
       - any failure → items 0, cursor "-", note "failed: <kind>", with "failed: http <status>" for the HTTP case; appended before raising
    5. In `enumerate_album`:
       - Pass `trace=listing.pages`.
       - After the tokenless re-issue, append "tokenless retry" to the last trace entry's note.
       - Wrap everything from `_session_scalars` through the page-cap raise in `except EnumerateError as exc`: set `exc.pages = list(listing.pages)` and `exc.fingerprint = listing.fingerprint`, then re-raise with a bare `raise` (same object, same chaining).
       - The album_id check stays before the wrapper.

    pushframe/gsync.py:
    6. SafeSyncError gains a class attribute `kind = "other"`. The classmethods set it: "listing:empty", "listing:truncated", "listing:below-metadata", "frame:empty-listing" and "manifest:drift". No message text changes.
    7. At each Google failure print site in run_google_sync, keep the existing `google-sync failed:` print FIRST, then print every line of `wire.format_diagnostics(...)`:
       - list_shared_albums EnumerateError: pages=[], fingerprint None, last_good=watch.last_good(), metadata_count None.
       - enumerate_album EnumerateError: pages and fingerprint from the exception via getattr with defaults, last_good, metadata_count.
       - check_listing_trust SafeSyncError, only when `e.kind` starts with "listing:": `listing.pages`, `listing.fingerprint`, last_good, metadata_count, listed=len(listing.items).
       The SafeSyncError raised later by run_google_sync_plan (frame/manifest) prints no diagnostics.

    pushframe/google/wire.py:
    8. Add `DIAGNOSTICS_HEADER = "Diagnostics (Google wire shape only — no ids, links or titles):"`.
    9. Add the pure function `format_diagnostics(*, pages, fingerprint, last_good=None, metadata_count=None, listed=None, show_last_good=True) -> list[str]`. It returns, in order:
       - the header
       - "  page {post}: {items} item(s), cursor {cursor}" plus " — {note}" when a note is set, or "  pages: none fetched". With more than 12 pages, show the first 6, then "  … {n} more page(s)", then the last 5.
       - "  fingerprint:   {render}"
       - when show_last_good: "  last good run: {render or (none stored)}", then "  changed since the last good run: f old → new; …" or "  unchanged since the last good run" when both are present
       - "  album metadata: {m} item(s), listed: {listed} (shortfall tolerance {listing_shortfall_tolerance(m)})" when metadata_count is known; otherwise "  listed: {listed}" when listed is known
       Every line goes through `redact_tokens`. Import listing_shortfall_tolerance from enumerate; enumerate never imports wire, so there is no cycle.

    pushframe/cli.py:
    10. In run_google_album's `except EnumerateError as e` after `enumerate_album`, keep the existing `google-album failed:` print first. Then print `format_diagnostics(pages=getattr(e, "pages", []), fingerprint=getattr(e, "fingerprint", None), metadata_count=album.item_count, show_last_good=False)`. google-album keeps no state, by design.

    11. Run the listed modules, then the full offline suite.
  </action>
  <verify>
    <automated>uv run --extra dev pytest -q -m "not live" tests/test_google_wire.py tests/test_google_enumerate.py tests/test_gsync_execute.py tests/test_gsync_engine.py tests/test_cli_google_album.py</automated>
  </verify>
  <acceptance_criteria>
    - The RED commit comes before the GREEN commit, and the new tests failed at RED.
    - `uv run --extra dev pytest -q -m "not live" tests/test_google_enumerate.py -k "kind or trace or carries"` passes.
    - `uv run --extra dev pytest -q -m "not live" tests/test_gsync_engine.py -k "kinds or pure_half"` passes.
    - `grep -c "class PageTrace" pushframe/google/enumerate.py` prints 1.
    - `grep -c "DIAGNOSTICS_HEADER" pushframe/google/wire.py` prints at least 2.
    - Every pre-existing test in the five listed modules passes with its body unchanged, including the message-text assertions on EnumerateError and SafeSyncError.
  </acceptance_criteria>
  <done>Every Google listing failure, in google-sync and google-album, prints its unchanged failure line followed by a compact block carrying no user data: per-answer item counts and cursor shapes with retry/failure notes, the fingerprint, the last good fingerprint and what changed, and metadata vs listed. Frame-side failures print no wire block.</done>
</task>

<task type="auto" tdd="true">
  <name>Task 3: consecutive-failure escalation, report subject escalation and docs (TLX-3, TLX-4, DP-3)</name>
  <files>pushframe/google/wire.py, pushframe/gsync.py, pushframe/report.py, tests/test_google_wire.py, tests/test_gsync_execute.py, tests/test_report.py, docs/ERRORS.md, docs/CLI.md</files>
  <read_first>
    - pushframe/google/wire.py and pushframe/gsync.py as left by Task 2
    - pushframe/report.py lines 37-70 and 125-154 (as left by Task 1)
    - tests/test_report.py lines 585-615 (test_gsync_report_flag_sends_and_keeps_exit_code — the end-to-end pattern)
    - docs/ERRORS.md lines 47-66 (Google sync table); docs/CLI.md lines 263-316 (email reports) and 950-987 (google-sync)
  </read_first>
  <behavior>
    RED first: commit `test(quick-261014-tlx): red tests for consecutive Google failure escalation`. Then GREEN: commit `feat(quick-261014-tlx): escalate repeated Google listing failures as a probable protocol change`. Then a docs commit: `docs(quick-261014-tlx): document the Google wire watch warnings and escalation`.

    tests/test_google_wire.py:
    - test_failure_class_mapping:
      - `EnumerateError("x", kind="null-payload")` → "enumerate:null-payload"
      - SafeSyncError.truncated_listing() / empty_listing() / listing_below_metadata(3, 757) → their listing kind
      - SafeSyncError.manifest_drift(()) and empty_frame_listing() → None
      - ValueError() → None
    - test_streak_first_then_escalates: on_failure("listing:truncated", fp) gives exactly one line. It starts 'note: first "listing:truncated" failure in a row' and contains "may be a transient Google answer". A NEW WireWatch on the same path (proving persistence) then gives a line starting "ATTENTION: probable Google protocol change — pushframe likely needs an update" and containing '"listing:truncated" failed 2 runs in a row'. The third call says "3 runs in a row".
    - test_streak_class_change_restarts: truncated then below-metadata gives the first-occurrence note for below-metadata.
    - test_success_resets_streak: a failure, then on_listing_ok(fp), then a failure gives the first-occurrence note again.
    - test_failures_never_touch_last_good: after on_listing_ok(fp_a), two on_failure(cls, fp_b) calls leave `last_good() == fp_a`. When fp_b differs in cursor, the second call's lines include "  wire fingerprint changed since the last good run: cursor " with old → new. When fp_b equals fp_a or is None, there is no such line.
    - test_uncounted_failure_is_a_noop: on_failure(None, fp) returns [] and leaves the file unchanged or absent.
    - test_streak_with_corrupt_state: a garbage state file gives the unreadable note and a count starting at 1. It never raises.
    - Pin `wire.ESCALATION_MARKER == "probable Google protocol change — pushframe likely needs an update"`.

    tests/test_gsync_execute.py, parametrized over scheduled False / True (manual and scheduled share the key and print the same lines):
    - test_two_consecutive_listing_failures_escalate: two runs with metadata 757 both give rc 1.
      - Run 1 contains "may be a transient Google answer" and not "probable Google protocol change".
      - Run 2 contains "ATTENTION: probable Google protocol change — pushframe likely needs an update" and '"listing:below-metadata" failed 2 runs in a row'.
      - In both runs, the first line containing "google-sync failed:" comes before the diagnostics header and the note/ATTENTION line.
    - test_success_between_failures_resets_the_streak: 757 then 3 then 757. The third run has the first-occurrence note, not the escalation.
    - test_frame_side_failure_is_not_counted: `_FakeAura([])` twice gives neither the note nor the ATTENTION line.

    tests/test_report.py:
    - test_error_marker_google_protocol_change: `should_send("ERROR", 0, "ATTENTION: probable Google protocol change — pushframe likely needs an update: …")` is True.
    - test_subject_escalation_wins_at_every_level: for DEBUG/INFO/ERROR with rc 1, an output of "google-sync failed: album listing is NOT exhausted cleanly …" + the diagnostics header + the ATTENTION line (with or without a WARNING format-change line) gives exactly "[pushframe] nightly: FAILED — probable Google protocol change — pushframe likely needs an update". With rc 0 the subject reads ATTENTION in the same place.
    - test_gsync_report_escalation_email_end_to_end: mirror test_gsync_report_flag_sends_and_keeps_exit_code with a fake_run printing a failure line plus the ATTENTION line and returning 1. rc is 1, one email is sent, and its subject contains "probable Google protocol change".
    - Pin the report.py literal against `wire.ESCALATION_MARKER`'s leading phrase.
  </behavior>
  <action>
    pushframe/google/wire.py (DP-3):
    1. Add `ESCALATION_MARKER = "probable Google protocol change — pushframe likely needs an update"`.
    2. Add `failure_class(exc) -> str | None`: "enumerate:" + exc.kind for an EnumerateError; exc.kind when it is a str starting "listing:" (SafeSyncError, duck-typed so wire never imports gsync); otherwise None.
    3. `WireWatch.on_failure(cls, fingerprint) -> list[str]`:
       - cls None returns [] without loading or saving.
       - Otherwise load tolerantly. When the stored failure_class equals cls, increment consecutive_failures; otherwise set it to cls with count 1. NEVER touch the stored fingerprint: last good stays last good. Stamp `updated` and save tolerantly.
       - Return the queued notes, then:
         - count 1: 'note: first "{cls}" failure in a row for this sync target — may be a transient Google answer; nothing on the frame was changed and the next run retries (a second "{cls}" failure in a row is flagged as a probable Google protocol change).' Every counted class happens before any frame contact, so the frame claim is exact.
         - count of 2 or more: 'ATTENTION: {ESCALATION_MARKER}: "{cls}" failed {n} runs in a row for this sync target — check {RELEASES_URL} for a newer pushframe; if there is none yet, open an issue with this run\'s output (it carries no ids, links or titles).' When `fingerprint_diff(last_good, fingerprint)` is non-empty, follow it with "  wire fingerprint changed since the last good run: f old → new; …".
    4. `on_listing_ok` also resets the streak (failure_class None, consecutive_failures 0) on every success; a success means the listing passed the SAFE-01 gates.

    pushframe/gsync.py:
    5. At each of the three Google failure sites from Task 2, after the diagnostics lines, print every line of `watch.on_failure(wire.failure_class(e), fp)`. fp is the run's fingerprint from the exception or the listing, with the same albums enrichment as the success path when the album is known. The frame/manifest SafeSyncError path calls nothing: the trust gate already reset the streak, and those classes are not Google wire failures.

    pushframe/report.py:
    6. Append `'probable Google protocol change'` to `_PROBLEM_MARKERS`, with a comment naming wire.py's WireWatch.on_failure.
    7. In `build_subject`, FIRST of all (before the Task 1 format-change rule and the existing branches): when that marker is in the output, return "{prefix}{'FAILED' if rc else 'ATTENTION'} — probable Google protocol change — pushframe likely needs an update" at every level. Update the module docstring's ERROR bullet to name both new markers.

    Docs (TLX-4):
    8. docs/ERRORS.md: add rows to the "Google sync" table:
       - the `WARNING: Google response format changed: <field> <old> → <new> …` line (what changed, the run still passed every safety gate, check for an update, warns once per change)
       - the `Diagnostics (Google wire shape only — no ids, links or titles):` block (how to read page lines, cursor shapes, fingerprint, last good, tolerance; safe to paste in an issue)
       - the `note: first "<class>" failure in a row …` line
       - the `ATTENTION: probable Google protocol change — pushframe likely needs an update: "<class>" failed N runs in a row …` line (update or open an issue)
       - the two `note: … Google wire-format state …` lines (unreadable / not saved; the sync is unaffected)
       Add a short paragraph under the table covering:
       - the state file locations (~/.local/state/pushframe/pairs/<pair>/google-wire.json; ~/.local/state/pushframe/google-wire/album-<hash>.json)
       - that a run whose listing passes the safety gates resets the streak and stores the fingerprint
       - that manual and scheduled runs share the key
       - that deleting the file is safe (the next run stores silently)
       - the class vocabulary (enumerate:<kind>, listing:empty|truncated|below-metadata)
    9. docs/CLI.md:
       - In the email-reports levels table, add both new markers to the `--report ERROR` row.
       - In the subject paragraph, add `ATTENTION — Google response format changed (check for a pushframe update)` (any level, exit 0) and `FAILED — probable Google protocol change — pushframe likely needs an update` (any level).
       - In the google-sync section, add one "Wire-format watch" paragraph pointing to docs/ERRORS.md.
    10. Do not touch pyproject.toml, uv.lock, CHANGELOG.md or pushframe/__init__.py (the release is a separate step). Run the full offline suite.
  </action>
  <verify>
    <automated>uv run --extra dev pytest -q -m "not live"</automated>
  </verify>
  <acceptance_criteria>
    - RED, GREEN and docs commits exist in that order, and the new tests failed at RED.
    - `uv run --extra dev pytest -q -m "not live"` reports more than 734 passed and 0 failed.
    - `grep -c "probable Google protocol change" docs/ERRORS.md` prints at least 1, and the same for docs/CLI.md and pushframe/report.py.
    - `grep -c "WARNING: Google response format changed" docs/ERRORS.md` prints at least 1, and the same for docs/CLI.md.
    - `grep -c "google-wire" docs/ERRORS.md` prints at least 1.
    - `git diff --quiet 2dea192 -- pyproject.toml uv.lock CHANGELOG.md pushframe/__init__.py` exits 0.
  </acceptance_criteria>
  <done>Two consecutive google-sync runs failing with the same Google listing class print, and email, "probable Google protocol change — pushframe likely needs an update" with the fingerprint diff. The first failure is described as possibly transient. A good listing resets the streak. The subject escalates at every level. Every new line is documented and the offline suite is green.</done>
</task>

</tasks>

<threat_model>
## Trust Boundaries

| Boundary | Description |
|----------|-------------|
| photos.google.com snAcKc and /albums answers -> fingerprint / diagnostics text | Untrusted, undocumented upstream structures are rendered into human-readable lines |
| run output -> job log / `--report` email (SMTP to the operator) | Warnings and diagnostics leave the machine by email and may be pasted into public issues |
| local state file under ~/.local/state/pushframe -> WireWatch loader | The file can be corrupted, truncated or hand-edited, and its values are re-printed |

## STRIDE Threat Register

| Threat ID | Category | Component | Severity | Disposition | Mitigation Plan |
|-----------|----------|-----------|----------|-------------|-----------------|
| T-261014-tlx-01 | Information disclosure | fingerprint, diagnostics, WARNING and ATTENTION lines (report emails, issues) | high | mitigate | Task 1: shapes are built only from value kinds, `item+` runs, power-of-two buckets and a cursor prefix of at most 4 chars, so ids, URLs, titles and full cursors never appear by construction. Task 2: every diagnostics line goes through redact_tokens. Tests feed AF1Qip ids, googleusercontent URLs, album titles and 226-char AKFE cursors and assert none reaches any rendered line |
| T-261014-tlx-02 | Tampering | WireWatch state loader | medium | mitigate | Task 1: closed schema (version 1, known keys, printable-ASCII values of at most 200 chars with no control chars, class charset, int counts); any violation is treated as absent with one note. Subjects are fixed text and never echo state values |
| T-261014-tlx-03 | Tampering | state_path_for (path construction) | medium | mitigate | Task 1: pair names are gated by safe_name_problem (an unsafe name disables the watch, nothing is built); album targets only ever appear as sha1 hex; tested with `../x` |
| T-261014-tlx-04 | Information disclosure | wire-state file permissions | low | mitigate | Task 1: 0600 via os.open + fchmod, ensure_private_dir 0700 for new dirs, atomic tmp + os.replace; tests assert the modes |
| T-261014-tlx-05 | Denial of service | state I/O inside the sync path | high | mitigate | Tasks 1 and 3: every load and save is wrapped and never raises. Corrupt-file and unsaveable-dir tests prove the sync result and exit code are unchanged. Task 2: a test proves the pure plan half never touches wire state |
| T-261014-tlx-06 | Repudiation | escalation heuristics (false or missed alarm) | low | accept | Advisory only: it never changes exit codes, SAFE gates or retries. Warns once per change; buckets and the full-page-only cursor field limit churn. Worst case is one spurious ATTENTION email |
| T-261014-tlx-07 | Tampering | concurrent manual + scheduled runs on one state file | low | accept | The atomic replace prevents torn files and the last writer wins. Worst case is one missed or extra streak increment |
| T-261014-tlx-SC | Tampering | npm/pip/cargo installs | low | accept | No package installs or dependency changes (stdlib hashlib/json/os only; pyproject.toml and uv.lock untouched) |
</threat_model>

<verification>
- `uv run --extra dev pytest -q -m "not live"`: green, more than 734 passed, 0 failed, DeprecationWarnings still errors.
- Live-shape replay through the offline routers: AKFE 300+300+157 gives the fingerprint `inner=[null,list,str,list[32-63],null,int] cursor=structural:AKFE/len128-255 item=[str,[str,int,int],int]`; a switch from the legacy to the AKFE wire prints exactly one WARNING and then stays silent.
- Two consecutive below-metadata failures escalate, in manual and scheduled mode alike; the email subject reads `FAILED — probable Google protocol change — pushframe likely needs an update`.
- `git diff --quiet 2dea192 -- pyproject.toml uv.lock CHANGELOG.md pushframe/__init__.py` exits 0.
</verification>

<success_criteria>
- Any change in the snAcKc inner shape, cursor slot/prefix/length class, item slot shape or ds:5 row length bucket for a target produces one WARNING line in the run output, job log and report email (ERROR level included), with an ATTENTION subject on a successful run.
- Every Google listing failure carries a self-sufficient diagnostics block that is safe to paste into a public issue.
- Two failures in a row of the same class read as a probable protocol change in both the message and the subject. One failure reads as possibly transient. A good listing resets the streak.
- No sync can be crashed or altered by wire-state problems. Pure functions, SAFE gates and retries are unchanged. Docs are updated, and no version or CHANGELOG change is made.
</success_criteria>

<output>
Create `.planning/quick/261014-tlx-detect-google-wire-format-drift-response/261014-tlx-SUMMARY.md` when done
</output>

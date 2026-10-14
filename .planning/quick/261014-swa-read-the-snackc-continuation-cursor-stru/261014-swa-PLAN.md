---
phase: quick-261014-swa
plan: 01
type: execute
wave: 1
depends_on: []
files_modified:
  - pushframe/google/parsers.py
  - pushframe/google/enumerate.py
  - pushframe/google/redaction.py
  - pushframe/google/__init__.py
  - tests/test_google_parsers.py
  - tests/test_google_enumerate.py
  - tests/fixtures/google_rpc_snackc_page_akfe.json
autonomous: true
requirements: [SWA-1, SWA-2, SWA-3, SWA-4]
gap_closure: false

estimate:
  tokens: 60000
  raw_tokens: 60000
  tasks: 2
  confidence: low

must_haves:
  truths:
    - "SWA-1: parse_snackc_payload reads the continuation cursor STRUCTURALLY from inner[2] whenever the parsed inner payload is a list with len >= 3, inner[1] a list or None, and inner[2] a str or None (the live 2026-10-14 shape [None, <items>, <cursor>, <album meta list[42]>, None, <int>]). A non-empty validated string is the cursor; an empty string or None means no cursor (end). On this path the AH_ regex is never consulted, so an AH_-looking string anywhere in the items (whole value or substring) can never become the cursor."
    - "SWA-1: a non-empty inner[2] that is not 16-4096 chars of [A-Za-z0-9_-] raises ProbeParseError naming `inner[2]` and the length, never echoing the value; enumerate_album turns any ProbeParseError from a snAcKc page into EnumerateError (chained), so google-sync / google-album print their existing `... failed:` line and exit 1 instead of a traceback."
    - "SWA-3: enumerate_album over the live AKFE wire shape (300+300+157, 226-char AKFE cursors, AH_ decoys inside every item, page 3 answering inner[2] == \"\") returns 757 unique items, exhausted_cleanly True, page_count 3, exactly 3 POSTs whose 2nd and 3rd carry the AKFE cursors — with and without expected_count=757."
    - "SWA-3: the existing gates are unchanged on the new wire: an empty inner[2] on a FULL page is the tokenless transient (one same-cursor retry with expected_count, 757 recovered); a SHORT page carrying an AKFE cursor stays terminal (the past-the-end page is never requested); the null-payload single retry still recovers; every pre-existing test in tests/test_google_enumerate.py, tests/test_cli_google_album.py, tests/test_gsync_execute.py and tests/test_gsync_engine.py passes unchanged."
    - "SWA-1 (legacy): when inner has no structural cursor slot (legacy fixture [item, item, item, [\"AH_…\"]], legacy fakes [items, [\"AH_…\"]]) the fallback returns the LAST whole-string AH_TOKEN_RE match found OUTSIDE media-item nodes; an AH_ string inside an item (whole value or URL substring) is never selected; the legacy fixture tests/fixtures/google_rpc_snackc_page.json still yields its AH_ cursor."
    - "SWA-2: redact_tokens collapses AH_ and AKFE cursors (prefix + 40 or more URL-safe chars) to `AH_…<last4>` / `AKFE…<last4>`, keeps the AF1Qip…<last4> behavior, is idempotent, and leaves short prose words (e.g. `AKFE cursor`) untouched."
    - "SWA-4: the cursor description is corrected in the enumerate.py module docstring and enumerate_album docstring, the parsers.py AH_TOKEN_RE comment / SnackcPage / parse_snackc_payload docstrings, the redaction.py module docstring and the pushframe/google/__init__.py docstring; no version bump, no CHANGELOG edit; full offline suite `uv run --extra dev pytest -q -m \"not live\"` green (713 pre-existing + the new tests, DeprecationWarnings still errors)."
  artifacts:
    - path: pushframe/google/parsers.py
      provides: "_CURSOR_RE (16-4096 URL-safe chars); structural inner[2] cursor read with fail-loud validation; _is_media_item predicate shared by _walk_items and the item-safe legacy fallback _legacy_cursor"
    - path: pushframe/google/enumerate.py
      provides: "_snackc_page converts ProbeParseError (envelope or payload) into EnumerateError; corrected cursor docstrings"
    - path: pushframe/google/redaction.py
      provides: "redact_tokens also collapses AH_/AKFE continuation cursors"
    - path: tests/fixtures/google_rpc_snackc_page_akfe.json
      provides: "fully synthetic live-shape batchexecute body: 6-element inner, AKFE cursor at inner[2], AH_ decoys inside items"
    - path: tests/test_google_enumerate.py
      provides: "_GoogleRouter wire='akfe' knob + AKFE end-to-end enumeration tests (757 three pages, tokenless transient, short page, null retry, malformed cursor)"
    - path: tests/test_google_parsers.py
      provides: "structural cursor tests, malformed-cursor no-echo tests, item-safe legacy fallback tests, cursor redaction tests"
  key_links:
    - from: pushframe/google/enumerate.py _snackc_page
      to: pushframe/google/parsers.py parse_snackc_payload
      via: "page = parse_snackc_payload(entries[0].payload) -> page.continuation_token becomes the next POST's args[1]; ProbeParseError re-raised as EnumerateError"
    - from: pushframe/gsync.py run_google_sync / pushframe/cli.py run_google_album
      to: pushframe/google/enumerate.py enumerate_album
      via: "existing `except EnumerateError as e: print(f'google-sync failed: {redact_tokens(str(e))}')` — unchanged, now also covers cursor-shape failures"
    - from: pushframe/google/parsers.py parse_snackc_payload legacy branch
      to: pushframe/google/parsers.py _is_media_item
      via: "_legacy_cursor walks the parsed inner, never descending into a media item node"
---

<objective>
URGENT production fix: read the snAcKc continuation cursor structurally from `inner[2]` — Google switched the cursor value from `AH_…` to `AKFE…` tokens, and the nightly `google-sync` on venus (00:02) has failed every run since.

Live evidence (read-only probes on venus, 2026-10-14, pushframe 5.1.34, the real 757-item album "Cadre"):
- The snAcKc `wrb.fr` inner payload is a 6-element list `[None, <items list[300]>, <cursor str>, <list[42] album metadata>, None, <int>]`.
- The cursor is `inner[2]`: a 226-char `[A-Za-z0-9_-]+` string starting `AKFE`. Following it gives 300 + 300 + 157 items, page 3 answering `inner[2] == ""` (end). 757 = the album metadata count.
- `parse_snackc_payload` (pushframe/google/parsers.py) takes the LAST `AH_TOKEN_RE` match anywhere in the raw payload string. With the AKFE format there is usually no match, so the page reads as tokenless and the listing stops at 300 (the 2026-10-13 incident; 5.1.33's SAFE gate now refuses to plan, so every run fails loud). Sometimes the regex matches an `AH_…` substring inside item data; that garbage is sent as the cursor and Google answers a null inner payload ("snAcKc carries a null inner payload"). One root cause, two symptoms.
- The AH_ format was live-proven ~2026-09 to 2026-10-01 and presumably sat in the same slot; backward compatibility is kept through a restricted legacy fallback.

Requirement IDs (from the quick-task description's "Required fix"):
- SWA-1 — parser: structural `inner[2]` cursor (non-empty = cursor, "" / None = end), charset and length validated, unexpected shape fails loud with a named ProbeParseError; the legacy AH_ fallback only when `inner[2]` is absent / not a string, and it can never pick a token from inside the items.
- SWA-2 — every other AH_ consumer checked; redaction also covers the AKFE cursor.
- SWA-3 — TDD with offline fakes: new-format fixture and fake (AKFE at inner[2], AH_ decoys in items), 3-page 300+300+157 ending `inner[2] == ""`, legacy format still passing, enumerate gates unchanged.
- SWA-4 — docstrings and comments describing the cursor updated; no version bump, no CHANGELOG.

Purpose: unblock the unattended nightly mirror without weakening any fail-loud gate, and make the next cursor drift fail loud with a named error instead of guessing.
Output: patched `pushframe/google/parsers.py`, `enumerate.py`, `redaction.py`, `__init__.py`; a new synthetic fixture; red-first offline tests; full offline suite green.
</objective>

<execution_context>
@~/.claude/gsd-core/workflows/execute-plan.md
@~/.claude/gsd-core/templates/summary.md
</execution_context>

<context>
@.planning/STATE.md
@./CLAUDE.md
@pushframe/google/parsers.py
@pushframe/google/enumerate.py
@pushframe/google/redaction.py
@tests/test_google_parsers.py
@tests/test_google_enumerate.py
@tests/fixtures/google_rpc_snackc_page.json

<interfaces>
<!-- Extracted from the codebase at planning time; executors use these directly. -->

pushframe/google/parsers.py (today, 345 lines):
- line 27: `AH_TOKEN_RE = re.compile(r"AH_[A-Za-z0-9_-]{40,}")` — only consumer is parse_snackc_payload (grep-verified at planning time).
- `class ProbeParseError(RuntimeError)`.
- `_walk_items(node, items)` lines 147-167 — the media-item predicate is inline: node is a list, len >= 3, node[0] a str starting "AF1Qip", node[1] a non-empty list whose [0] is a str starting "http". A matching node is appended and NOT descended into.
- `_dedupe(items)`; `@dataclass SnackcPage(items: list[dict], continuation_token: str | None)` — None = exhausted.
- `parse_snackc_payload(payload_str: str | None) -> SnackcPage` lines 200-222: empty/None payload -> ProbeParseError; json.loads failure -> ProbeParseError; walks items over the WHOLE inner; cursor = last AH_TOKEN_RE.findall over the raw payload string.
- `parse_batchexecute(text) -> list[BatchexecuteEntry(rpcid, payload)]` — raises ProbeParseError when the body has zero JSON lines.

pushframe/google/enumerate.py (today, 336 lines):
- imports ProbeParseError, parse_batchexecute, parse_snackc_payload, redact_tokens.
- `_snackc_page(session, scalars, album_id, page_key, continuation_token) -> tuple[list[dict], str | None]` lines 157-210: inner `_snackc_entries(resp)` (HTTP != 200 -> EnumerateError with `redact_tokens(resp.text[:200])`; calls parse_batchexecute; no snAcKc entry -> EnumerateError); null payload -> one retry from the module-global `_retry_budget`; then `page = parse_snackc_payload(entries[0].payload)`; returns `page.items, page.continuation_token`. ProbeParseError currently escapes uncaught.
- `enumerate_album(session, album_id, *, page_key=None, max_pages=MAX_PAGES, expected_count=None) -> AlbumListing` lines 213-291: tokenless-full-short retry (local budget 1), short page (< PAGE_SIZE, read at CALL time — tests monkeypatch it) terminal, `next_token is None or not page_items` terminal. NO logic change in this plan.
- Callers catch only EnumerateError: pushframe/gsync.py (~line 527 `print(f'google-sync failed: {redact_tokens(str(e))}')`, return 1) and pushframe/cli.py run_google_album (~lines 1461 and 1502, `google-album failed:`).

pushframe/google/redaction.py (today): `_FULL_TOKEN_RE = AF1Qip[A-Za-z0-9_-]{40,}`; `redact_link(url)`; `redact_tokens(text)` collapses only AF1Qip tokens to `AF1Qip…<last4>` — AH_ cursors are NOT redacted today. Re-exported by pushframe/google/__init__.py and probes/common.py (tests/test_probe_shared_link.py pins `redact_tokens(token_only) == "AF1Qip…0123"`).

Test harness (tests/test_google_enumerate.py):
- `_next_token(n) = "AH_" + "N"*40 + f"{n:04d}"`; `_rpc_items(page, count)` items `[id, [url, w, h], ts]`; `_rpc_response(items, next_token)` builds the LEGACY inner `[items]` or `[items, [token]]` (len <= 2 — so it stays on the legacy fallback path).
- `_GoogleRouter(pages, *, page_key, rpc_status, rpc_body, albums_page, null_positions, tokenless_positions, token_on_last_page)`: validates the triple-nested f.req, rejects a stale cursor with HTTP 400 (`inner[1] != self._expected_token`), counters `post_count`, `nulls_served`, `tokenless_served`, `freq_inners` (decoded args per POST), `_pages_served`. `rpc_body` short-circuits every POST with a fixed body. Page responses are built in TWO places today: the tokenless_positions branch (`_rpc_response(_rpc_items(cur + 1, ...), None)`) and the normal branch (`_rpc_response(_rpc_items(page_idx + 1, ...), next_token)`, minting `_next_token(page_idx + 2)`).
- Other legacy fakes that must stay green UNCHANGED: tests/test_cli_google_album.py `_rpc_response` and tests/test_gsync_execute.py `_snackc_response` (both `[items]` / `[items, [token]]`).

Legacy fixture tests/fixtures/google_rpc_snackc_page.json: inner = `[item1, item2, item3, ["AH_0000000000000000000000000000000000000000FAKECURSOR0001"]]` (inner[2] is an item list -> legacy path); stays as the backward-compat proof, unchanged.
</interfaces>
</context>

<tasks>

<task type="tracer" tdd="true">
  <name>Task 1 (tracer): AKFE cursor read structurally from inner[2], end to end through enumerate_album (SWA-1, SWA-3)</name>
  <files>pushframe/google/parsers.py, pushframe/google/enumerate.py, tests/test_google_parsers.py, tests/test_google_enumerate.py, tests/fixtures/google_rpc_snackc_page_akfe.json</files>
  <read_first>
    - pushframe/google/parsers.py lines 1-30 and 147-222 (regex constant, `_walk_items`, `SnackcPage`, `parse_snackc_payload`)
    - pushframe/google/enumerate.py lines 1-41 (module docstring) and 157-238 (`_snackc_page`, `enumerate_album` docstring)
    - tests/test_google_enumerate.py lines 30-180 (helpers + `_GoogleRouter`) and 318-347 (short-page and tokenless tests — style to mirror)
    - tests/test_google_parsers.py lines 1-95 (fixture-test style)
  </read_first>
  <behavior>
    RED first — commit `test(quick-261014-swa): red tests for the structural AKFE snAcKc cursor` (run the new tests, confirm they FAIL on today's code) — then GREEN — commit `fix(quick-261014-swa): read the snAcKc continuation cursor from inner[2]`.

    New fixture tests/fixtures/google_rpc_snackc_page_akfe.json (fully synthetic, never recorded live): the body `)]}'`, a blank line, then one line `[["wrb.fr","snAcKc",<inner JSON string>,null,null,null,"generic"]]`. Inner = `[null, [item1, item2, item3], CURSOR, META, null, 3]` where CURSOR = "AKFE" + "FAKECURSOR" * 22 + "01" (226 chars), META = a 42-element list ("FAKE album" then 41 nulls), items shaped `[id, [base_url, w, h], ts, sig]` with ids `AF1QipFAKEakfeitem00000000000000000000000001..03`, item2's sig = a WHOLE-string decoy "AH_" + "FAKEDECOY" * 5 + "0002", and item3's base_url = "https://lh3.googleusercontent.com/pw/AH_" + "FAKEDECOYURL" * 4 + "0003" (a substring decoy). Generate the file with a throwaway script in the session scratchpad (json.dumps with separators=(",", ":") for the inner string, then for the envelope line) so the JSON-in-JSON escaping is exact; commit only the fixture.

    tests/test_google_parsers.py (new; module constant AKFE_FIXTURE_CURSOR = "AKFE" + "FAKECURSOR" * 22 + "01"; inline payloads built with json.dumps; a small helper building an item with optional decoys is fine):
    - test_parse_snackc_akfe_fixture_reads_cursor_from_inner2: parse_batchexecute gives 1 snAcKc entry; parse_snackc_payload gives the 3 item ids in order and `continuation_token == AKFE_FIXTURE_CURSOR` (len 226); the token does not start with "AH_".
    - test_parse_snackc_structural_cursor_beats_ah_decoys: `[None, [item_with_whole_AH_decoy, item_with_url_substring_AH_decoy], CURSOR, META, None, 2]` -> continuation_token == CURSOR, 2 items.
    - test_parse_snackc_structural_empty_cursor_is_end_even_with_ah_decoys: same items, inner[2] == "" -> continuation_token is None, 2 items (the 2026-10-13 garbage-cursor root cause).
    - test_parse_snackc_structural_null_cursor_is_end: inner[2] None -> None.
    - test_parse_snackc_malformed_cursor_fails_loud_without_echo, parametrized over "AKFE" + "x" * 40 + "!", "AKFE" + "x" * 40 + "/+=", "AKFE" + "x" * 20 + " " + "x" * 20, "AKFE" + "A" * 5000, and "AKFEshort1" -> ProbeParseError whose message contains "inner[2]" and does NOT contain the bad value (for the 5000-char case assert its first 60 chars are absent).

    tests/test_google_enumerate.py (new):
    - Helpers: `_akfe_token(n)` = "AKFE" + "K" * 218 + f"{n:04d}" (226 chars); `_rpc_items_akfe(page, count)` = `_rpc_items(page, count)` items each extended with a 4th WHOLE-string decoy "AH_" + "D" * 40 + f"{page:02d}{i:04d}"; `_rpc_response_akfe(items, cursor)` builds the live inner `[None, items, cursor or "", META42, None, len(items)]` inside the same wrb.fr envelope as `_rpc_response`.
    - `_GoogleRouter` gains kwarg `wire: str = "legacy"` ("legacy" | "akfe"). Route BOTH page-building sites (tokenless branch and normal branch) through one method that dispatches on `self.wire`; in akfe mode the router mints `_akfe_token(page_idx + 2)` and answers `inner[2] == ""` where legacy would omit the token. The stale-cursor 400 check is unchanged, so a decoy sent as cursor fails the request.
    - test_enumerate_akfe_cursor_757_three_pages, parametrized expected_count in (None, 757): `_GoogleRouter([300, 300, 157], wire="akfe")` -> 757 unique items, exhausted_cleanly True, page_count 3, post_count 3, nulls_served 0, `freq_inners[0][1] is None`, `freq_inners[1][1] == _akfe_token(2)`, `freq_inners[2][1] == _akfe_token(3)`, both 226 chars.
    - test_enumerate_akfe_empty_cursor_on_full_page_is_the_tokenless_transient: wire="akfe", tokenless_positions={1}, expected_count=757 -> 757, post_count 4, tokenless_served 1, `freq_inners[0] == freq_inners[1]`.
    - test_enumerate_akfe_short_page_with_cursor_is_terminal: wire="akfe", token_on_last_page=True -> 757, post_count 3, nulls_served 0.
    - test_enumerate_akfe_null_payload_retry_unchanged: wire="akfe", null_positions={2} -> 757, post_count 4, nulls_served 1.
    - test_enumerate_malformed_cursor_is_a_named_enumerate_error: `_GoogleRouter([300], rpc_body=_rpc_response_akfe(_rpc_items_akfe(1, 300), BAD))` with BAD = "AKFE" + "x" * 40 + "!bad" -> `pytest.raises(EnumerateError, match=r"inner\[2\]")` and BAD not in str(exc.value).
    Expected RED on today's code: the AKFE tests fail (the AH_ regex picks an item decoy, the router answers 400), the structural parser tests fail, the malformed-cursor tests fail (no validation; enumerate ends on the page cap instead).
  </behavior>
  <action>
    pushframe/google/parsers.py (per SWA-1):
    1. Add module constant `_CURSOR_RE` compiled from the class `[A-Za-z0-9_-]` with a bounded length of 16 to 4096 (always used with fullmatch). Comment: live 2026-10-14 cursors are 226-char `AKFE…`; legacy `AH_…` are 43+ chars; the bounds are deliberately generous (Claude's discretion) — an out-of-bounds value is a drift to fail loud on, not to guess at.
    2. In `parse_snackc_payload`, after json.loads and the existing whole-inner item walk (keep it), decide the cursor: when `inner` is a list with len >= 3, `inner[1]` is a list or None, and `inner[2]` is a str or None — the structural slot (live shape `[None, <items>, <cursor>, <album meta>, None, <int>]`) — then: falsy `inner[2]` ("" or None) means continuation_token None (end); a non-empty value that does not fullmatch `_CURSOR_RE` raises ProbeParseError with a message of the form "snAcKc continuation cursor at inner[2] has an unexpected shape (length N; expected 16-4096 chars of [A-Za-z0-9_-]) — Google changed the wire format; refusing to guess" — interpolate ONLY the length, NEVER the value (cursors are capability-like and error text reaches logs and report emails); a valid value is the cursor. The AH_ regex is NOT consulted on this path. Otherwise (no structural slot) keep today's legacy behavior for now — Task 2 restricts it.
    3. Rewrite the `SnackcPage` and `parse_snackc_payload` docstrings: cursor = inner[2] (AKFE… since 2026-10-14, AH_… before), "" / None = end, malformed = ProbeParseError, legacy AH_ fallback only when the slot is absent.

    pushframe/google/enumerate.py:
    4. In `_snackc_page`, catch ProbeParseError from `parse_batchexecute` (inside `_snackc_entries`) and from `parse_snackc_payload`, and raise EnumerateError chained with `from exc`, message "snAcKc page could not be parsed: {exc} — failing loud, never a partial listing". Rationale (Claude's discretion, core value "named error with a remedy instead of a traceback"): gsync and cli catch only EnumerateError, so today a parse failure escapes as a traceback in the scheduled run. ProbeParseError stays the parser-level type (parser tests keep asserting it); existing `test_enumerate_fails_loud_on_malformed_envelope` asserts RuntimeError, which EnumerateError satisfies. Do NOT touch `_retry_budget`, the null-payload retry, the tokenless-full-short retry, the short-page terminal rule or the call-time PAGE_SIZE read.
    5. Docstrings (per SWA-4): module docstring — the SPA-shell bullet says "no continuation cursor" instead of naming AH_; the Continuation bullet says the cursor is the snAcKc inner payload's slot inner[2] (live shape `[None, <items>, <cursor>, <album meta>, None, <int>]`), a 226-char `AKFE…` string since 2026-10-14 (previously `AH_…`, still accepted through a legacy fallback when the slot is absent), "" = exhausted, live proof 2026-10-14 300+300+157 = 757; the TOKENLESS FULL PAGE bullet gains the root cause: the 2026-10-13 tokenless 300-item page and the recurring null-payload errors were the old AH_ regex missing the AKFE cursor or picking an AH_ substring out of item data; the retry and gates stay as defense in depth. `enumerate_album` docstring: "swap the LAST AH_ cursor per page" becomes "follow each page's continuation cursor (inner[2])".
    6. Run the four Google test modules; then the full offline suite.
  </action>
  <verify>
    <automated>uv run --extra dev pytest -q -m "not live" tests/test_google_parsers.py tests/test_google_enumerate.py tests/test_cli_google_album.py tests/test_gsync_execute.py tests/test_gsync_engine.py</automated>
  </verify>
  <acceptance_criteria>
    - The RED commit exists before the GREEN commit (`git log --oneline -3` shows `test(quick-261014-swa): ...` then `fix(quick-261014-swa): ...`), and the new tests failed at the RED commit.
    - `uv run --extra dev pytest -q -m "not live" tests/test_google_enumerate.py -k akfe` passes, including the 757 / three-POST / AKFE-cursor-chain assertions.
    - `uv run --extra dev pytest -q -m "not live" tests/test_google_parsers.py -k "akfe or structural or malformed"` passes.
    - `grep -c "_CURSOR_RE" pushframe/google/parsers.py` is at least 2 (definition + use).
    - Every pre-existing test in the five listed modules passes unchanged (no edits to existing test bodies or to the legacy fixture).
  </acceptance_criteria>
  <done>On the live AKFE wire shape the enumerator follows inner[2] to the full 757-item listing in 3 POSTs; "" / None end the walk; a malformed cursor is a named EnumerateError that never echoes the cursor; the legacy AH_ shapes still enumerate; tokenless, short-page and null-payload gates behave exactly as before.</done>
</task>

<task type="auto" tdd="true">
  <name>Task 2: item-safe legacy AH_ fallback, AH_/AKFE cursor redaction, consumer audit and docs (SWA-1 legacy, SWA-2, SWA-4)</name>
  <files>pushframe/google/parsers.py, pushframe/google/redaction.py, pushframe/google/__init__.py, tests/test_google_parsers.py</files>
  <read_first>
    - pushframe/google/parsers.py `_walk_items` and `parse_snackc_payload` as left by Task 1
    - pushframe/google/redaction.py (whole file, 33 lines)
    - pushframe/google/__init__.py lines 1-17 (package docstring)
    - tests/test_google_parsers.py redaction test (`test_redact_link_never_emits_full_url`) for style
  </read_first>
  <behavior>
    RED first — commit `test(quick-261014-swa): red tests for an item-safe legacy cursor fallback and cursor redaction` — then GREEN — commit `fix(quick-261014-swa): legacy AH_ fallback never reads item data; redact AH_/AKFE cursors`.

    tests/test_google_parsers.py (new):
    - test_parse_snackc_legacy_fallback_ignores_item_embedded_ah_strings: legacy shape (no structural slot) `[[id, ["https://lh3.googleusercontent.com/pw/AH_" + "U" * 44, 1, 2], 1, "AH_" + "D" * 44]]` -> continuation_token is None, 1 item.
    - test_parse_snackc_legacy_fallback_standalone_token_beats_later_item_decoys: `[[REAL], [item_with_whole_AH_decoy]]` with REAL = "AH_" + "R" * 40 + "0001" -> continuation_token == REAL (today's last-match-over-the-raw-string picks the decoy).
    - test_parse_snackc_legacy_fallback_last_standalone_token_wins: `[[item_a], ["AH_" + "R" * 40 + "0001"], ["AH_" + "R" * 40 + "0002"]]` (inner[2] is a list, so legacy path) -> the "0002" token.
    - The existing `test_parse_snackc_payload_fixture` (legacy fixture, cursor at inner[3][0]) and `test_parse_snackc_without_token_is_exhausted` stay unchanged and green.
    - test_redact_tokens_collapses_akfe_and_ah_cursors: with AKFE = "AKFE" + "K" * 218 + "WXYZ" and AH = "AH_" + "N" * 40 + "0001", `redact_tokens(f"cursor={AKFE} next={AH}") == "cursor=AKFE…WXYZ next=AH_…0001"`; neither full value is present.
    - test_redact_tokens_mixed_is_idempotent_and_keeps_af1qip: a string carrying an AF1Qip token, an AKFE cursor and an AH_ cursor -> each collapses to its `prefix…last4`; `redact_tokens(redact_tokens(s)) == redact_tokens(s)`.
    - test_redact_tokens_leaves_short_prose_alone: "AKFE cursor, AH_ token" is returned unchanged.
  </behavior>
  <action>
    pushframe/google/parsers.py (per SWA-1, legacy branch):
    1. Extract the media-item predicate inlined in `_walk_items` into `_is_media_item(node) -> bool` (same conditions, documented as the §1b shape) and use it in `_walk_items` — behavior identical.
    2. Add `_legacy_cursor(node) -> str | None`: depth-first over the PARSED inner payload; never descend into a node for which `_is_media_item` is True; collect every str value that fullmatches both AH_TOKEN_RE and `_CURSOR_RE`; return the LAST collected (preserves the live-proven "last token is the freshest" rule) or None. In `parse_snackc_payload`'s no-structural-slot branch, replace the scan over the raw payload string with `_legacy_cursor(inner)`. Cursors are whole JSON string values, never substrings of a larger string. The legacy fixture (token at inner[3][0]) and the legacy fakes (`[items, [token]]`) keep their token outside item nodes, so they still resolve.
    3. Rewrite the comment above AH_TOKEN_RE: LEGACY cursor shape (live-proven ~2026-09 to 2026-10-01), consulted only when the inner payload has no structural inner[2] slot, matched against whole string values outside media items.

    pushframe/google/redaction.py (per SWA-2):
    4. Add `_CURSOR_TOKEN_RE` matching a captured prefix `AH_` or `AKFE` followed by 40 or more chars of `[A-Za-z0-9_-]`; `redact_tokens` applies the existing AF1Qip substitution first, then this one, replacing each match with its prefix + "…" + its last 4 chars. `redact_link` unchanged. Module docstring: AH_ and AKFE continuation cursors are capability-like; the parser never interpolates a cursor value into an error message, so the redaction is the second line of defense for raw bodies (e.g. the HTTP != 200 body head in enumerate.py).

    pushframe/google/__init__.py (per SWA-4):
    5. The enumeration bullet of the package docstring: "the snAcKc continuation loop (300/page, structural inner[2] cursor — AKFE… live since 2026-10-14, legacy AH_… fallback — clean exhaustion)".

    Consumer audit (per SWA-2, no edits — record the outcome in the SUMMARY):
    6. Confirm by grep that inside pushframe/ no module other than parsers.py matches cursors. Leave probes/browser_bootstrap.py untouched (disposable phase-16 live instrument, not imported by the package; its AH_ regex documents the historical wire); leave tests/fixtures/google_share_page_sample.html untouched (parse_af_initdata never reads cursors; its AH_ string is inert); keep tests/fixtures/google_rpc_snackc_page.json as the legacy-format fixture; docs/ has no AH_ mention (planning-time grep), so no doc page changes. probes/common.py re-exports redact_tokens and inherits the new redaction automatically.
    7. No version bump, no CHANGELOG edit (separate release step). Run the full offline suite.
  </action>
  <verify>
    <automated>uv run --extra dev pytest -q -m "not live" tests/test_google_parsers.py tests/test_probe_shared_link.py tests/test_google_enumerate.py tests/test_cli_google_album.py tests/test_gsync_execute.py && uv run --extra dev pytest -q -m "not live"</automated>
  </verify>
  <acceptance_criteria>
    - The RED commit precedes the GREEN commit and the new legacy-fallback and redaction tests failed at the RED commit.
    - `uv run --extra dev pytest -q -m "not live"` passes with zero failures and at least 713 + the new tests collected (record the exact count in the SUMMARY).
    - `grep -n "_is_media_item" pushframe/google/parsers.py` shows the definition and its use in both `_walk_items` and `_legacy_cursor`.
    - `grep -n "AKFE" pushframe/google/redaction.py pushframe/google/enumerate.py pushframe/google/__init__.py` returns a hit in each file.
    - `git diff --quiet 5b1a2b7 -- pyproject.toml pushframe/__init__.py CHANGELOG.md` exits 0 (no version or changelog change across this plan's commits).
  </acceptance_criteria>
  <done>The legacy AH_ fallback can only pick a standalone token outside media items; AH_ and AKFE cursors are redacted in any printed or logged text; every cursor description in the package is accurate; the consumer audit is recorded; the full offline suite is green with no version bump.</done>
</task>

</tasks>

<threat_model>
## Trust Boundaries

| Boundary | Description |
|----------|-------------|
| photos.google.com snAcKc -> parse_snackc_payload | Untrusted, undocumented upstream wire (it just changed cursor format); its cursor is echoed back to Google and drives listing completeness |
| album listing -> gsync plan -> Aura frame writes | A truncated listing turns into destructive hides; the SAFE-01 gates (unchanged) stand between them |
| error text -> stderr / logs / scheduled report email | Cursors and capability tokens must never be printed in full |

## STRIDE Threat Register

| Threat ID | Category | Component | Severity | Disposition | Mitigation Plan |
|-----------|----------|-----------|----------|-------------|-----------------|
| T-261014-swa-01 | Tampering | parse_snackc_payload choosing an AH_ substring from item data as the cursor (garbage cursor, then null payload / wrong page) | high | mitigate | Task 1: the cursor comes only from the structural inner[2] slot when present; Task 2: the legacy fallback walks parsed values, skips media-item nodes, requires a whole-string AH_ match; decoy tests in both wire shapes |
| T-261014-swa-02 | Denial of service | AKFE cursor invisible to the AH_ regex, so every listing is truncated at 300 and the nightly is refused every run | high | mitigate | Task 1: structural read; AKFE end-to-end test 300+300+157 = 757 in 3 POSTs, with and without expected_count |
| T-261014-swa-03 | Information disclosure | Continuation cursors (capability-like) landing in error messages, logs or report emails | medium | mitigate | Task 1: the cursor-shape ProbeParseError interpolates only the length (no-echo tests); Task 2: redact_tokens collapses AH_/AKFE cursors; print sites already wrap EnumerateError text in redact_tokens |
| T-261014-swa-04 | Tampering | A future wire drift in inner[2] being guessed at instead of refused | medium | mitigate | Task 1: fullmatch against 16-4096 chars of [A-Za-z0-9_-]; anything else raises ProbeParseError, surfaced by enumerate_album as EnumerateError, so google-sync prints `google-sync failed:` and exits 1 before any frame contact |
| T-261014-swa-05 | Denial of service | Validation bounds too strict for a legitimate future cursor, blocking the nightly | low | accept | Bounds are generous (live 226, legacy 43+); failing loud with a named error that says Google changed the wire format beats guessing a cursor; fix is a parser update |
| T-261014-swa-SC | Tampering | npm/pip/cargo installs | low | accept | No package installs or dependency changes in this plan (uv.lock untouched) |
</threat_model>

<verification>
- `uv run --extra dev pytest -q -m "not live" tests/test_google_parsers.py tests/test_google_enumerate.py tests/test_probe_shared_link.py tests/test_cli_google_album.py tests/test_gsync_execute.py tests/test_gsync_engine.py` — all green.
- `uv run --extra dev pytest -q -m "not live"` — 713 pre-existing + new tests, zero failures, DeprecationWarnings still errors.
- Incident replays offline: AKFE 300+300+157 = 757 (cursor at inner[2], AH_ decoys in items, "" end); "" on a full page = tokenless transient recovered by the single retry; short page with an AKFE cursor terminal; malformed inner[2] = named EnumerateError without the cursor in its text; legacy AH_ fixture and fakes unchanged and green.
- `git diff --quiet 5b1a2b7 -- pyproject.toml pushframe/__init__.py CHANGELOG.md` — no version or changelog change.
</verification>

<success_criteria>
- The nightly's failure mode is gone: on the live AKFE wire the album "Cadre" enumerates to all 757 items by following inner[2], and no AH_-looking string inside item data can ever be sent back to Google as a cursor.
- The legacy AH_ format still enumerates; tokenless-page retry, short-page termination, null-payload retry and the SAFE-01 below-metadata gate behave exactly as before.
- The next cursor-format drift fails loud with a named error (no traceback, no guessed cursor), and cursors never appear in full in any printed or logged text.
- Full offline suite green; no version bump; live re-verification on venus belongs to the separate release step.
</success_criteria>

<output>
Create `.planning/quick/261014-swa-read-the-snackc-continuation-cursor-stru/261014-swa-SUMMARY.md` when done. Record: the structural rule chosen (slot conditions, 16-4096 bounds), the ProbeParseError-to-EnumerateError conversion, the consumer audit outcome (probes/browser_bootstrap.py, share-page fixture, legacy snAcKc fixture, docs/ — all intentionally unchanged), and the final offline test count.
</output>

"""Offline tests for the Google wire-format watch (quick-261014-tlx).

Early warning for SILENT Google protocol changes (the 2026-10-13/14 AH_ ->
AKFE cursor move): a privacy-safe structural fingerprint per run, a per-target
last-good store, diagnostics for listing failures and a consecutive-failure
streak. Every path here is offline; the wire-state root is redirected into a
tmp dir by the autouse conftest fixture.
"""
from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from pushframe.google import wire
from pushframe.google.parsers import (
    AlbumSummary,
    SnackcPage,
    _len_bucket,
    parse_album_summaries,
    parse_batchexecute,
    parse_snackc_payload,
)

FIXTURES = Path(__file__).parent / "fixtures"


def _fixture_page(name: str):
    text = (FIXTURES / name).read_text()
    entry = next(e for e in parse_batchexecute(text) if e.rpcid == "snAcKc")
    return parse_snackc_payload(entry.payload)


# --- shapes -------------------------------------------------------------------

def test_wire_shape_akfe_fixture():
    page = _fixture_page("google_rpc_snackc_page_akfe.json")
    assert page.inner_shape == "[null,list,str,list[32-63],null,int]"
    assert page.item_shape == "[str,[str,int,int],int]"
    assert page.cursor_shape == "structural:AKFE/len128-255"


def test_wire_shape_legacy_fixture():
    page = _fixture_page("google_rpc_snackc_page.json")
    assert page.inner_shape == "[item+,list[1]]"
    assert page.item_shape == "[str,[str,int,int],int]"
    assert page.cursor_shape == "legacy:AH_/len32-63"


def test_cursor_shape_end_and_none():
    structural_end = json.dumps([None, [], "", ["m"], None, 0])
    assert parse_snackc_payload(structural_end).cursor_shape == "structural:end"
    legacy_none = json.dumps([[[
        "AF1QipFAKEx" + "0" * 30,
        ["https://lh3.example/x", 1, 2], 1700000000000]]])
    assert parse_snackc_payload(legacy_none).cursor_shape == "legacy:none"


@pytest.mark.parametrize("n,expected", [
    (0, "0"), (1, "1"), (2, "2-3"), (3, "2-3"), (9, "8-15"),
    (42, "32-63"), (226, "128-255"),
])
def test_len_bucket(n, expected):
    assert _len_bucket(n) == expected


def test_snackc_page_positional_construction_keeps_defaults():
    page = SnackcPage([], None)
    assert page.inner_shape is None
    assert page.item_shape is None
    assert page.cursor_shape == "legacy:none"


def test_album_summary_row_shape():
    entry = [4, "Cadre", [["2026-01-01"]], 3, 1, "RkFLRVBBR0VLRVkwMDAx",
             None, None, "AF1QipFAKEalbum" + "0" * 30 + "1"]
    card = ["AF1QipFAKErow00" + "0" * 30, ["https://lh3.example/c", 1, 1],
            None, None, {"72930366": entry}]
    rows = parse_album_summaries([[card]])
    assert rows[0].row_shape == "row[8-15]"
    assert AlbumSummary(album_id="x", title="t").row_shape is None


def test_fingerprint_never_carries_user_data():
    cursor = "AKFE" + "SECRETCURSORBODY0123456789" * 8
    items = [["AF1QipSECRETID%04d" % i + "0" * 20,
              ["https://lh3.googleusercontent.com/pw/SECRETURL%04d" % i, 4032, 3024],
              1734567890000 + i] for i in range(5)]
    meta = ["Vacances Corse"] + [None] * 41
    inner = [None, items, cursor, meta, None, len(items)]
    page = parse_snackc_payload(json.dumps(inner))
    fp = {"inner": page.inner_shape, "cursor": page.cursor_shape,
          "item": page.item_shape, "albums": "row[8-15]"}
    rendered = wire.render_fingerprint(fp)
    blobs = [rendered, *[v for v in fp.values()]]
    for blob in blobs:
        for forbidden in ("AF1Qip", "http", "googleusercontent", "Vacances",
                          "SECRET", cursor[4:24]):
            assert forbidden not in blob, (forbidden, blob)


def test_render_diff_merge():
    fp = {"item": "I", "albums": "A", "inner": "N", "cursor": "C", "x": "ignored"}
    assert wire.render_fingerprint(fp) == "inner=N cursor=C item=I albums=A"
    assert wire.render_fingerprint({"inner": "N", "cursor": None}) == "inner=N"
    assert wire.render_fingerprint({}) == "(none)"
    assert wire.render_fingerprint(None) == "(none)"
    old = {"inner": "a", "cursor": "c", "item": "i"}
    new = {"inner": "b", "cursor": None, "item": "i", "albums": "z"}
    assert wire.fingerprint_diff(old, new) == [("inner", "a", "b")]
    assert wire.merge_fingerprint(old, new) == {
        "inner": "b", "cursor": "c", "item": "i", "albums": "z"}


# --- state paths --------------------------------------------------------------

def test_state_path_for():
    root = wire.state_root()
    assert wire.state_path_for(pair="cadre") == root / "pairs" / "cadre" / "google-wire.json"
    assert wire.state_path_for(pair="../x") is None
    a = wire.state_path_for(album_target=" Cadre ")
    b = wire.state_path_for(album_target="Cadre")
    assert a == b and a.parent == root / "google-wire"
    assert "Cadre" not in a.name
    import re
    assert re.fullmatch(r"album-[0-9a-f]{16}\.json", a.name)
    assert wire.state_path_for(album_target="") is None
    assert wire.state_path_for() is None


def test_state_root_default_is_home_at_call_time(monkeypatch, tmp_path):
    monkeypatch.setattr(wire, "STATE_ROOT_OVERRIDE", None)
    monkeypatch.setenv("HOME", str(tmp_path))
    assert wire.state_root() == tmp_path / ".local" / "state" / "pushframe"


# --- WireWatch ----------------------------------------------------------------

FP_A = {"inner": "[null,list,str,list[32-63],null,int]",
        "cursor": "structural:AKFE/len128-255",
        "item": "[str,[str,int,int],int]", "albums": "row[8-15]"}


def _watch_path(tmp_path):
    return tmp_path / "state" / "w" / "google-wire.json"


def test_watch_first_run_stores_silently(tmp_path):
    path = _watch_path(tmp_path)
    assert wire.WireWatch(path).on_listing_ok(FP_A) == []
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    data = json.loads(path.read_text())
    assert data["version"] == 1 and data["fingerprint"] == FP_A


def test_watch_same_fingerprint_is_silent(tmp_path):
    path = _watch_path(tmp_path)
    wire.WireWatch(path).on_listing_ok(FP_A)
    assert wire.WireWatch(path).on_listing_ok(dict(FP_A)) == []


def test_watch_changed_field_warns_once(tmp_path):
    path = _watch_path(tmp_path)
    wire.WireWatch(path).on_listing_ok(FP_A)
    changed = dict(FP_A, inner="[list,list[1]]")
    lines = wire.WireWatch(path).on_listing_ok(changed)
    assert len(lines) == 1
    assert lines[0].startswith("WARNING: Google response format changed:")
    assert f"inner {FP_A['inner']} → [list,list[1]]" in lines[0]
    assert "check for a pushframe update" in lines[0]
    assert wire.WireWatch(path).on_listing_ok(changed) == []
    assert wire.WireWatch(path).last_good() == changed


def test_watch_absent_field_not_compared_and_kept(tmp_path):
    path = _watch_path(tmp_path)
    wire.WireWatch(path).on_listing_ok(FP_A)
    assert wire.WireWatch(path).on_listing_ok(dict(FP_A, cursor=None)) == []
    assert wire.WireWatch(path).last_good()["cursor"] == FP_A["cursor"]


@pytest.mark.parametrize("content", [
    b"\xff\xfe",
    "[]",
    '{"version": 2}',
    '{"version": 1, "fingerprint": {"inner": "a\\nb"}}',
    '{"version": 1, "consecutive_failures": true}',
])
def test_watch_corrupt_state_is_absent_with_one_note(tmp_path, content):
    path = _watch_path(tmp_path)
    path.parent.mkdir(parents=True)
    if isinstance(content, bytes):
        path.write_bytes(content)
    else:
        path.write_text(content)
    lines = wire.WireWatch(path).on_listing_ok(FP_A)
    assert len(lines) == 1
    assert lines[0].startswith("note: Google wire-format state")
    assert "WARNING" not in lines[0]
    data = json.loads(path.read_text())
    assert data["version"] == 1 and data["fingerprint"] == FP_A


def test_watch_unsaveable_state_never_raises(tmp_path, monkeypatch):
    blocker = tmp_path / "blocker"
    blocker.write_text("i am a file")
    monkeypatch.setattr(wire, "STATE_ROOT_OVERRIDE", blocker / "sub")
    path = wire.state_path_for(pair="cadre")
    lines = wire.WireWatch(path).on_listing_ok(FP_A)
    assert len(lines) == 1
    assert lines[0].startswith("note: could not save Google wire-format state")


def test_watch_disabled():
    assert wire.WireWatch(None).on_listing_ok(FP_A) == []
    assert wire.WireWatch(None).last_good() is None


def test_report_marker_literal_matches_wire_constant():
    from pushframe import report
    assert wire.FORMAT_CHANGE_MARKER in report._PROBLEM_MARKERS


# --- diagnostics (quick-261014-tlx, TLX-2) ------------------------------------

def PageTrace(*args):  # noqa: N802 - lazy so a missing class fails per test
    from pushframe.google.enumerate import PageTrace as _PageTrace
    return _PageTrace(*args)


FP_B = dict(FP_A, cursor="legacy:AH_/len32-63")


def test_format_diagnostics_full():
    pages = [PageTrace(1, 300, "structural:AKFE/len128-255"),
             PageTrace(2, 300, "structural:end", "tokenless retry")]
    lines = wire.format_diagnostics(
        pages=pages, fingerprint=FP_A, last_good=FP_B,
        metadata_count=757, listed=300)
    assert lines[0] == wire.DIAGNOSTICS_HEADER
    assert "  page 1: 300 item(s), cursor structural:AKFE/len128-255" in lines
    p2 = next(ln for ln in lines if ln.startswith("  page 2:"))
    assert p2.endswith("— tokenless retry")
    assert any(ln.startswith("  fingerprint:") for ln in lines)
    assert any(ln.startswith("  last good run:") for ln in lines)
    assert any(ln.startswith("  changed since the last good run: cursor ")
               for ln in lines)
    assert ("  album metadata: 757 item(s), listed: 300 "
            "(shortfall tolerance 16)") in lines


def test_format_diagnostics_edges():
    lines = wire.format_diagnostics(pages=[], fingerprint=None, last_good=None)
    assert "  pages: none fetched" in lines
    assert any("(none stored)" in ln for ln in lines)
    same = wire.format_diagnostics(pages=[], fingerprint=FP_A, last_good=FP_A)
    assert "  unchanged since the last good run" in same
    hidden = wire.format_diagnostics(pages=[], fingerprint=FP_A, last_good=FP_B,
                                     show_last_good=False)
    assert not any("last good run" in ln for ln in hidden)
    many = [PageTrace(i, 300, "structural:AKFE/len128-255") for i in range(1, 21)]
    out = wire.format_diagnostics(pages=many, fingerprint=FP_A)
    page_lines = [ln for ln in out if ln.startswith("  page ")]
    assert [int(ln.split(":")[0].split()[1]) for ln in page_lines] == \
        [1, 2, 3, 4, 5, 6, 16, 17, 18, 19, 20]
    assert "  … 9 more page(s)" in out
    assert out.index("  … 9 more page(s)") < out.index(page_lines[6])


def test_format_diagnostics_redacts():
    secret_id = "AF1QipSECRETIDBODY" + "0" * 40
    cursor = "AKFE" + "CURSORBODYXYZ" * 17
    lines = wire.format_diagnostics(
        pages=[PageTrace(1, 0, "-", f"failed: {secret_id} {cursor}")],
        fingerprint=FP_A)
    text = "\n".join(lines)
    assert "SECRETIDBODY" not in text
    assert "CURSORBODYXYZ" not in text


# --- failure streak (quick-261014-tlx, TLX-3) ---------------------------------

def test_failure_class_mapping():
    from pushframe.google.enumerate import EnumerateError
    from pushframe.gsync import SafeSyncError
    assert wire.failure_class(EnumerateError("x", kind="null-payload")) == \
        "enumerate:null-payload"
    assert wire.failure_class(SafeSyncError.truncated_listing()) == "listing:truncated"
    assert wire.failure_class(SafeSyncError.empty_listing()) == "listing:empty"
    assert wire.failure_class(SafeSyncError.listing_below_metadata(3, 757)) == \
        "listing:below-metadata"
    assert wire.failure_class(SafeSyncError.manifest_drift(())) is None
    assert wire.failure_class(SafeSyncError.empty_frame_listing()) is None
    assert wire.failure_class(ValueError()) is None


def test_escalation_marker_literal():
    assert wire.ESCALATION_MARKER == \
        "probable Google protocol change — pushframe likely needs an update"


def test_streak_first_then_escalates(tmp_path):
    path = _watch_path(tmp_path)
    first = wire.WireWatch(path).on_failure("listing:truncated", FP_A)
    assert len(first) == 1
    assert first[0].startswith('note: first "listing:truncated" failure in a row')
    assert "may be a transient Google answer" in first[0]
    second = wire.WireWatch(path).on_failure("listing:truncated", FP_A)
    assert second[0].startswith(
        "ATTENTION: probable Google protocol change — pushframe likely needs an update")
    assert '"listing:truncated" failed 2 runs in a row' in second[0]
    third = wire.WireWatch(path).on_failure("listing:truncated", FP_A)
    assert "3 runs in a row" in third[0]


def test_streak_class_change_restarts(tmp_path):
    path = _watch_path(tmp_path)
    wire.WireWatch(path).on_failure("listing:truncated", FP_A)
    lines = wire.WireWatch(path).on_failure("listing:below-metadata", FP_A)
    assert lines[0].startswith('note: first "listing:below-metadata" failure')


def test_success_resets_streak(tmp_path):
    path = _watch_path(tmp_path)
    wire.WireWatch(path).on_failure("listing:truncated", FP_A)
    wire.WireWatch(path).on_listing_ok(FP_A)
    lines = wire.WireWatch(path).on_failure("listing:truncated", FP_A)
    assert lines[0].startswith('note: first "listing:truncated" failure')


def test_failures_never_touch_last_good(tmp_path):
    path = _watch_path(tmp_path)
    wire.WireWatch(path).on_listing_ok(FP_A)
    wire.WireWatch(path).on_failure("listing:truncated", FP_B)
    lines = wire.WireWatch(path).on_failure("listing:truncated", FP_B)
    assert wire.WireWatch(path).last_good() == FP_A
    diff_line = next(ln for ln in lines if ln.startswith(
        "  wire fingerprint changed since the last good run: cursor "))
    assert f"{FP_A['cursor']} → {FP_B['cursor']}" in diff_line
    for same in (FP_A, None):
        out = wire.WireWatch(path).on_failure("listing:truncated", same)
        assert not any("wire fingerprint changed" in ln for ln in out)


def test_uncounted_failure_is_a_noop(tmp_path):
    path = _watch_path(tmp_path)
    assert wire.WireWatch(path).on_failure(None, FP_A) == []
    assert not path.exists()


def test_streak_with_corrupt_state(tmp_path):
    path = _watch_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_bytes(b"\xff garbage")
    lines = wire.WireWatch(path).on_failure("listing:truncated", FP_A)
    assert lines[0].startswith("note: Google wire-format state")
    assert any(ln.startswith('note: first "listing:truncated" failure')
               for ln in lines)

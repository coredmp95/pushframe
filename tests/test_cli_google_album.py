"""Offline tests for `pushframe google-album` (plan 17-02 T2, reworked to the
RPC-first live-proven protocol).

Zero network: resolution runs against AlbumSummary fakes (pure function);
the full command runs with a real GoogleSession over a MockTransport
replaying the live-proven flow — /albums ds:5 (real row shape) → snAcKc
batch-1 with NULL continuation → token-swap pages → =d sizes. Redaction is
proven by grepping stdout for full AF1Qip tokens.
"""
from __future__ import annotations

import base64
import json
import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pushframe.cli import (  # noqa: E402
    AlbumResolution,
    resolve_album,
    run_google_album,
)
from pushframe.google import enumerate as _enum  # noqa: E402
from pushframe.google.client import GoogleSession  # noqa: E402
from pushframe.google.parsers import AlbumSummary  # noqa: E402

ALBUM_ID_1 = "AF1QipFAKEalbumONE" + "0" * 28 + "1"
ALBUM_ID_2 = "AF1QipFAKEalbumTWO" + "0" * 28 + "2"
ALBUM_ID_3 = "AF1QipFAKEalbumTHR" + "0" * 28 + "3"
PAGE_KEY_1 = "FAKEKEYONE0001"
ALBUMS = [
    AlbumSummary(album_id=ALBUM_ID_1, title="Vacances Corse", item_count=24),
    AlbumSummary(album_id=ALBUM_ID_2, title="Vacances Bretagne", item_count=5),
    AlbumSummary(album_id=ALBUM_ID_3, title="Famille 2024", item_count=12),
]


def test_resolve_album_by_unique_substring():
    r = resolve_album("corse", ALBUMS)
    assert isinstance(r, AlbumResolution)
    assert r.status == "resolved" and r.album.album_id == ALBUM_ID_1


def test_resolve_album_ambiguous_lists_candidates():
    r = resolve_album("vacances", ALBUMS)
    assert r.status == "ambiguous" and len(r.candidates) == 2


def test_resolve_album_not_found_carries_all_albums():
    r = resolve_album("alpes", ALBUMS)
    assert r.status == "not_found" and len(r.candidates) == 3


def test_resolve_album_direct_link_bypasses_names():
    r = resolve_album(f"https://photos.google.com/share/{ALBUM_ID_1}?key=K1", ALBUMS)
    assert r.status == "resolved" and r.album.album_id == ALBUM_ID_1


def test_resolve_album_direct_id_bypasses_names():
    r = resolve_album(ALBUM_ID_3, ALBUMS)
    assert r.status == "resolved"


# --- Full command over a stateful MockTransport ------------------------------

def _albums_html() -> str:
    """A synthetic /albums page whose ds:5 uses the LIVE-PROVEN row shape:
    [cover_id, [cover_url, w, h, …], …, {<key>: [4, title, [dates], count,
    1, page_key_b64, …, share_token, …]}]."""
    entries = []
    for a in ALBUMS:
        key_b64 = base64.b64encode(f"key-{a.album_id[-4:]}".encode()).decode()
        entry = ('["AF1QipFAKEcover' + a.album_id[-4:] +
                 '00000000000000000000000000000' +
                 '", ["https://lh3.googleusercontent.com/pw/FAKEcover", 1, 2], '
                 'null, null, {"72930366": [4, "' + a.title +
                 '", [1700000000000], ' + str(a.item_count) +
                 ', 1, "' + key_b64 +
                 '", null, ["x"], "' + a.album_id +
                 '", ["sig"]]}]')
        entries.append(entry)
    data_literal = "[[" + ", ".join(entries) + "]]"
    return (
        '<html><body><script>window.WIZ_global_data = '
        '{"SNlM0e": "SYNTH-AT", "FdrFJe": "123", "cfb2h": "boq_bl", '
        '"oPEP7c": "someone@example.com"};</script>'
        "<script>AF_initDataCallback({key: 'ds:5', hash: '1', data:"
        + data_literal + '});</script>'
        '</body></html>'
    )


def _rpc_items(page: int, count: int) -> list:
    # Real Google media ids run ~48 chars after the AF1Qip prefix; keep the
    # synthetic ids above redact_link's 40-char threshold so the redaction
    # assertions below exercise the real shape, not a threshold edge.
    return [[f"AF1QipRPC{page:02d}{i:06d}" + "0" * 32,
             [f"https://lh3.googleusercontent.com/pw/FAKEp{page:02d}i{i:06d}",
              100 + i, 200 + i],
             1700000000000 + i]
            for i in range(1, count + 1)]


def _rpc_response(items: list, next_token: str | None) -> str:
    payload = [items]
    if next_token:
        payload.append([next_token])
    inner = json.dumps(payload, separators=(",", ":"))
    line = json.dumps([["wrb.fr", "snAcKc", inner, None, "generic"]],
                      separators=(",", ":"))
    return ")]}'\n\n" + line + "\n"


def _router():
    state = {"posted": 0, "expected_token": None}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == "GET" and path == "/":
            return httpx.Response(200, text=_albums_html())
        if request.method == "GET" and path == "/albums":
            return httpx.Response(200, text=_albums_html())
        if request.method == "GET" and path.endswith("=d"):
            num = int(path.removesuffix("=d").rsplit("i", 1)[-1])
            return httpx.Response(206, text="x",
                                  headers={"Content-Range": f"bytes 0-0/{num * 10 + 3}"})
        if request.method == "POST":
            state["posted"] += 1
            # Validate the client's protocol side: triple-nested envelope,
            # NULL batch-1 continuation, then a correct AH_ token chain.
            from urllib.parse import unquote
            parts = dict(p.split("=", 1) for p in request.content.decode().split("&")
                         if "=" in p)
            arr = json.loads(unquote(parts["f.req"]))
            inner = json.loads(arr[0][0][1])
            if inner[1] != state["expected_token"]:
                return httpx.Response(400, text="stale continuation")
            if inner[3] != f"key-{ALBUM_ID_1[-4:]}":
                return httpx.Response(400, text="wrong page_key")
            page = state["posted"]
            has_next = page < 2
            tok = ("AH_" + "N" * 40 + f"{page + 1:04d}") if has_next else None
            state["expected_token"] = tok
            return httpx.Response(200, text=_rpc_response(_rpc_items(page, 5), tok))
        return httpx.Response(404)

    return handler


def _session() -> GoogleSession:
    return GoogleSession([{"name": "SID", "value": "x", "domain": ".google.com",
                           "path": "/"}],
                         transport=httpx.MockTransport(_router()))


def test_google_album_by_name_full_enumeration_and_disk_weight(capsys, monkeypatch):
    # The synthetic pages carry 5 items each — the full-page size, so the
    # short-page-terminal rule (live drift 2026-10-01) never fires early.
    monkeypatch.setattr(_enum, "PAGE_SIZE", 5)
    rc = run_google_album("corse", session=_session())
    assert rc == 0
    out = capsys.readouterr().out
    # Resolution echo (redacted id shape), enumeration totals, disk weight.
    assert "Album: Vacances Corse" in out
    assert "Items: 10" in out          # 5 + 5 across 2 snAcKc pages
    assert "exhausted: cleanly" in out
    assert "Disk weight:" in out and "MiB" in out
    # Per-item table with redacted id shapes only.
    assert "Per-item" in out
    assert ALBUM_ID_1 not in out       # full tokens never print
    assert "AF1QipRPC01" + "0" * 32 not in out
    assert "AF1Qip…" in out            # the redacted shape does print


def test_google_album_ambiguous_prints_numbered_and_exits_2(capsys):
    rc = run_google_album("vacances", session=_session())
    assert rc == 2
    out = capsys.readouterr().out
    assert "more than one album" in out
    assert "1. Vacances Corse" in out
    assert "2. Vacances Bretagne" in out


def test_google_album_not_found_prints_albums_and_exits_2(capsys):
    rc = run_google_album("alpes", session=_session())
    assert rc == 2
    out = capsys.readouterr().out
    assert "No album matches" in out
    assert "Famille 2024" in out


def test_google_album_by_direct_link_resolves_and_enumerates(capsys, monkeypatch):
    """A pasted share link works: the id is extracted and the page_key from
    the listing (when the link lacks one) comes from the /albums match."""
    monkeypatch.setattr(_enum, "PAGE_SIZE", 5)   # synthetic full pages = 5 items
    rc = run_google_album(f"https://photos.google.com/share/{ALBUM_ID_1}",
                          session=_session())
    assert rc == 0
    out = capsys.readouterr().out
    assert "Items: 10" in out


def test_google_album_list_flag_prints_numbered_albums_with_counts(capsys):
    rc = run_google_album(None, session=_session(), list_all=True)
    assert rc == 0
    out = capsys.readouterr().out
    assert "3 shared albums:" in out
    assert "1. Vacances Corse" in out
    assert "3. Famille 2024" in out
    assert "24 items (metadata)" in out
    assert ALBUM_ID_1 not in out       # redaction holds on the listing too


def test_google_album_requires_target_without_list(capsys):
    rc = run_google_album(None, session=_session())
    assert rc == 2
    assert "--list" in capsys.readouterr().out


def test_google_album_without_vault_fails_cleanly(monkeypatch, capsys):
    """No vault (and no injected session) -> clean failure, exit 1, and no
    network reach whatsoever (the vault paths are pointed at nothing)."""
    monkeypatch.setattr("pushframe.google.vault.DEFAULT_VAULT_PATH",
                        Path("/nonexistent/prod-vault.json"))
    monkeypatch.setattr("pushframe.google.vault.LEGACY_VAULT_PATH",
                        Path("/nonexistent/legacy-vault.json"))
    rc = run_google_album("anything")  # no session injected; vault absent
    assert rc == 1
    assert "google-album failed" in capsys.readouterr().out

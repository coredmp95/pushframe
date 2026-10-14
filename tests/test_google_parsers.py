"""Offline tests for the package parsers (migrated from
tests/test_probe_shared_link.py — plan 17-01 T1).

Zero network: fixtures are fully synthetic (authored, never recorded from
the live site). Covers the share-page ds:1 parser, the snAcKc payload
parser, the batchexecute envelope splitter and the redaction helpers.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pushframe.google.parsers import (  # noqa: E402
    ProbeParseError,
    parse_af_initdata,
    parse_batchexecute,
    parse_snackc_payload,
)
from pushframe.google.redaction import redact_link, redact_tokens  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"
SHARE_PAGE = FIXTURES / "google_share_page_sample.html"
SNACKC_PAGE = FIXTURES / "google_rpc_snackc_page.json"
SNACKC_AKFE_PAGE = FIXTURES / "google_rpc_snackc_page_akfe.json"

# Live 2026-10-14 cursor shape (synthetic): AKFE + 222 URL-safe chars = 226.
AKFE_FIXTURE_CURSOR = "AKFE" + "FAKECURSOR" * 22 + "01"

# Fully synthetic share URL for redaction tests — shape-valid, never real.
FAKE_FULL_URL = (
    "https://photos.google.com/share/"
    "AF1QipSYNTHsynthSYNTHsynthSYNTHsynthSYNTHsynth0123"
)

EXPECTED_IDS = [
    "AF1QipFAKEitem0000000000000000000000000000001",
    "AF1QipFAKEitem0000000000000000000000000000002",
    "AF1QipFAKEitem0000000000000000000000000000003",
    "AF1QipFAKEitem0000000000000000000000000000004",
]


def test_parse_share_page_fixture_returns_all_items():
    items = parse_af_initdata(SHARE_PAGE.read_text())
    assert len(items) == 4, f"expected 4 items, got {len(items)}"
    assert [i["id"] for i in items] == EXPECTED_IDS
    assert all(i["base_url"].startswith("https://lh3.googleusercontent.com/pw/FAKE")
               for i in items)
    assert items[0]["width"] == 4898 and items[0]["height"] == 3265
    assert items[0]["ts_ms"] == 1532210429477


def test_parse_handles_missing_ds1():
    with pytest.raises(ProbeParseError) as exc:
        parse_af_initdata("<html><body>no data here</body></html>")
    assert "ds:1" in str(exc.value), "error must name the missing key (fail-loud)"


def test_parse_snackc_payload_fixture():
    """The snAcKc inner payload walks with the SAME item shape as the share
    page (live-proven duality), and the LAST AH_ token is the cursor."""
    text = SNACKC_PAGE.read_text()
    entries = parse_batchexecute(text)
    assert len(entries) == 1
    assert entries[0].rpcid == "snAcKc"
    page = parse_snackc_payload(entries[0].payload)
    assert [i["id"] for i in page.items] == [
        "AF1QipFAKErpcitem000000000000000000000000001",
        "AF1QipFAKErpcitem000000000000000000000000002",
        "AF1QipFAKErpcitem000000000000000000000000003",
    ]
    assert page.items[0]["base_url"].startswith("https://lh3.googleusercontent.com/pw/FAKErpc")
    assert page.continuation_token == "AH_0000000000000000000000000000000000000000FAKECURSOR0001"


def test_parse_snackc_without_token_is_exhausted():
    payload = '[["AF1QipFAKEitem0000000000000000000000000000099",["https://x.example/pw/FAKE",1,2],1]]'
    page = parse_snackc_payload(payload)
    assert len(page.items) == 1
    assert page.continuation_token is None  # clean exhaustion signal


def test_parse_snackc_null_payload_fails_loud():
    with pytest.raises(ProbeParseError):
        parse_snackc_payload(None)
    with pytest.raises(ProbeParseError):
        parse_snackc_payload("not json at all")


def test_parse_batchexecute_empty_body_fails_loud():
    with pytest.raises(ProbeParseError):
        parse_batchexecute(")]}'\n\n")


def test_redact_link_never_emits_full_url():
    redacted = redact_link(FAKE_FULL_URL)
    # Shape convention: domain path kept, token collapsed to AF1Qip…<last4>.
    assert redacted == "photos.google.com/share/AF1Qip…0123"
    # The full token must be absent from the output.
    assert "AF1QipSYNTHsynth" not in redacted
    # Defensive: even raw token text gets collapsed.
    token_only = FAKE_FULL_URL.rsplit("/", 1)[1]
    assert redact_tokens(token_only) == "AF1Qip…0123"


# --- SWA: structural inner[2] cursor (live 2026-10-14 AKFE wire) -------------

META42 = ["FAKE album"] + [None] * 41


def _akfe_item(n: int, *, sig=None, base=None) -> list:
    return [f"AF1QipFAKEstruct{n:04d}" + "0" * 30,
            [base or f"https://lh3.googleusercontent.com/pw/FAKEstruct{n:04d}", 10, 20],
            1700000000000 + n, sig or f"qsig{n}"]


def _payload(inner) -> str:
    return json.dumps(inner, separators=(",", ":"))


def _decoy_items() -> list:
    return [
        _akfe_item(1, sig="AH_" + "D" * 44),
        _akfe_item(2, base="https://lh3.googleusercontent.com/pw/AH_" + "U" * 44),
    ]


def test_parse_snackc_akfe_fixture_reads_cursor_from_inner2():
    entries = [e for e in parse_batchexecute(SNACKC_AKFE_PAGE.read_text())
               if e.rpcid == "snAcKc"]
    assert len(entries) == 1
    page = parse_snackc_payload(entries[0].payload)
    assert [i["id"][-2:] for i in page.items] == ["01", "02", "03"]
    assert page.continuation_token == AKFE_FIXTURE_CURSOR
    assert len(page.continuation_token) == 226
    assert not page.continuation_token.startswith("AH_")


def test_parse_snackc_structural_cursor_beats_ah_decoys():
    inner = [None, _decoy_items(), AKFE_FIXTURE_CURSOR, META42, None, 2]
    page = parse_snackc_payload(_payload(inner))
    assert page.continuation_token == AKFE_FIXTURE_CURSOR
    assert len(page.items) == 2


def test_parse_snackc_structural_empty_cursor_is_end_even_with_ah_decoys():
    inner = [None, _decoy_items(), "", META42, None, 2]
    page = parse_snackc_payload(_payload(inner))
    assert page.continuation_token is None
    assert len(page.items) == 2


def test_parse_snackc_structural_null_cursor_is_end():
    inner = [None, _decoy_items(), None, META42, None, 2]
    page = parse_snackc_payload(_payload(inner))
    assert page.continuation_token is None
    assert len(page.items) == 2


@pytest.mark.parametrize("bad", [
    "AKFE" + "x" * 40 + "!",
    "AKFE" + "x" * 40 + "/+=",
    "AKFE" + "x" * 20 + " " + "x" * 20,
    "AKFE" + "A" * 5000,
    "AKFEshort1",
], ids=["bang", "slash-plus-eq", "space", "too-long", "too-short"])
def test_parse_snackc_malformed_cursor_fails_loud_without_echo(bad):
    inner = [None, _decoy_items(), bad, META42, None, 2]
    with pytest.raises(ProbeParseError) as exc:
        parse_snackc_payload(_payload(inner))
    msg = str(exc.value)
    assert "inner[2]" in msg
    assert bad[:60] not in msg
    assert bad not in msg


# --- SWA: item-safe legacy fallback + cursor redaction -----------------------

def test_parse_snackc_legacy_fallback_ignores_item_embedded_ah_strings():
    inner = [[
        "AF1QipFAKElegacy" + "0" * 30,
        ["https://lh3.googleusercontent.com/pw/AH_" + "U" * 44, 1, 2],
        1,
        "AH_" + "D" * 44,
    ]]
    page = parse_snackc_payload(_payload(inner))
    assert page.continuation_token is None
    assert len(page.items) == 1


def test_parse_snackc_legacy_fallback_standalone_token_beats_later_item_decoys():
    real = "AH_" + "R" * 40 + "0001"
    inner = [[real], [_akfe_item(1, sig="AH_" + "D" * 44)]]
    page = parse_snackc_payload(_payload(inner))
    assert page.continuation_token == real


def test_parse_snackc_legacy_fallback_last_standalone_token_wins():
    inner = [[_akfe_item(1)], ["AH_" + "R" * 40 + "0001"], ["AH_" + "R" * 40 + "0002"]]
    page = parse_snackc_payload(_payload(inner))
    assert page.continuation_token == "AH_" + "R" * 40 + "0002"


def test_redact_tokens_collapses_akfe_and_ah_cursors():
    akfe = "AKFE" + "K" * 218 + "WXYZ"
    ah = "AH_" + "N" * 40 + "0001"
    out = redact_tokens(f"cursor={akfe} next={ah}")
    assert out == "cursor=AKFE…WXYZ next=AH_…0001"
    assert akfe not in out and ah not in out


def test_redact_tokens_mixed_is_idempotent_and_keeps_af1qip():
    af = "AF1Qip" + "x" * 44 + "ABCD"
    akfe = "AKFE" + "K" * 218 + "WXYZ"
    ah = "AH_" + "N" * 40 + "0001"
    s = f"{af} {akfe} {ah}"
    out = redact_tokens(s)
    assert out == "AF1Qip…ABCD AKFE…WXYZ AH_…0001"
    assert redact_tokens(out) == out


def test_redact_tokens_leaves_short_prose_alone():
    assert redact_tokens("AKFE cursor, AH_ token") == "AKFE cursor, AH_ token"

"""Offline tests for the package parsers (migrated from
tests/test_probe_shared_link.py — plan 17-01 T1).

Zero network: fixtures are fully synthetic (authored, never recorded from
the live site). Covers the share-page ds:1 parser, the snAcKc payload
parser, the batchexecute envelope splitter and the redaction helpers.
"""
from __future__ import annotations

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

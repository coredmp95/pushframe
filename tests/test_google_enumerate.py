"""Offline tests for the RPC-first snAcKc enumerator and disk-weight
measurer (plan 17-01 T2, reworked after the 2026-09-28 live validation).

Zero network: a stateful MockTransport router replays the live-proven
protocol — batch-1 via a NULL continuation (the share page is never
fetched), triple-nested f.req envelope, AH_ token swap, clean exhaustion —
over synthetic pages of 300+300+194 = 794 items (the live-confirmed shape,
LGS-05).
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from urllib.parse import unquote

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pushframe.google.client import GoogleSession  # noqa: E402
from pushframe.google.enumerate import (  # noqa: E402
    EnumerateError,
    enumerate_album,
    measure_disk_weight,
)

# The live-observed transient: HTTP 200, well-formed wrb.fr/snAcKc entry,
# null inner payload. A null answer consumed no page — the router counts
# these separately and re-serves the same page when the client retries.
NULL_PAYLOAD_RESPONSE = (
    ")]}'\n\n" + json.dumps(
        [["wrb.fr", "snAcKc", None, None, "generic"]], separators=(",", ":")
    ) + "\n"
)

ALBUM_ID = "AF1QipFAKEalbum" + "0" * 30 + "1"
PAGE_KEY = "FAKEPAGEKEY0001"

HOME_HTML = (
    "<html><body><script>window.WIZ_global_data = "
    '{"SNlM0e": "SYNTH-AT", "FdrFJe": "12345", "cfb2h": "boq_test_bl", '
    '"oPEP7c": "someone@example.com"};</script></body></html>'
)

COOKIES = [{"name": "SID", "value": "fake-sid", "domain": ".google.com", "path": "/"}]


def _next_token(n: int) -> str:
    return "AH_" + "N" * 40 + f"{n:04d}"


def _rpc_items(page: int, count: int) -> list:
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


class _GoogleRouter:
    """Stateful MockTransport replaying the live-proven RPC-first protocol:
    it VALIDATES the client's side (triple-nested envelope, NULL batch-1
    continuation, token chain, page_key, Range headers) and fails the
    request when the client deviates."""

    def __init__(self, pages: list[int], *, page_key: str | None = PAGE_KEY,
                 rpc_status: int = 200, rpc_body: str | None = None,
                 albums_page: str | None = None,
                 null_positions: set[int] | None = None,
                 token_on_last_page: bool = False) -> None:
        self.pages = pages
        self.page_key = page_key
        self.rpc_status = rpc_status
        self.rpc_body = rpc_body
        self.albums_page = albums_page
        # 1-based POST indices that answer the live-observed null-payload
        # transient instead of a page (a null consumes nothing).
        self.null_positions = null_positions or set()
        # Live drift (2026-10-01, the 757-item "Cadre" album): Google emits
        # a continuation token even on a SHORT final page; requesting past
        # it answers the null-payload shape.
        self.token_on_last_page = token_on_last_page
        self.albums_gets = 0
        self.post_count = 0
        self.nulls_served = 0
        self.size_requests: list[tuple[str, str]] = []
        self.last_freq: str | None = None
        self.freq_inners: list[list] = []  # decoded args of every f.req POST
        self._expected_token: str | None = None  # batch-1 = NULL continuation
        self._pages_served = 0

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == "GET" and path == "/":
            return httpx.Response(200, text=HOME_HTML)
        if request.method == "GET" and path == "/albums":
            self.albums_gets += 1
            return httpx.Response(200, text=self.albums_page or HOME_HTML)
        if request.method == "GET" and path.endswith("=d"):
            num = int(re.search(r"(\d+)$", path.removesuffix("=d")).group(1))
            self.size_requests.append((path, request.headers.get("range", "")))
            return httpx.Response(206, text="x",
                                  headers={"Content-Range": f"bytes 0-0/{num * 1000 + 7}"})
        if request.method == "POST" and "batchexecute" in path:
            self.post_count += 1
            if self.rpc_status != 200:
                return httpx.Response(self.rpc_status, text="server exploded")
            if self.rpc_body is not None:
                return httpx.Response(200, text=self.rpc_body)
            parts = dict(p.split("=", 1) for p in request.content.decode().split("&")
                         if "=" in p)
            self.last_freq = unquote(parts["f.req"])
            arr = json.loads(self.last_freq)
            # TRIPLE nesting is the live-proven envelope (double = HTTP 400).
            if not (isinstance(arr, list) and len(arr) == 1
                    and isinstance(arr[0], list) and len(arr[0]) == 1
                    and isinstance(arr[0][0], list) and arr[0][0][0] == "snAcKc"):
                return httpx.Response(400, text="bad envelope")
            inner = json.loads(arr[0][0][1])
            self.freq_inners.append(inner)
            if inner[0] != ALBUM_ID:
                return httpx.Response(400, text="wrong album id")
            if inner[1] != self._expected_token:
                return httpx.Response(400, text="stale continuation token")
            if inner[3] != self.page_key:
                return httpx.Response(400, text="wrong page_key")
            # Scheduled null answers are served INSTEAD of a page and consume
            # nothing: no page advances, no token is minted — mirroring the
            # live transient, where the retry re-issued the identical call.
            if self.post_count in self.null_positions:
                self.nulls_served += 1
                return httpx.Response(200, text=NULL_PAYLOAD_RESPONSE)
            page_idx = self._pages_served
            self._pages_served += 1
            if page_idx >= len(self.pages):
                return httpx.Response(400, text="too many pages requested")
            has_next = page_idx + 1 < len(self.pages)
            if has_next or (self.token_on_last_page
                            and page_idx + 1 == len(self.pages)):
                next_token = _next_token(page_idx + 2)
            else:
                next_token = None
            self._expected_token = next_token
            return httpx.Response(200, text=_rpc_response(
                _rpc_items(page_idx + 1, self.pages[page_idx]), next_token))
        return httpx.Response(404)


def _session(router: _GoogleRouter) -> GoogleSession:
    return GoogleSession(COOKIES, transport=httpx.MockTransport(router.handler))


def test_enumerate_album_794_items_clean_exhaustion():
    """The live-confirmed shape: 4 snAcKc pages (300+300+300+194 on the live
    1094-photo album; the synthetic mirror is 300+300+194 = 794), exhausted
    cleanly, no live network (LGS-05, TEST-02)."""
    router = _GoogleRouter([300, 300, 194])
    listing = enumerate_album(_session(router), ALBUM_ID, page_key=PAGE_KEY)
    assert len(listing.items) == 794
    assert len({i["id"] for i in listing.items}) == 794  # all unique
    assert listing.exhausted_cleanly is True
    assert listing.page_count == 3
    assert listing.album_id == ALBUM_ID
    assert router.post_count == 3
    assert router.last_freq is not None
    # The envelope the client actually sent is triple-nested compact JSON.
    assert json.loads(router.last_freq)[0][0][0] == "snAcKc"


def test_enumerate_batch1_uses_null_continuation():
    """The RPC-first discovery: batch-1 IS a snAcKc call with a NULL
    continuation — the router rejects any non-null first token with a 400,
    so passing enumeration proves the null form."""
    router = _GoogleRouter([300])
    listing = enumerate_album(_session(router), ALBUM_ID, page_key=PAGE_KEY)
    assert len(listing.items) == 300
    inner = json.loads(json.loads(router.last_freq)[0][0][1])
    assert inner[1] is None or inner[1] != ""  # last call carried the swap
    assert router.post_count == 1


def test_enumerate_token_swap_chain_validated_by_router():
    """The router rejects stale tokens (400) — a passing enumeration proves
    the client swapped AH_ cursors correctly on every page."""
    router = _GoogleRouter([300, 194])
    listing = enumerate_album(_session(router), ALBUM_ID, page_key=PAGE_KEY)
    assert len(listing.items) == 494
    assert router.post_count == 2


def test_enumerate_works_without_page_key():
    """page_key is OPTIONAL (live-proven: null page_key still answers 300
    items on the 1096-item album); the router asserts the 4th arg is None."""
    router = _GoogleRouter([300], page_key=None)
    listing = enumerate_album(_session(router), ALBUM_ID)
    assert len(listing.items) == 300


def test_enumerate_fails_loud_on_http_400():
    router = _GoogleRouter([300], rpc_status=400)
    with pytest.raises(EnumerateError, match="HTTP 400"):
        enumerate_album(_session(router), ALBUM_ID, page_key=PAGE_KEY)


def test_enumerate_fails_loud_on_malformed_envelope():
    router = _GoogleRouter([300], rpc_body=")]}'\nnot json at all\n")
    with pytest.raises(RuntimeError):
        enumerate_album(_session(router), ALBUM_ID, page_key=PAGE_KEY)


def test_enumerate_requires_album_id():
    router = _GoogleRouter([])
    with pytest.raises(EnumerateError, match="album_id is required"):
        enumerate_album(_session(router), "")


def test_enumerate_page_cap_fails_loud_incomplete():
    router = _GoogleRouter([300] * 5)
    with pytest.raises(EnumerateError, match="INCOMPLETE"):
        enumerate_album(_session(router), ALBUM_ID, page_key=PAGE_KEY, max_pages=2)


def test_measure_disk_weight_content_range():
    """1-octet Range GETs → exact Content-Range totals (live-proven sizing)."""
    router = _GoogleRouter([])
    session = _session(router)
    base_urls = [f"https://lh3.googleusercontent.com/pw/FAKEi{i:06d}"
                 for i in (1, 2, 3)]
    sizes = measure_disk_weight(session, base_urls)
    assert sizes == [1007, 2007, 3007]
    for _path, range_header in router.size_requests:
        assert range_header == "bytes=0-0"


def test_measure_disk_weight_zero_when_no_size_headers():
    session = GoogleSession(COOKIES, transport=httpx.MockTransport(
        lambda request: httpx.Response(200)))
    assert measure_disk_weight(session, ["https://lh3.example/pw/FAKEnoheader"]) == [0]


def test_null_payload_retried_once_same_cursor_then_recovers():
    """The live-observed transient (2026-09-28 smoke): HTTP 200 carrying a
    well-formed wrb.fr/snAcKc entry whose payload is null. The client re-issues
    the exact same call — a null answer consumed no page, so the router
    re-serves page 1 — and recovers instead of failing (LGS-05 hardening)."""
    router = _GoogleRouter([300], null_positions={1})
    listing = enumerate_album(_session(router), ALBUM_ID, page_key=PAGE_KEY)
    assert len(listing.items) == 300
    assert listing.exhausted_cleanly is True
    assert router.post_count == 2           # 1 null answer + 1 retry
    assert router.nulls_served == 1
    # The retry re-issued the IDENTICAL call: same album id, cursor, page_key.
    assert router.freq_inners[0] == router.freq_inners[1]


def test_null_persisting_across_the_single_retry_fails_loud():
    """The null transient recurring on the immediate retry fails loud — the
    bounded retry is a recovery, never a loop (fail-loud over partial data,
    T-17-02)."""
    router = _GoogleRouter([300], null_positions={1, 2})
    with pytest.raises(EnumerateError, match="single bounded retry"):
        enumerate_album(_session(router), ALBUM_ID, page_key=PAGE_KEY)
    assert router.post_count == 2           # null, retry (null again), stop
    assert router.nulls_served == 2


def test_null_retry_budget_is_global_per_enumeration():
    """ONE retry per run, not per page: after the budget recovers batch-1,
    a second null on page 2 fails loud instead of retrying again — a
    per-page budget would have recovered and returned 600 items here."""
    router = _GoogleRouter([300, 300], null_positions={1, 3})
    with pytest.raises(EnumerateError, match="single bounded retry"):
        enumerate_album(_session(router), ALBUM_ID, page_key=PAGE_KEY)
    assert router.nulls_served == 2   # batch-1 + the later page
    assert router.post_count == 3     # null, recovered retry, null — no 4th


def test_null_retry_recovers_on_later_page_within_budget():
    """The budget is GLOBAL, not first-page-only: a null hitting page 2's
    first call is recovered the same way batch-1's would be (and the global
    test above proves any second null then fails loud)."""
    router = _GoogleRouter([300, 194], null_positions={2})
    listing = enumerate_album(_session(router), ALBUM_ID, page_key=PAGE_KEY)
    assert len(listing.items) == 494
    assert listing.page_count == 2
    assert listing.exhausted_cleanly is True
    assert router.post_count == 3      # page 1, null, retried page 2


def test_short_page_is_terminal_even_when_google_mints_a_token():
    """The 2026-10-01 live drift (the 757-item 'Cadre' album): Google emits
    a continuation token even on a SHORT final page (157 < 300), and the
    call past it answers the null-payload shape — which the old loop read
    as the September transient, retried once, and failed loud, hard-blocking
    the user. A short page IS the end of the listing: stop there, exhausted
    cleanly, and never request the past-the-end page."""
    router = _GoogleRouter([300, 300, 157], token_on_last_page=True)
    listing = enumerate_album(_session(router), ALBUM_ID, page_key=PAGE_KEY)
    assert len(listing.items) == 757            # 300 + 300 + 157, live counts
    assert listing.exhausted_cleanly is True
    assert listing.page_count == 3
    assert router.post_count == 3               # the null page is never requested
    assert router.nulls_served == 0

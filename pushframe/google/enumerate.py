"""Album enumeration and disk-weight measurement over photos.google.com.

RPC-FIRST protocol (live-proven 2026-09-28, this session — supersedes the
phase-16 share-page bootstrap for the linked flow):

- Batch-1 IS a snAcKc call with a NULL continuation:
  `snAcKc(album_id, null, null, page_key)` answers HTTP 200 with the first
  300 items — the share page is never fetched. (The authenticated share
  page is an SPA shell: no ds:1 block, no continuation cursor — the phase-16
  share-page batch-1 only ever worked for the ANONYMOUS flow.)
- `page_key` (4th argument) is OPTIONAL: a null page_key still answers
  (300 items on the live 1096-item album). The /albums listing carries it,
  so it is sent when known.
- Continuation: the cursor is the snAcKc inner payload's slot inner[2]
  (live shape `[None, <items>, <cursor>, <album meta>, None, <int>]`) — a
  226-char `AKFE...` string since 2026-10-14 (previously `AH_...`, still
  accepted through a legacy fallback when the slot is absent); "" = exhausted.
  Live proof: 2026-10-14 300+300+157 = 757; earlier 300+300+300+194 = 1094.
- The album's item-count METADATA (the /albums ds:5 value) can exceed the
  photo count the walker returns (live: 1096 metadata vs 1094 photos — the
  delta matches the album's videos, which the §1b photo walker skips).
  The authoritative photo count is the enumeration's.
- TOKENLESS FULL PAGE (live incident 2026-10-13): page 1 of the 757-item
  'Cadre' album answered a FULL 300-item page with NO AH_ token; the old
  loop read that as exhaustion and the plan planned from 300 of 757. Root
  cause (found 2026-10-14): the old regex missed the new AKFE cursor, or
  picked an AH_ substring out of item data (the recurring null-payload
  errors). The retry and gates below stay as defense in depth. A
  re-run minutes later returned 300+300+157 with tokens — the missing token
  is a transient Google answer. When the caller passes `expected_count`
  (the album metadata count) a tokenless FULL page short of it is re-issued
  ONCE (same cursor, one retry per enumeration); if it persists beyond the
  video tolerance the listing is returned exhausted_cleanly=False so the
  caller's safety gate refuses it.
- scalars (SNlM0e at-token, FdrFJe f.sid, cfb2h bl) come from the logged-in
photos home page; the f.req envelope is TRIPLE-nested (double nesting
answers HTTP 400 — the phase-16 finding that started this all).
- ONE bounded retry (post-phase-17 hardening, live smoke 2026-09-28):
Google occasionally answers HTTP 200 with a well-formed wrb.fr/snAcKc
entry whose inner payload is null. The exact call is re-issued once
(same cursor — a null answer consumed no page); a null again fails
loud. The budget is one retry per enumeration, not per page.

- Wire fingerprint (quick-261014-tlx): the FINAL page-1 answer (after any null
  or tokenless retry) is reduced to a privacy-safe structural fingerprint on
  `AlbumListing.fingerprint` (inner shape, cursor path/prefix/length bucket —
  only for a FULL page 1 —, first item's slot kinds). pushframe.google.wire
  stores it per sync target and warns when it changes.

Fail-loud discipline (T-17-02): HTTP != 200, a malformed envelope, a
persisting null payload or a page-cap overrun raises — never a silent
partial listing.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from urllib.parse import quote

import httpx

from pushframe.google.parsers import (
    ProbeParseError,
    SnackcPage,
    parse_batchexecute,
    parse_snackc_payload,
)
from pushframe.google.redaction import redact_tokens

BATCHEXECUTE_URL = "https://photos.google.com/_/PhotosUi/data/batchexecute"
PHOTOS_HOME = "https://photos.google.com/"
PHOTOS_ALBUMS = "https://photos.google.com/albums"

MAX_PAGES = 60  # 60 x 300 = 18000, far beyond any real album (T-17-04 cap)
PAGE_SIZE = 300  # live-proven page size

# ONE bounded retry for the live-observed transient (2026-09-28 smoke):
# HTTP 200 carrying a well-formed snAcKc entry whose inner payload is
# null. The budget is GLOBAL to one enumerate_album run — one recovery
# per enumeration, not per page — and a null answer never consumed a
# page, so the retry re-issues the exact same call (same cursor).
_retry_budget = {"snAcKc_null_payload": 1}


def listing_shortfall_tolerance(expected_count: int) -> int:
    """How many items a listing may legitimately fall short of the album's
    metadata count: max(5, ceil(2%)).

    Videos are the only legitimate metadata-vs-listing delta (live album
    'Nous': 1096 metadata vs 1094 photos); the floor of 5 absorbs small
    albums. Integer ceil, deliberately no float math. This is the ONE
    tolerance definition, shared by the enumerator's not-clean decision and
    gsync's SAFE-01 listing_below_metadata gate.
    """
    return max(5, (2 * expected_count + 99) // 100)

# WIZ_global_data scalars on the home page.
_FSID_RE = _FSID_RE = None  # replaced below (kept name stable for tests)
_FSID_RE = __import__("re").compile(r'"FdrFJe"\s*:\s*"([^"]+)"')
_BL_RE = __import__("re").compile(r'"cfb2h"\s*:\s*"([^"]+)"')
_AT_RE = __import__("re").compile(r'"SNlM0e"\s*:\s*"([^"]+)"')


class EnumerateError(RuntimeError):
    """Album enumeration failed — fail loud, never a silent partial listing."""


@dataclass
class AlbumListing:
    """The complete, exhausted album listing (D-06: every item, disk weight
    via measure_disk_weight, photo count cross-checkable against the UI).

    exhausted_cleanly False also means: the walk stopped on a tokenless FULL
    page that is short of the caller's expected_count (transient truncation)."""

    items: list[dict] = field(default_factory=list)
    page_count: int = 0
    exhausted_cleanly: bool = False
    album_id: str | None = None
    # Privacy-safe wire fingerprint of the final page-1 answer (see wire.py);
    # keys inner / cursor / item, None values when not observable.
    fingerprint: dict | None = None

    @property
    def total_bytes(self) -> int:
        return sum(i.get("bytes", 0) for i in self.items)


@dataclass
class _SessionScalars:
    at: str
    fsid: str
    bl: str


def _session_scalars(session) -> _SessionScalars:
    """Extract at-token / f.sid / bl from the logged-in photos home page."""
    text = session.home_text()
    at_m = _AT_RE.search(text)
    fsid_m = _FSID_RE.search(text)
    bl_m = _BL_RE.search(text)
    if not (at_m and fsid_m and bl_m):
        missing = [n for n, m in (("SNlM0e", at_m), ("FdrFJe", fsid_m),
                                  ("cfb2h", bl_m)) if not m]
        raise EnumerateError(
            f"photos.google.com home lacks required batchexecute scalars: "
            f"{missing} — session dead or page shape changed; failing loud"
        )
    return _SessionScalars(at=at_m.group(1), fsid=fsid_m.group(1), bl=bl_m.group(1))


def _freq_envelope(rpcid: str, args: list) -> str:
    """The TRIPLE-nested compact f.req envelope (live-proven: double nesting
    answers HTTP 400). Shape: [[ [rpcid, <stringified args>, null, "generic"] ]] —
    the 4-element entry sits at the third nesting level, its 2nd member being
    the JSON-encoded args STRING."""
    entry = [rpcid, json.dumps(args, separators=(",", ":")), None, "generic"]
    return json.dumps([[entry]], separators=(",", ":"))


def _rpc_headers(session) -> dict[str, str]:
    headers = {
        "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
        "Origin": PHOTOS_HOME.rstrip("/"),
        "Referer": PHOTOS_HOME,
    }
    auth = session.authorization_header()
    if auth:
        headers["Authorization"] = auth
    return headers


def _snackc_page(session, scalars: _SessionScalars, album_id: str,
                 page_key: str | None,
                 continuation_token: str | None) -> SnackcPage:
    """Issue ONE snAcKc batchexecute call; return the parsed SnackcPage
    (`.items`, `.continuation_token` — None = exhausted — and wire shapes).

    continuation_token=None is the live-proven batch-1 form. A null inner
    payload on HTTP 200 (live-observed transient) is retried exactly once
    with the same cursor while the global budget lasts; a persisting null
    fails loud rather than emitting a partial listing."""
    args = [album_id, continuation_token, None, page_key]
    freq = _freq_envelope("snAcKc", args)
    url = (
        f"{BATCHEXECUTE_URL}?rpcids=snAcKc"
        f"&source-path={quote('/share/' + album_id, safe='')}"
        f"&f.sid={quote(scalars.fsid, safe='')}"
        f"&bl={quote(scalars.bl, safe='')}"
        f"&hl=fr&soc-app=165&soc-platform=1&soc-device=1"
    )
    body = f"f.req={quote(freq, safe='')}&at={quote(scalars.at, safe='')}&"
    headers = _rpc_headers(session)

    def _post() -> httpx.Response:
        return session.http.post(url, content=body.encode("utf-8"),
                                 headers=headers)

    def _snackc_entries(resp: httpx.Response) -> list:
        if resp.status_code != 200:
            raise EnumerateError(
                f"snAcKc batchexecute returned HTTP {resp.status_code} "
                f"(album {album_id[:12]}…, continuation sent: "
                f"{continuation_token is not None}) — failing loud; body head: "
                f"{redact_tokens(resp.text[:200])}"
            )
        entries = [e for e in parse_batchexecute(resp.text)
                   if e.rpcid == "snAcKc"]
        if not entries:
            raise EnumerateError(
                "snAcKc batchexecute answer carries no wrb.fr/snAcKc entry — "
                "malformed envelope; refusing to guess"
            )
        return entries

    try:
        entries = _snackc_entries(_post())
        if (entries[0].payload is None
                and _retry_budget["snAcKc_null_payload"] > 0):
            _retry_budget["snAcKc_null_payload"] -= 1
            entries = _snackc_entries(_post())
        if entries[0].payload is None:
            raise EnumerateError(
                "snAcKc carries a null inner payload on HTTP 200 (live-observed "
                "transient); the single bounded retry did not recover it — "
                "failing loud, never a partial listing"
            )
        page = parse_snackc_payload(entries[0].payload)
    except ProbeParseError as exc:
        # gsync / cli catch only EnumerateError: a parse failure must be a
        # named error with a remedy, not a traceback in the scheduled run.
        raise EnumerateError(
            f"snAcKc page could not be parsed: {exc} — failing loud, never "
            f"a partial listing"
        ) from exc
    return page


def enumerate_album(session, album_id: str, *, page_key: str | None = None,
                    max_pages: int = MAX_PAGES,
                    expected_count: int | None = None) -> AlbumListing:
    """Enumerate EVERY photo of an album (D-06) via the RPC-first protocol:

    batch-1 = snAcKc(album_id, None, None, page_key) → 300 items; follow each
    page's continuation cursor (inner[2]) until no token, a 0-item page, or a SHORT page
    (< PAGE_SIZE — the live-proven terminal signal, see the loop below).
    Fail-loud on HTTP != 200 or a malformed envelope (never a silent
    partial listing).

    `album_id` is the SHARE token (the /share/<id> path segment — what the
    /albums listing carries and what the live proof used). `page_key` is
    the share URL's ?key= value when known (optional, live-proven).

    A null-payload snAcKc answer on HTTP 200 (live-observed transient) is
    retried once per run with the same cursor — see _snackc_page.

    `expected_count` (opt-in; omitted = legacy semantics) is the album's
    metadata item count. When given, a FULL page that arrives with no
    continuation token while the listing is still short of it is re-issued
    once (same cursor, local one-per-enumeration budget); if it is still
    tokenless and the shortfall exceeds listing_shortfall_tolerance, the
    partial listing is returned with exhausted_cleanly=False (no raise —
    the caller's safety gate names it).
    """
    if not album_id or not isinstance(album_id, str):
        raise EnumerateError("album_id is required — refusing to guess")
    _retry_budget["snAcKc_null_payload"] = 1  # one bounded retry per run
    scalars = _session_scalars(session)

    listing = AlbumListing(album_id=album_id)
    token: str | None = None
    tokenless_retries_left = 1  # local: one same-cursor retry per enumeration
    while listing.page_count < max_pages:
        page = _snackc_page(session, scalars, album_id, page_key, token)
        page_items, next_token = page.items, page.continuation_token
        listing.page_count += 1
        known = {i["id"] for i in listing.items}
        fresh = [i for i in page_items if i["id"] not in known]
        listing.items.extend(fresh)

        def _tokenless_full_short() -> bool:
            # PAGE_SIZE is read at call time (tests monkeypatch it).
            return (next_token is None and bool(page_items)
                    and len(page_items) >= PAGE_SIZE
                    and expected_count is not None
                    and len(listing.items) < expected_count)

        if _tokenless_full_short() and tokenless_retries_left > 0:
            tokenless_retries_left -= 1
            page = _snackc_page(session, scalars, album_id, page_key, token)
            page_items, next_token = page.items, page.continuation_token
            known = {i["id"] for i in listing.items}
            listing.items.extend(i for i in page_items if i["id"] not in known)
        if listing.page_count == 1:
            # The FINAL page-1 answer (after any null / tokenless retry). A
            # cursor is only expected on a FULL page: a short single page
            # legitimately has none, so it is not part of the fingerprint.
            listing.fingerprint = {
                "inner": page.inner_shape,
                "item": page.item_shape,
                "cursor": (page.cursor_shape
                           if len(page_items) >= PAGE_SIZE else None),
            }
        if (_tokenless_full_short()
                and expected_count - len(listing.items)
                > listing_shortfall_tolerance(expected_count)):
            listing.exhausted_cleanly = False
            return listing
        # A SHORT page (< PAGE_SIZE) is the end of the listing — even when
        # Google still mints a continuation token on it. Live drift,
        # 2026-10-01 (the 757-item "Cadre" album: 300+300+157, page 3 short
        # WITH a token): requesting the past-the-end page answers the
        # null-payload shape, which used to read as the September transient,
        # burn the single retry and fail loud — blocking the user entirely.
        # Stopping on the short page is the paginated-API convention and
        # costs nothing: the items above are already the complete listing.
        if (next_token is None or not page_items
                or len(page_items) < PAGE_SIZE):
            listing.exhausted_cleanly = True
            return listing
        token = next_token

    raise EnumerateError(
        f"album enumeration hit the {max_pages}-page cap (T-17-04) with "
        f"{len(listing.items)} items and a live continuation token — "
        f"listing is INCOMPLETE; raise max_pages or investigate"
    )


def list_shared_albums(session) -> list:
    """The account's shared albums, from photos.google.com/albums' ds:5 block
    (live-proven: 3 albums with titles, share tokens, base64 page_keys and
    metadata item counts; the 'Cadre' row's count = 24 matched the UI).

    Note: the home page's ds:5 is the photo feed — the /albums page is the
    one that carries the album cards.
    """
    from pushframe.google.parsers import extract_initdata, parse_album_summaries

    resp = session.http.get(PHOTOS_ALBUMS)
    if resp.status_code != 200:
        raise EnumerateError(
            f"photos.google.com/albums returned HTTP {resp.status_code} "
            f"— failing loud"
        )
    try:
        ds5 = extract_initdata(resp.text, "ds:5")
    except ProbeParseError:
        # An account with no albums renders a ds:5 without card rows — a
        # legitimate empty listing, not a parse failure.
        return []
    return parse_album_summaries(ds5)


def measure_disk_weight(session, base_urls: list[str]) -> list[int]:
    """Exact per-item byte sizes via 1-octet Range GETs on `{baseUrl}=d`.

    Google answers `Content-Range: bytes 0-0/TOTAL` — the album's total disk
    weight is measurable without downloading any photo (live-proven: album C,
    24 items → 86.6 MiB exact). Items whose answer carries neither
    Content-Range nor Content-Length report 0 (defensive; live never seen).
    """
    sizes: list[int] = []
    for base in base_urls:
        r = session.http.get(f"{base}=d", headers={"Range": "bytes=0-0"})
        cr = r.headers.get("content-range", "")
        size = int(cr.rsplit("/", 1)[-1]) if "/" in cr else None
        if size is None:
            cl = r.headers.get("content-length")
            size = int(cl) if cl else 0
        sizes.append(size)
    return sizes

"""Parsers for Google Photos share pages and snAcKc batchexecute payloads.

Migrated from probes/shared_link_probe.py (phase 16, live-proven against a
24-item and a 794-item album). The media-item shape is shared by BOTH the
share page's ds:1 payload and the snAcKc RPC inner payload (ALBUM-ACCESS.md
§1b):

    [mediaItemId, [baseUrl, width, height, ...], uploadTimestampMs, ...]

Fail-loud convention: ProbeParseError names the missing structure; a partial
or silent item list is never emitted (T-17-02).
"""
from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass

_DS1_RE = re.compile(r"AF_initDataCallback\(\s*\{\s*key:\s*['\"]ds:1['\"]")

# Any AF_initDataCallback block, key captured (ds:0 album headers, ds:1 media,
# ds:N anything else the frontend carries).
_ANY_DS_RE = re.compile(r"AF_initDataCallback\(\s*\{\s*key:\s*['\"](ds:\d+)['\"]")

# snAcKc continuation cursors: AH_ followed by 40+ URL-safe chars (live-proven).
AH_TOKEN_RE = re.compile(r"AH_[A-Za-z0-9_-]{40,}")


class ProbeParseError(RuntimeError):
    """Raised when a Google page/payload lacks the expected structure (fail-loud)."""


def extract_initdata(html: str, key: str) -> list:
    """Extract and JSON-parse the `data` argument of the given ds:N AF_initDataCallback.

    Regex-locates the `key: 'ds:N'` occurrences, then performs balanced-bracket
    extraction of the `data:[...]` argument and parses it as a JS array literal.
    Raises ProbeParseError (naming the missing key) when the page has no such
    block — fail loud, never emit a partial item list silently.
    """
    matches = [m for m in _ANY_DS_RE.finditer(html) if m.group(1) == key]
    if not matches:
        raise ProbeParseError(
            f"page has no AF_initDataCallback with key '{key}' — page shape changed "
            "or the link did not resolve to an album page; refusing to guess"
        )

    # Try every occurrence (pages can carry several; non-payload lookalikes
    # — e.g. prose or comments naming the structure — fail their parse and the
    # walk continues to the next occurrence). First parseable payload wins.
    last_error: ProbeParseError | None = None
    for match in matches:
        try:
            return _extract_data_at(html, match, key=key)
        except ProbeParseError as exc:
            last_error = exc
            continue
    raise ProbeParseError(
        f"no parseable {key} data block among {len(matches)} occurrence(s): "
        f"{last_error} — refusing to guess"
    )


def extract_ds1_data(html: str) -> list:
    """Extract and JSON-parse the `data` argument of the ds:1 AF_initDataCallback
    (the shared-album media payload). Thin wrapper over `extract_initdata`."""
    return extract_initdata(html, "ds:1")


def _extract_data_at(html: str, match: re.Match, *, key: str = "ds:1") -> list:
    # From the match, find the `data:` argument's opening bracket.
    tail = html[match.end():]
    data_m = re.search(r"\bdata\s*:", tail)
    if not data_m:
        raise ProbeParseError(
            "ds:1 callback found but has no 'data:' argument — refusing to guess"
        )
    after = tail[data_m.end():]
    bracket_m = re.search(r"\[", after)
    if not bracket_m:
        raise ProbeParseError("data argument carries no opening '[' — refusing to guess")

    start = match.end() + data_m.end() + bracket_m.start()
    depth = 0
    end = None
    in_str = False
    esc = False
    quote = ""
    for i in range(start, len(html)):
        ch = html[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == quote:
                in_str = False
            continue
        if ch in ("'", '"'):
            in_str = True
            quote = ch
        elif ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    if end is None:
        raise ProbeParseError("unbalanced brackets in ds:1 data payload — truncated page?")

    literal = html[start:end]
    return _parse_array_literal(literal, key=key)


def _parse_array_literal(literal: str, *, key: str = "ds:1") -> list:
    """Parse a JS array literal: strict json.loads first, tolerant fallback second.

    The payload is normally plain JSON (double-quoted). When Google emits JS-isms
    (single quotes, bare keys, trailing commas), a conservative sanitizer normalizes
    only those cases — no bespoke parser beyond balanced extraction, per the plan.
    """
    try:
        parsed = json.loads(literal)
    except json.JSONDecodeError as first_error:
        sanitized = literal
        # Strip // line comments if any leaked in.
        sanitized = re.sub(r"^\s*//.*$", "", sanitized, flags=re.MULTILINE)
        # Quote bare object keys:  {foo: 1} -> {"foo": 1}
        sanitized = re.sub(r"([{,]\s*)([A-Za-z_][A-Za-z0-9_]*)(\s*):", r'\1"\2"\3:', sanitized)
        # Trailing commas: [1,2,] -> [1,2]
        sanitized = re.sub(r",\s*([\]}])", r"\1", sanitized)
        try:
            parsed = json.loads(sanitized)
        except json.JSONDecodeError:        raise ProbeParseError(
            f"{key} data is neither strict JSON nor tolerantly sanitizable "
            f"(first error: {first_error.msg} at {first_error.pos}) — refusing to guess"
        ) from first_error
        print("parse note: ds:1 payload needed tolerant sanitization (JS-isms present)",
              file=sys.stderr)
    if not isinstance(parsed, list):
        raise ProbeParseError(f"{key} data is not an array — page shape changed")
    return parsed


def _walk_items(node, items):
    """Depth-first walk collecting media items with the §1b structure:
    [mediaItemId, [baseUrl, width, height, ...], uploadTimestampMs, ...]."""
    if not isinstance(node, list):
        return
    if (len(node) >= 3 and isinstance(node[0], str)
            and node[0].startswith("AF1Qip")
            and isinstance(node[1], list) and node[1]
            and isinstance(node[1][0], str)
            and node[1][0].startswith("http")):
        base = node[1]
        items.append({
            "id": node[0],
            "base_url": base[0],
            "width": base[1] if len(base) > 1 else None,
            "height": base[2] if len(base) > 2 else None,
            "ts_ms": node[2] if isinstance(node[2], (int, float)) else None,
        })
        return
    for child in node:
        _walk_items(child, items)


def _dedupe(items: list[dict]) -> list[dict]:
    seen: set[str] = set()
    out: list[dict] = []
    for item in items:
        if item["id"] not in seen:
            seen.add(item["id"])
            out.append(item)
    return out


def parse_af_initdata(html: str) -> list[dict]:
    """Parse a shared-album page's HTML into a deduped list of media items."""
    data = extract_ds1_data(html)
    items: list[dict] = []
    _walk_items(data, items)
    return _dedupe(items)


@dataclass
class SnackcPage:
    """One snAcKc page: walked items + the freshest continuation token.

    `continuation_token is None` means the album is exhausted — no further
    snAcKc call should be issued (live-proven exhaustion signal).
    """

    items: list[dict]
    continuation_token: str | None


def parse_snackc_payload(payload_str: str | None) -> SnackcPage:
    """Parse one snAcKc `wrb.fr` inner payload string (the wire's JSON-in-JSON).

    Runs the same item walker as the share page (the payload's item shape is
    identical) and pulls the continuation cursor: the LAST AH_ token in the
    payload is the freshest one (live-proven). No token = album exhausted.
    """
    if not payload_str:
        raise ProbeParseError(
            "snAcKc entry carries no inner payload (null) — malformed envelope; "
            "refusing to emit a partial listing"
        )
    try:
        inner = json.loads(payload_str)
    except json.JSONDecodeError as exc:
        raise ProbeParseError(
            f"snAcKc inner payload is not JSON (error at position {exc.pos}) — "
            f"refusing to guess"
        ) from exc
    items: list[dict] = []
    _walk_items(inner, items)
    tokens = AH_TOKEN_RE.findall(payload_str)
    return SnackcPage(items=_dedupe(items), continuation_token=tokens[-1] if tokens else None)


@dataclass
class BatchexecuteEntry:
    """One `wrb.fr` entry of a batchexecute response body."""

    rpcid: str | None
    payload: str | None


def parse_batchexecute(text: str) -> list[BatchexecuteEntry]:
    """Split a batchexecute response body into its `wrb.fr` entries.

    The body is `)]}'`-prefixed, then one JSON array per line. Non-JSON lines
    are skipped (the wire carries rpcids other than the requested one, e.g.
    `di` heartbeats); only entries shaped ["wrb.fr", rpcid, payload, ...] are
    returned. A body with ZERO parseable lines raises — a truncated response
    must fail loud, not read as an empty album (T-17-02).
    """
    entries: list[BatchexecuteEntry] = []
    saw_json_line = False
    for line in text.split("\n"):
        line = line.strip()
        if not line or line.startswith(")]}'"):
            continue
        try:
            arr = json.loads(line)
        except json.JSONDecodeError:
            continue
        saw_json_line = True
        if not isinstance(arr, list):
            continue
        for entry in arr:
            if isinstance(entry, list) and entry and entry[0] == "wrb.fr":
                entries.append(BatchexecuteEntry(
                    rpcid=entry[1] if len(entry) > 1 else None,
                    payload=entry[2] if len(entry) > 2 else None,
                ))
    if not saw_json_line:
        raise ProbeParseError(
            "batchexecute response carries no JSON lines after the )]}\\' prefix "
            "— truncated or reshaped envelope; refusing to guess"
        )
    return entries


@dataclass
class AlbumSummary:
    """One shared album, as surfaced by photos.google.com/albums' ds:5 block
    (live-proven row shape; see parse_album_summaries).

    `item_count` is the album's METADATA count — it can exceed the photo
    count the media walker returns when the album carries videos (live:
    1096 vs 1094 photos). The authoritative PHOTO count is enumerate_album's.
    """

    album_id: str | None
    title: str | None
    share_url: str | None = None
    item_count: int | None = None


def _walk_album_summaries(node, out):
    """Depth-first walk of the /albums page's ds:5 payload collecting
    shared-album entries (live-proven 2026-09-28 row shape):

        [album_cover_id, [cover_url, w, h, …], …, …, {<key>: ENTRY}]

    ENTRY = [4, <title:str>, [dates…], <item_count:int>, 1,
             <page_key_b64:str>, …, <share_token AF1Qip…:str>, …]

    The album's identity token is the SHARE token (the /share/<id> path
    segment); the base64-decoded field 5 is the share URL's ?key= page_key
    (both live-proven by enumerating through the constructed URL).
    """
    if not isinstance(node, list):
        return
    if node and isinstance(node[0], str) and node[0].startswith("AF1Qip"):
        for field_ in node:
            if not isinstance(field_, dict):
                continue
            for entry in field_.values():
                if (isinstance(entry, list) and len(entry) >= 9
                        and isinstance(entry[1], str)
                        and isinstance(entry[3], int)
                        and isinstance(entry[5], str)
                        and isinstance(entry[8], str)
                        and entry[8].startswith("AF1Qip")):
                    out.append(AlbumSummary(
                        album_id=entry[8],
                        title=entry[1],
                        item_count=entry[3],
                        share_url=(f"https://photos.google.com/share/{entry[8]}"
                                   f"?key={_decode_page_key(entry[5])}"),
                    ))
        return
    for child in node:
        _walk_album_summaries(child, out)


def _decode_page_key(b64: str) -> str:
    """Decode the ds:5 entry's base64 share key into the ?key= page_key
    (live-proven: `UX20fm…` base64 == the ?key= the constructed URL used)."""
    import base64

    try:
        return base64.b64decode(b64).decode("ascii", "replace")
    except Exception:
        return ""


def parse_album_summaries(ds5_data: list) -> list[AlbumSummary]:
    """Parse the /albums page's ds:5 payload into deduped AlbumSummary rows
    (title + share token + page_key + metadata item count)."""
    out: list[AlbumSummary] = []
    _walk_album_summaries(ds5_data, out)
    seen: set[str] = set()
    deduped = []
    for s in out:
        if s.album_id and s.album_id not in seen:
            seen.add(s.album_id)
            deduped.append(s)
    return deduped

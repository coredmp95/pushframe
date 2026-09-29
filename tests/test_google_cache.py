"""Offline tests for the pruned-disk download cache (phase 18 plan 18-01 —
CSE-01/04, SAFE-04, CSE-07). A MockTransport router replays `=d` routes with
synthetic bytes, HTTP failures and Content-Length mismatches; zero network.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pushframe.aws.s3client import get_md5  # noqa: E402
from pushframe.google.cache import (  # noqa: E402
    CacheOutcome,
    download_to_cache,
    prune_cache,
    videos_skipped,
)

# Synthetic ids longer than the AF1Qip redaction threshold (tests assert real
# shapes, not threshold edges — same discipline as the phase-17 CLI tests).
def _item(n: int, base: str = "https://lh3.googleusercontent.com/pw/") -> dict:
    return {
        "id": f"AF1QipFAKEitem{n:06d}" + "0" * 30,
        "base_url": f"{base}FAKEbase{n:06d}" + "0" * 30,
        "width": 100 + n,
        "height": 200 + n,
        "ts_ms": 1_700_000_000_000 + n,
    }


def _listing(items: list[dict]):
    from types import SimpleNamespace
    return SimpleNamespace(items=items)


class _Session:
    """A minimal GoogleSession stand-in: only .http is used by the cache."""

    def __init__(self, handler):
        self.http = httpx.Client(transport=httpx.MockTransport(handler))


class _Router:
    """Routes =d downloads: FAKEbaseNNNNNN → body b'bytes-NNNNNN', plus
    canned failure routes for the SAFE-04 paths."""

    def __init__(self, failures: dict[str, str] | None = None,
                 length_lying: set[str] | None = None,
                 no_content_length: set[str] | None = None):
        self.failures = failures or {}
        self.length_lying = length_lying or set()
        self.no_content_length = no_content_length or set()
        self.requests: list[str] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.requests.append(path)
        m = re.search(r"FAKEbase(\d{6})", path)
        n = m.group(1)
        if n in self.failures:
            return httpx.Response(int(self.failures[n]), text="exploded")
        body = f"bytes-{n}".encode()
        headers = {}
        if n not in self.no_content_length:
            length = len(body) + (10 if n in self.length_lying else 0)
            headers["Content-Length"] = str(length)
        return httpx.Response(200, content=body, headers=headers)


def test_download_stages_exact_bytes_with_frame_convention_md5(tmp_path):
    router = _Router()
    listing = _listing([_item(1), _item(2)])
    outcome = download_to_cache(_Session(router.handler), listing, tmp_path / "cache")
    assert len(outcome.staged) == 2
    assert outcome.failed == []
    for staged in outcome.staged:
        n = re.search(r"FAKEitem(\d{6})", staged["google_media_id"]).group(1)
        expected_body = f"bytes-{n}".encode()
        assert Path(staged["path"]).read_bytes() == expected_body
        assert staged["md5_hash"] == get_md5(expected_body)
        assert staged["size_bytes"] == len(expected_body)
    # the =d suffix convention (exact-bytes original)
    for req in router.requests:
        assert req.endswith("=d")


def test_failed_download_never_writes_never_hashes(tmp_path):
    router = _Router(failures={"000002": "500"})
    listing = _listing([_item(1), _item(2)])
    outcome = download_to_cache(_Session(router.handler), listing, tmp_path / "cache")
    assert [gid for gid, _ in outcome.failed] == ["AF1QipFAKEitem000002" + "0" * 30]
    assert "500" in outcome.failed[0][1]
    # SAFE-04: the failed id left NO file behind
    fake_id = "AF1QipFAKEitem000002" + "0" * 30
    assert not (tmp_path / "cache" / fake_id).exists()


def test_content_length_mismatch_is_a_failed_not_staged_download(tmp_path):
    router = _Router(length_lying={"000001"})
    listing = _listing([_item(1)])
    outcome = download_to_cache(_Session(router.handler), listing, tmp_path / "cache")
    assert outcome.staged == []
    assert len(outcome.failed) == 1
    assert "truncated" in outcome.failed[0][1]


def test_missing_content_length_falls_back_to_expected_size(tmp_path):
    """A stream transport that does NOT populate Content-Length: the caller's
    expected_size (the phase-16 Range measurement) is the length check."""
    # httpx auto-derives Content-Length from a bytes body, so the "header
    # absent" case needs a streamed response (no declared length).
    def streaming_handler(request: httpx.Request) -> httpx.Response:
        m = re.search(r"FAKEbase(\d{6})", request.url.path)
        body = f"bytes-{m.group(1)}".encode()
        return httpx.Response(200, content=iter([body]))

    session = _Session(streaming_handler)
    listing = _listing([_item(1)])
    gid = "AF1QipFAKEitem000001" + "0" * 30
    good = download_to_cache(session, listing, tmp_path / "c1",
                             expected_sizes={gid: 12})
    assert len(good.staged) == 1  # 12 == len(b'bytes-000001')
    bad = download_to_cache(session, listing, tmp_path / "c2",
                            expected_sizes={gid: 999})
    assert bad.staged == [] and "truncated" in bad.failed[0][1]


def test_manifest_members_are_skipped_not_downloaded(tmp_path):
    class _M:
        def entry_for(self, gid):
            return {"md5_hash": "x"} if gid.endswith("000001" + "0" * 30) else None

    router = _Router()
    listing = _listing([_item(1), _item(2)])
    outcome = download_to_cache(_Session(router.handler), listing, tmp_path / "cache",
                                manifest=_M())
    assert outcome.skipped_manifest == 1
    assert {s["google_media_id"] for s in outcome.staged} == {
        "AF1QipFAKEitem000002" + "0" * 30
    }
    # exactly one =d request flew (the non-manifest item)
    assert len(router.requests) == 1


def test_concurrent_workers_stage_all_items_order_independently(tmp_path):
    router = _Router()
    listing = _listing([_item(n) for n in range(1, 9)])
    outcome = download_to_cache(_Session(router.handler), listing, tmp_path / "cache",
                                workers=4)
    assert len(outcome.staged) == 8
    assert len({s["md5_hash"] for s in outcome.staged}) == 8
    assert outcome.failed == []


def test_workers_minimum_is_one(tmp_path):
    router = _Router()
    listing = _listing([_item(1)])
    outcome = download_to_cache(_Session(router.handler), listing, tmp_path / "cache",
                                workers=0)
    assert len(outcome.staged) == 1


def test_prune_deletes_exactly_manifest_backed_files(tmp_path):
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    id_a = "AF1QipFAKEitem000001" + "0" * 30
    id_b = "AF1QipFAKEitem000002" + "0" * 30
    id_c = "AF1QipFAKEitem000003" + "0" * 30
    for gid in (id_a, id_b, id_c):
        (cache_dir / gid).write_bytes(b"x")
    pruned = prune_cache(cache_dir, {id_a, id_b})
    assert pruned == 2
    assert not (cache_dir / id_a).exists()
    assert not (cache_dir / id_b).exists()
    assert (cache_dir / id_c).exists()  # un-manifested: kept for retry


def test_prune_is_idempotent_and_counts_missing_files(tmp_path):
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    id_a = "AF1QipFAKEitem000001" + "0" * 30
    (cache_dir / id_a).write_bytes(b"x")
    assert prune_cache(cache_dir, {id_a}) == 1
    # second run: the file is gone, the count is identical, no raise
    assert prune_cache(cache_dir, {id_a}) == 1
    assert prune_cache(tmp_path / "does-not-exist", {id_a}) == 1


def test_videos_skipped_delta(tmp_path):
    listing = _listing([_item(1), _item(2), _item(3)])
    assert videos_skipped(listing, 5) == 2
    assert videos_skipped(listing, 3) == 0
    assert videos_skipped(listing, None) is None


def test_cache_module_has_no_aura_side_imports():
    """CSE-01 structural pin: the cache module imports nothing from the
    sync/apply side — concurrency cannot reach the frame-write seam. Checked
    on the AST import graph, not the raw text (docstrings may discuss it)."""
    import ast
    src_path = (Path(__file__).resolve().parent.parent / "pushframe" / "google"
                / "cache.py")
    tree = ast.parse(src_path.read_text())
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    for banned in ("pushframe.sync", "pushframe.cli", "pushframe.aura"):
        assert not any(m == banned or m.startswith(banned + ".") for m in imported), banned

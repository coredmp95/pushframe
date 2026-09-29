"""Offline tests for the gsync plan-computing half (phase 18 plan 18-02).

Covers the load-bearing correctness point (roadmap criterion 3): second run
against an unchanged album reports ZERO to upload even though the cache was
pruned — demand is rebuilt from listing+manifest, never from a cache walk.
Zero network, zero credentials; compute_plan is consumed unchanged.
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pushframe.aws.s3client import get_md5  # noqa: E402
from pushframe.gsync import (  # noqa: E402
    SENTINEL_SUFFIX,
    SafeSyncError,
    build_demand,
    format_plan_report,
    run_google_sync_plan,
)
from pushframe.google.cache import CacheOutcome  # noqa: E402
from pushframe.google.manifest import GoogleManifest  # noqa: E402
from pushframe.models.asset import Asset  # noqa: E402
from pushframe.sync import compute_plan  # noqa: E402

ALBUM = "AF1QipFAKEalbum" + "0" * 30 + "1"


def _gid(n: int) -> str:
    return f"AF1QipFAKEitem{n:06d}" + "0" * 30


def _item(n: int) -> dict:
    return {"id": _gid(n), "base_url": f"https://x/pw/FAKE{n}",
            "width": 100, "height": 200, "ts_ms": 1_700_000_000_000}


def _listing(items: list[dict], exhausted: bool = True):
    return SimpleNamespace(items=items, exhausted_cleanly=exhausted)


def _asset(id_: str, md5_hash: str, selected: bool = True):
    return Asset.model_construct(id=id_, md5_hash=md5_hash,
                                 taken_at="2024-03-11T12:00:00.000Z",
                                 selected=selected)


def _staged(n: int, cache_dir: Path) -> CacheOutcome:
    body = f"photo-{n}".encode()
    path = cache_dir / _gid(n)
    path.write_bytes(body)
    return CacheOutcome(staged=[{
        "google_media_id": _gid(n), "path": str(path),
        "md5_hash": get_md5(body), "size_bytes": len(body),
    }])


def _manifest_for(*ns: int) -> GoogleManifest:
    m = GoogleManifest()
    for n in ns:
        m.add(_gid(n), md5_hash=get_md5(f"photo-{n}".encode()),
              size_bytes=len(f"photo-{n}".encode()), album_share_token=ALBUM)
    return m


def test_second_run_zero_upload_with_pruned_cache(tmp_path):
    """Criterion 3's tracer truth: full manifest + fully pruned cache →
    to_upload == [] and unchanged == N. The demand map's sentinel paths never
    reach to_upload because the frame still holds every manifest md5."""
    listing = _listing([_item(1), _item(2), _item(3)])
    manifest = _manifest_for(1, 2, 3)
    frame_assets = [_asset(f"frame-{n}", get_md5(f"photo-{n}".encode()))
                    for n in (1, 2, 3)]
    plan, failures, videos = run_google_sync_plan(
        listing, manifest, CacheOutcome(), tmp_path / "cache", frame_assets)
    assert plan.to_upload == []
    assert plan.unchanged == 3
    assert plan.to_delete == [] and plan.to_reshow == []
    assert failures == []


def test_fresh_run_uploads_every_staged_item(tmp_path):
    listing = _listing([_item(1), _item(2)])
    plan, failures, videos = run_google_sync_plan(
        listing, GoogleManifest(), _staged(1, tmp_path) , tmp_path,
        [_asset("frame-a", get_md5(b"other"))])
    # merge a second staged item into the outcome
    staged = _staged(1, tmp_path)
    staged.staged.extend(_staged(2, tmp_path).staged)
    plan, failures, videos = run_google_sync_plan(
        listing, GoogleManifest(), staged, tmp_path,
        [_asset("frame-a", get_md5(b"other"))])
    assert len(plan.to_upload) == 2
    assert plan.unchanged == 0
    assert failures == []


def test_removed_item_hides_not_deletes(tmp_path):
    """Run 1 synced 3 items; run 2's listing dropped item 3 → its frame asset
    is a hide (removal) candidate; the other two are unchanged."""
    listing = _listing([_item(1), _item(2)])
    manifest = _manifest_for(1, 2, 3)
    frame_assets = [_asset(f"frame-{n}", get_md5(f"photo-{n}".encode()))
                    for n in (1, 2, 3)]
    plan, _, _ = run_google_sync_plan(
        listing, manifest, CacheOutcome(), tmp_path / "cache", frame_assets)
    assert [a.id for a in plan.to_delete] == ["frame-3"]
    assert plan.unchanged == 2


def test_readded_hidden_item_reshows_without_reupload(tmp_path):
    """Criterion 4's offline half: item 3 was hidden (removed from album,
    re-added) → it comes back as to_reshow, never as a re-upload."""
    listing = _listing([_item(1), _item(3)])
    manifest = _manifest_for(1, 3)
    frame_assets = [
        _asset("frame-1", get_md5(b"photo-1")),
        _asset("frame-3", get_md5(b"photo-3"), selected=False),  # hidden
    ]
    plan, _, _ = run_google_sync_plan(
        listing, manifest, CacheOutcome(), tmp_path / "cache", frame_assets)
    assert [a.id for a in plan.to_reshow] == ["frame-3"]
    assert plan.to_upload == []


def test_failed_download_excluded_from_demand_and_reported(tmp_path):
    listing = _listing([_item(1), _item(2)])
    staged = _staged(1, tmp_path)
    staged.failed.append((_gid(2), "=d download returned HTTP 500"))
    plan, failures, _ = run_google_sync_plan(
        listing, GoogleManifest(), staged, tmp_path, [_asset("f", get_md5(b"x"))])
    assert [gid for gid, _ in failures] == [_gid(2)]
    assert len(plan.to_upload) == 1  # only the staged item is plannable


def test_manifest_member_with_pruned_cache_asserts_md5_via_sentinel(tmp_path):
    """The sentinel path shape: manifest-backed + pruned → demand keyed by the
    manifest's md5 with a <id>.absent path (CSE-03: no walk, no re-download)."""
    listing = _listing([_item(1)])
    manifest = _manifest_for(1)
    demand, failures, _ = build_demand(listing, manifest, CacheOutcome(),
                                       tmp_path / "cache")
    assert list(demand) == [get_md5(b"photo-1")]
    assert demand[get_md5(b"photo-1")][0].name == _gid(1) + SENTINEL_SUFFIX
    assert failures == []


def test_manifest_drift_fails_loud_when_frame_lacks_the_md5(tmp_path):
    """A manifest entry whose md5 the frame does NOT hold means the pruned
    cache claim can't be honored — fail loud, never upload a sentinel path."""
    listing = _listing([_item(1)])
    manifest = _manifest_for(1)
    frame_assets = [_asset("frame-x", get_md5(b"something-else"))]
    with pytest.raises(SafeSyncError, match="manifest claims an upload"):
        run_google_sync_plan(listing, manifest, CacheOutcome(), tmp_path,
                             frame_assets)


def test_safe01_empty_listing_aborts():
    with pytest.raises(SafeSyncError, match="album listing is EMPTY"):
        run_google_sync_plan(_listing([]), GoogleManifest(), CacheOutcome(),
                             Path("/tmp"), [_asset("f", "m")])

def test_safe01_truncated_listing_aborts():
    with pytest.raises(SafeSyncError, match="NOT exhausted cleanly"):
        run_google_sync_plan(_listing([_item(1)], exhausted=False),
                             GoogleManifest(), CacheOutcome(), Path("/tmp"),
                             [_asset("f", "m")])


def test_safe01_empty_frame_listing_aborts():
    with pytest.raises(SafeSyncError, match="frame asset listing is EMPTY"):
        run_google_sync_plan(_listing([_item(1)]), GoogleManifest(),
                             CacheOutcome(), Path("/tmp"), [])


def test_video_delta_reported_through_the_plan(tmp_path):
    listing = _listing([_item(1), _item(2), _item(3)])
    manifest = _manifest_for(1, 2, 3)
    frame_assets = [_asset(f"frame-{n}", get_md5(f"photo-{n}".encode()))
                    for n in (1, 2, 3)]
    _, _, videos = run_google_sync_plan(
        listing, manifest, CacheOutcome(), tmp_path / "cache", frame_assets,
        metadata_item_count=5)
    assert videos == 2


def test_format_plan_report_counts_videos_and_redacts(tmp_path):
    listing = _listing([_item(1)])
    manifest = _manifest_for(1)
    frame_assets = [_asset("frame-x", get_md5(b"photo-1"), selected=False)]
    plan, failures, videos = run_google_sync_plan(
        listing, manifest, CacheOutcome(), tmp_path, frame_assets,
        metadata_item_count=4)
    report = format_plan_report(plan, failures, videos)
    assert "1 to re-show" in report
    assert "0 to hide" in report
    assert "Videos skipped: 3" in report
    # no unredacted full id anywhere, and no item lines when nothing uploads
    assert _gid(1) not in report
    assert "Upload candidates" not in report


def test_compute_plan_imported_not_forked():
    """CSE-03/05 structural pin: gsync reuses sync.compute_plan — no diff
    logic is copied into the module."""
    import ast
    src_path = (Path(__file__).resolve().parent.parent / "pushframe" / "gsync.py")
    tree = ast.parse(src_path.read_text())
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
             and isinstance(n.func, ast.Name)]
    assert any(c.func.id == "compute_plan" for c in calls)

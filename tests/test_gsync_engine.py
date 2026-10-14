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
    default_cache_dir,
    format_noop_message,
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


def test_safe01_listing_below_metadata_aborts(tmp_path):
    """2026-10-13 incident shape (c): 300 listed of 757 metadata."""
    listing = _listing([_item(n) for n in range(1, 301)])
    with pytest.raises(SafeSyncError) as exc:
        run_google_sync_plan(listing, GoogleManifest(), CacheOutcome(),
                             tmp_path, [_asset("f", "m")],
                             metadata_item_count=757)
    assert "album metadata says 757 items" in str(exc.value)
    assert "listing returned 300" in str(exc.value)


def test_listing_below_metadata_tolerance_boundary(tmp_path):
    def run(listed, metadata):
        items = [_item(n) for n in range(1, listed + 1)]
        return run_google_sync_plan(
            _listing(items), _manifest_for(*range(1, listed + 1)),
            CacheOutcome(), tmp_path / "cache",
            [_asset(f"frame-{n}", get_md5(f"photo-{n}".encode()))
             for n in range(1, listed + 1)],
            metadata_item_count=metadata)

    run(741, 757)       # delta 16 == tolerance -> plans
    with pytest.raises(SafeSyncError, match="album metadata says 757"):
        run(740, 757)
    run(5, 10)          # floor of 5
    with pytest.raises(SafeSyncError, match="album metadata says 10"):
        run(4, 10)


def test_listing_below_metadata_ignored_when_unknown_or_listing_larger(tmp_path):
    def assets(n):
        return [_asset(f"frame-{i}", get_md5(f"photo-{i}".encode()))
                for i in range(1, n + 1)]

    run_google_sync_plan(_listing([_item(1)]), _manifest_for(1), CacheOutcome(),
                         tmp_path, assets(1), metadata_item_count=None)
    run_google_sync_plan(_listing([_item(n) for n in range(1, 6)]),
                         _manifest_for(*range(1, 6)), CacheOutcome(),
                         tmp_path, assets(5), metadata_item_count=3)


def test_safe01_gate_order_empty_then_truncated_then_below_metadata(tmp_path):
    with pytest.raises(SafeSyncError, match="album listing is EMPTY"):
        run_google_sync_plan(_listing([]), GoogleManifest(), CacheOutcome(),
                             tmp_path, [_asset("f", "m")],
                             metadata_item_count=757)
    with pytest.raises(SafeSyncError, match="NOT exhausted cleanly"):
        run_google_sync_plan(_listing([_item(1)], exhausted=False),
                             GoogleManifest(), CacheOutcome(), tmp_path,
                             [_asset("f", "m")], metadata_item_count=757)


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


# --- 2026-10-12 review quick fixes: B1 (cache dir expanduser) + B2 (unmanaged) ---


def test_default_cache_dir_is_absolute_under_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    result = default_cache_dir("TOKEN")
    assert result == tmp_path / ".config" / "pushframe" / "google-cache" / "TOKEN"
    assert result.is_absolute()
    assert "~" not in str(result)


def test_no_unexpanded_tilde_path_literals_in_package():
    """B1 guard: a `Path("~...")` literal is only legal when expanded on the
    same line or held in a module-level UPPER_CASE constant whose consumers
    expand it."""
    import re
    pkg = Path(__file__).resolve().parent.parent / "pushframe"
    literal = re.compile(r"""Path\(\s*['"]~""")
    constant = re.compile(r"^[A-Z_]+\s*=\s*Path\(")
    offenders = []
    for py in sorted(pkg.rglob("*.py")):
        for no, line in enumerate(py.read_text().splitlines(), 1):
            text = line.strip()
            if text.startswith("#") or not literal.search(text):
                continue
            if "expanduser" in text or constant.match(text):
                continue
            offenders.append(f"{py.relative_to(pkg.parent)}:{no}: {text}")
    assert offenders == []


def test_compute_plan_lists_visible_hashless_assets_as_unmanaged():
    visible = _asset("frame-video-v", None)
    hidden = _asset("frame-video-h", None, selected=False)
    kept = _asset("frame-kept", get_md5(b"photo-1"))
    plan = compute_plan({get_md5(b"photo-1"): [Path("a.jpg")]},
                        [visible, hidden, kept])
    assert len(plan.unmanaged_visible) == 1
    assert plan.unmanaged_visible[0] is visible
    assert plan.frame_no_hash == 2
    assert plan.unchanged == 1
    assert plan.to_delete == []
    assert plan.to_reshow == []
    assert plan.already_hidden == 0


def _unmanaged_plan(n: int):
    assets = [_asset(f"frame-nohash-{i}", None) for i in range(n)]
    return compute_plan({}, assets)


def test_format_plan_report_names_unmanaged_visible():
    plan = _unmanaged_plan(1)
    report = format_plan_report(plan, [], None)
    assert ("1 visible photo(s) on the frame have no content hash and are "
            "NOT managed by the mirror") in report
    assert "frame-nohash-0" not in report
    detailed = format_plan_report(plan, [], None, detail_ids=True)
    line = [l for l in detailed.splitlines() if "frame-nohash-0" in l]
    assert len(line) == 1 and "(taken " in line[0]
    assert "NOT managed" not in format_plan_report(_unmanaged_plan(0), [], None)


def test_noop_message_does_not_claim_a_perfect_mirror_when_unmanaged():
    clean = SimpleNamespace(unchanged=3, already_hidden=1, unmanaged_visible=[])
    assert format_noop_message(clean) == (
        "Nothing to do \u2014 the frame already mirrors the album "
        "(3 unchanged, 1 already hidden).")
    dirty = SimpleNamespace(unchanged=3, already_hidden=1,
                            unmanaged_visible=[object(), object()])
    msg = format_noop_message(dirty)
    assert msg.startswith("Nothing to do")
    assert "already mirrors" not in msg
    assert "2" in msg and "NOT managed by the mirror" in msg

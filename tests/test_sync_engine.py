"""Offline unit tests for pushframe.sync (the dry-run diff engine's pure
core). Zero network access, zero credentials -- scan_directory only reads
local bytes under tmp_path, and compute_plan is a pure function.
"""
from pathlib import Path

from pushframe.aws.s3client import get_md5
from pushframe.models.asset import Asset
from pushframe.sync import compute_plan, scan_directory


def _asset(id_, md5_hash, taken_at="2024-03-11T12:00:00.000Z", selected=True):
    """`selected` is THIS FRAME's visibility, as joined from `asset_settings`
    by FrameApi.get_assets -- False means the photo is hidden on the frame."""
    return Asset.model_construct(id=id_, md5_hash=md5_hash, taken_at=taken_at,
                                 selected=selected)


def _write(path, data: bytes):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def test_scan_directory_recurses_into_nested_subdirectories(tmp_path):
    _write(tmp_path / "top.jpg", b"top-bytes")
    _write(tmp_path / "2024" / "vacation" / "nested.jpg", b"nested-bytes")

    result = scan_directory(tmp_path)

    all_paths = [p for paths in result.local_hashes.values() for p in paths]
    assert tmp_path / "top.jpg" in all_paths
    assert tmp_path / "2024" / "vacation" / "nested.jpg" in all_paths


def test_scan_directory_hashes_each_eligible_extension_case_insensitively(tmp_path):
    _write(tmp_path / "a.jpg", b"jpg-bytes")
    _write(tmp_path / "b.JPEG", b"jpeg-bytes")
    _write(tmp_path / "c.png", b"png-bytes")
    _write(tmp_path / "d.HEIC", b"heic-bytes")

    result = scan_directory(tmp_path)

    all_paths = {p.name: h for h, paths in result.local_hashes.items() for p in paths}
    assert all_paths["a.jpg"] == get_md5(b"jpg-bytes")
    assert all_paths["b.JPEG"] == get_md5(b"jpeg-bytes")
    assert all_paths["c.png"] == get_md5(b"png-bytes")
    assert all_paths["d.HEIC"] == get_md5(b"heic-bytes")
    assert result.skipped_non_image == 0


def test_scan_directory_skips_non_image_files_without_erroring(tmp_path):
    _write(tmp_path / "clip.mp4", b"video-bytes")
    _write(tmp_path / ".DS_Store", b"ds-store-bytes")
    _write(tmp_path / "notes.txt", b"text-bytes")
    _write(tmp_path / "photo.jpg", b"photo-bytes")

    result = scan_directory(tmp_path)

    assert result.skipped_non_image == 3
    all_paths = [p for paths in result.local_hashes.values() for p in paths]
    assert tmp_path / "photo.jpg" in all_paths
    assert len(all_paths) == 1


def test_scan_directory_collapses_byte_identical_files_to_one_hash_key(tmp_path):
    _write(tmp_path / "folder_a" / "photo.jpg", b"identical-bytes")
    _write(tmp_path / "folder_b" / "copy.jpg", b"identical-bytes")

    result = scan_directory(tmp_path)

    assert len(result.local_hashes) == 1
    expected_hash = get_md5(b"identical-bytes")
    paths = result.local_hashes[expected_hash]
    assert len(paths) == 2
    assert tmp_path / "folder_a" / "photo.jpg" in paths
    assert tmp_path / "folder_b" / "copy.jpg" in paths


def test_scan_directory_empty_directory_yields_empty_result(tmp_path):
    result = scan_directory(tmp_path)

    assert result.local_hashes == {}
    assert result.skipped_non_image == 0


def test_compute_plan_local_only_hash_becomes_single_upload():
    local_hashes = {"hash-a": [Path("/photos/a.jpg"), Path("/photos/a-copy.jpg")]}

    plan = compute_plan(local_hashes, frame_assets=[])

    assert plan.to_upload == [Path("/photos/a.jpg")]
    assert plan.to_delete == []
    assert plan.unchanged == 0
    assert plan.frame_no_hash == 0


def test_compute_plan_matched_hash_is_unchanged_not_upload_or_delete():
    local_hashes = {"hash-a": [Path("/photos/a.jpg")]}
    matched = _asset("asset-1", "hash-a")

    plan = compute_plan(local_hashes, frame_assets=[matched])

    assert plan.to_upload == []
    assert plan.to_delete == []
    assert plan.unchanged == 1


def test_compute_plan_multiset_surplus_frame_assets_become_delete_candidates():
    local_hashes = {"hash-x": [Path("/photos/x.jpg")]}
    kept = _asset("asset-kept", "hash-x")
    surplus_1 = _asset("asset-surplus-1", "hash-x")
    surplus_2 = _asset("asset-surplus-2", "hash-x")

    plan = compute_plan(local_hashes, frame_assets=[kept, surplus_1, surplus_2])

    assert plan.unchanged == 1
    delete_ids = {a.id for a in plan.to_delete}
    assert delete_ids == {"asset-surplus-1", "asset-surplus-2"}
    assert plan.to_upload == []


def test_compute_plan_frame_hash_absent_locally_becomes_delete():
    orphan = _asset("asset-orphan", "hash-not-local")

    plan = compute_plan(local_hashes={}, frame_assets=[orphan])

    assert plan.to_delete == [orphan]
    assert plan.unchanged == 0


def test_compute_plan_hashless_frame_asset_excluded_from_unchanged_and_delete():
    video_asset = _asset("asset-video", None)

    plan = compute_plan(local_hashes={}, frame_assets=[video_asset])

    assert plan.to_delete == []
    assert plan.unchanged == 0
    assert plan.frame_no_hash == 1


def test_compute_plan_carries_skipped_non_image_onto_returned_plan():
    plan = compute_plan(local_hashes={}, frame_assets=[], skipped_non_image=7)

    assert plan.skipped_non_image == 7


# --- 4-way visibility classification (HIDE-02, HIDE-08; D-05/D-06) ----------
#
# compute_plan classifies every frame asset on (present-local x selected):
#   present + hidden  -> to_reshow        present + visible -> unchanged
#   gone    + visible -> to_delete        gone    + hidden  -> already_hidden

def test_compute_plan_present_local_hidden_asset_is_a_reshow_candidate(tmp_path):
    _write(tmp_path / "kept.jpg", b"kept-bytes")
    scan = scan_directory(tmp_path)
    (local_hash,) = scan.local_hashes.keys()
    hidden = _asset("asset-hidden", local_hash, selected=False)

    plan = compute_plan(scan.local_hashes, [hidden])

    assert plan.to_reshow == [hidden], "a hidden photo still wanted locally must be re-shown"
    assert plan.to_delete == []
    assert plan.unchanged == 0


def test_compute_plan_present_local_visible_asset_is_unchanged(tmp_path):
    _write(tmp_path / "kept.jpg", b"kept-bytes")
    scan = scan_directory(tmp_path)
    (local_hash,) = scan.local_hashes.keys()

    plan = compute_plan(scan.local_hashes, [_asset("asset-visible", local_hash, selected=True)])

    assert plan.unchanged == 1
    assert plan.to_reshow == []
    assert plan.to_delete == []


def test_compute_plan_gone_local_visible_asset_is_a_removal_candidate(tmp_path):
    scan = scan_directory(tmp_path)  # empty dir -- nothing wanted locally
    visible = _asset("asset-visible", "hash-not-local", selected=True)

    plan = compute_plan(scan.local_hashes, [visible])

    assert plan.to_delete == [visible]
    assert plan.already_hidden == 0


def test_compute_plan_gone_local_hidden_asset_is_a_noop(tmp_path):
    """Already hidden and no longer wanted locally: nothing left to do. It must
    NOT be re-removed on every run (D-06)."""
    scan = scan_directory(tmp_path)
    hidden = _asset("asset-hidden", "hash-not-local", selected=False)

    plan = compute_plan(scan.local_hashes, [hidden])

    assert plan.already_hidden == 1
    assert plan.to_delete == [], "an already-hidden asset must never be a removal candidate"
    assert plan.to_reshow == []


def test_compute_plan_hidden_match_consumes_demand_and_is_never_reuploaded(tmp_path):
    """D-06 dedup: a hidden frame asset still counts as present, so its local
    counterpart must not be uploaded a second time."""
    _write(tmp_path / "kept.jpg", b"kept-bytes")
    scan = scan_directory(tmp_path)
    (local_hash,) = scan.local_hashes.keys()

    plan = compute_plan(scan.local_hashes, [_asset("asset-hidden", local_hash, selected=False)])

    assert plan.to_upload == [], "a hidden photo already on the frame must not be re-uploaded"
    assert plan.to_reshow, "it should be re-shown instead"


def test_compute_plan_classifies_a_mixed_frame_four_ways(tmp_path):
    _write(tmp_path / "a.jpg", b"a-bytes")
    _write(tmp_path / "b.jpg", b"b-bytes")
    scan = scan_directory(tmp_path)
    by_name = {p.name: h for h, paths in scan.local_hashes.items() for p in paths}

    reshow = _asset("a-hidden", by_name["a.jpg"], selected=False)
    unchanged = _asset("b-visible", by_name["b.jpg"], selected=True)
    removal = _asset("c-visible-gone", "hash-gone-visible", selected=True)
    noop = _asset("d-hidden-gone", "hash-gone-hidden", selected=False)

    plan = compute_plan(scan.local_hashes, [reshow, unchanged, removal, noop])

    assert plan.to_reshow == [reshow]
    assert plan.unchanged == 1
    assert plan.to_delete == [removal]
    assert plan.already_hidden == 1
    assert plan.to_upload == []

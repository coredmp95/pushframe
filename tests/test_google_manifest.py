"""Offline tests for the persistent google_media_id → md5_hash manifest
(phase 18 plan 18-01 — CSE-02). Zero network, tmp_path-backed.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pushframe.google.manifest import (  # noqa: E402
    GoogleManifest,
    ManifestError,
)


def _entry(**overrides):
    base = {
        "md5_hash": "DSWMyGKS2k3nxpzqIdh37g==",
        "size_bytes": 3_412_350,
        "album_share_token": "AF1QipFAKEalbum" + "0" * 30 + "1",
        "first_synced": "2026-09-28T18:00:00+00:00",
    }
    base.update(overrides)
    return base


def test_load_absent_file_is_empty_manifest(tmp_path):
    m = GoogleManifest.load(tmp_path / "missing.json")
    assert m.entries == {}


def test_save_then_load_round_trips_exactly(tmp_path):
    path = tmp_path / "manifest.json"
    m = GoogleManifest()
    m.add("FAKEID001", md5_hash="aQ==", size_bytes=10,
          album_share_token="AF1QipFAKEalbum" + "0" * 30 + "1",
          first_synced="2026-09-28T18:00:00+00:00")
    m.add("FAKEID002", md5_hash="bQ==", size_bytes=20,
          album_share_token="AF1QipFAKEalbum" + "0" * 30 + "1")
    m.save(path)
    reloaded = GoogleManifest.load(path)
    assert reloaded.entries == m.entries
    assert reloaded.entry_for("FAKEID002")["md5_hash"] == "bQ=="
    assert reloaded.entry_for("FAKEID002")["size_bytes"] == 20
    assert reloaded.entry_for("nope") is None


def test_save_is_atomic_and_leaves_no_tmp_file(tmp_path):
    path = tmp_path / "manifest.json"
    m = GoogleManifest()
    m.add("FAKEID001", **{"md5_hash": "aQ==", "size_bytes": 1,
                          "album_share_token": "AF1QipFAKEalbum" + "0" * 30 + "1"})
    m.save(path)
    assert path.exists()
    assert not (tmp_path / "manifest.json.tmp").exists()


def test_save_enforces_0600(tmp_path):
    path = tmp_path / "manifest.json"
    m = GoogleManifest()
    m.add("FAKEID001", md5_hash="aQ==", size_bytes=1,
          album_share_token="AF1QipFAKEalbum" + "0" * 30 + "1")
    m.save(path)
    assert path.stat().st_mode & 0o777 == 0o600


def test_save_over_preexisting_loose_file_repairs_mode(tmp_path):
    path = tmp_path / "manifest.json"
    path.write_text("{}")
    path.chmod(0o644)
    m = GoogleManifest()
    m.add("FAKEID001", md5_hash="aQ==", size_bytes=1,
          album_share_token="AF1QipFAKEalbum" + "0" * 30 + "1")
    m.save(path)
    assert path.stat().st_mode & 0o777 == 0o600


def test_load_rejects_non_object_root(tmp_path):
    path = tmp_path / "manifest.json"
    path.write_text("[]")
    with pytest.raises(ManifestError, match="not a JSON object"):
        GoogleManifest.load(path)


def test_load_rejects_extra_keys_no_secrets_escape_hatch(tmp_path):
    """The shape is CLOSED: a future edit cannot smuggle per-id secrets (or
    any unvetted state) into the manifest without tripping this guard."""
    path = tmp_path / "manifest.json"
    entry = _entry(suspicious_cookie="SID=leak")
    path.write_text(json.dumps({"FAKEID001": entry}))
    with pytest.raises(ManifestError, match="unexpected keys.*suspicious_cookie"):
        GoogleManifest.load(path)


def test_load_rejects_missing_keys(tmp_path):
    path = tmp_path / "manifest.json"
    entry = _entry()
    del entry["size_bytes"]
    path.write_text(json.dumps({"FAKEID001": entry}))
    with pytest.raises(ManifestError, match="missing.*size_bytes"):
        GoogleManifest.load(path)


def test_load_rejects_non_string_md5(tmp_path):
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps({"FAKEID001": _entry(md5_hash=42)}))
    with pytest.raises(ManifestError, match="md5_hash must be a non-empty string"):
        GoogleManifest.load(path)


def test_load_rejects_bool_size_bytes(tmp_path):
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps({"FAKEID001": _entry(size_bytes=True)}))
    with pytest.raises(ManifestError, match="size_bytes must be an int"):
        GoogleManifest.load(path)

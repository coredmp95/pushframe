"""S5 of the 2026-10-12 review: private state must be private by construction,
not by umask luck (prod showed 775/664 under umask 0o002).

Every test runs under `prod_umask` (umask 0o002) so EXPLICIT modes are proven
to beat a permissive umask. Modes are compared with stat.S_IMODE.
"""
from __future__ import annotations

import os
import stat
import sys
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pushframe.utils.io import ensure_private_dir, write_private_bytes  # noqa: E402


@pytest.fixture(autouse=True)
def prod_umask():
    old = os.umask(0o002)
    try:
        yield
    finally:
        os.umask(old)


def _mode(path) -> int:
    return stat.S_IMODE(os.stat(path).st_mode)


# --- helpers ---------------------------------------------------------------

def test_ensure_private_dir_creates_every_new_component_0700(tmp_path):
    before = _mode(tmp_path)
    leaf = ensure_private_dir(tmp_path / "a" / "b" / "c")
    assert isinstance(leaf, Path) and leaf.is_dir()
    for part in ("a", "a/b", "a/b/c"):
        assert _mode(tmp_path / part) == 0o700
    assert _mode(tmp_path) == before


def test_ensure_private_dir_expands_tilde(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    leaf = ensure_private_dir("~/x/y")
    assert leaf == tmp_path / "x" / "y"
    assert _mode(leaf) == 0o700


def test_ensure_private_dir_tighten_only_when_asked(tmp_path):
    leaf = tmp_path / "loose"
    leaf.mkdir()
    os.chmod(leaf, 0o775)
    ensure_private_dir(leaf)
    assert _mode(leaf) == 0o775
    ensure_private_dir(leaf, tighten=True)
    assert _mode(leaf) == 0o700


def test_write_private_bytes_creates_and_rewrites_0600(tmp_path):
    f = tmp_path / "new.bin"
    write_private_bytes(f, b"hello")
    assert f.read_bytes() == b"hello" and _mode(f) == 0o600
    old = tmp_path / "old.bin"
    old.write_bytes(b"old-old-old")
    os.chmod(old, 0o664)
    write_private_bytes(old, b"new")
    assert old.read_bytes() == b"new" and _mode(old) == 0o600


# --- call sites ------------------------------------------------------------

def test_download_to_cache_stages_private(tmp_path):
    from pushframe.google.cache import download_to_cache
    body = b"photo-bytes"

    def handler(request):
        return httpx.Response(200, content=body,
                              headers={"Content-Length": str(len(body))})

    session = SimpleNamespace(
        http=httpx.Client(transport=httpx.MockTransport(handler)))
    gid = "AF1QipFAKEitem000001" + "0" * 30
    listing = SimpleNamespace(items=[{"id": gid, "base_url": "https://x/pw/FAKE1"}])
    cache = tmp_path / "gc" / ("AF1Qip" + "T" * 30)
    outcome = download_to_cache(session, listing, cache)
    assert len(outcome.staged) == 1
    assert _mode(tmp_path / "gc") == 0o700
    assert _mode(cache) == 0o700
    assert _mode(cache / gid) == 0o600


def _clear_profile_env(monkeypatch):
    for name in ("PUSHFRAME_PROBE_CHROME_PROFILE", "AURA_PROBE_CHROME_PROFILE",
                 "USHFRAME_PROBE_CHROME_PROFILE"):
        monkeypatch.delenv(name, raising=False)


def test_default_profile_dir_created_private(tmp_path, monkeypatch):
    from pushframe.google import bootstrap as bs
    _clear_profile_env(monkeypatch)
    target = tmp_path / "cfg" / "chrome-profile"
    monkeypatch.setattr(bs, "DEFAULT_PROFILE_DIR", target)
    assert bs._require_profile() == target
    assert _mode(tmp_path / "cfg") == 0o700
    assert _mode(target) == 0o700


def test_default_profile_dir_tightened_when_preexisting_loose(tmp_path, monkeypatch):
    from pushframe.google import bootstrap as bs
    _clear_profile_env(monkeypatch)
    target = tmp_path / "chrome-profile"
    target.mkdir()
    os.chmod(target, 0o775)
    monkeypatch.setattr(bs, "DEFAULT_PROFILE_DIR", target)
    bs._require_profile()
    assert _mode(target) == 0o700


def test_env_override_profile_never_chmoded(tmp_path, monkeypatch):
    from pushframe.google import bootstrap as bs
    _clear_profile_env(monkeypatch)
    chosen = tmp_path / "mine"
    chosen.mkdir()
    os.chmod(chosen, 0o775)
    monkeypatch.setenv("PUSHFRAME_PROBE_CHROME_PROFILE", str(chosen))
    bs._require_profile()
    assert _mode(chosen) == 0o775
    fresh = tmp_path / "fresh"
    monkeypatch.setenv("PUSHFRAME_PROBE_CHROME_PROFILE", str(fresh))
    bs._require_profile()
    assert _mode(fresh) == 0o700


def test_manifest_save_dirs_and_file_private(tmp_path):
    from pushframe.google.manifest import GoogleManifest
    target = tmp_path / "pairs" / "cadre" / "manifest.json"
    GoogleManifest().save(target)
    assert _mode(tmp_path / "pairs") == 0o700
    assert _mode(tmp_path / "pairs" / "cadre") == 0o700
    assert _mode(target) == 0o600


def test_write_budget_save_private(tmp_path):
    from datetime import datetime, timezone
    from pushframe.ratelimit import WriteBudget
    path = tmp_path / "state" / "budget-x.json"
    budget = WriteBudget(capacity=30, refill_per_min=0.75, path=path,
                         tokens=7.0, updated_at=datetime(2026, 1, 1, tzinfo=timezone.utc))
    budget.save()
    assert _mode(path.parent) == 0o700
    assert _mode(path) == 0o600
    # pre-existing loose file ends up 0600 and still round-trips
    os.chmod(path, 0o664)
    budget.tokens = 9.0
    budget.save()
    assert _mode(path) == 0o600
    loaded = WriteBudget.load(path, capacity=30, refill_per_min=0.75)
    assert loaded.tokens == 9.0

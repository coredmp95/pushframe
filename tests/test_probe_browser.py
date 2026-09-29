"""Offline tests for the cookie vault (plan 16-02 T1).

Phase 17 (plan 17-01 T3): the vault MOVED to pushframe/google/vault.py —
imports and monkeypatch targets updated accordingly; test bodies unchanged.
The package-side vault suite lives in test_google_vault.py (same boundary
coverage plus the legacy-fallback migration tests).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pushframe.google.vault import CookieVaultError, cookies_for_httpx, load, save  # noqa: E402

COOKIES = [
    {"name": "SID", "value": "fake-sid-value", "domain": ".google.com"},
    {"name": "SAPISID", "value": "fake-sapisid-value", "domain": ".google.com"},
]


def _vault(tmp_path: Path) -> Path:
    return tmp_path / "vault.json"


def test_save_load_roundtrip_preserves_names_values(tmp_path):
    vault = _vault(tmp_path)
    save(COOKIES, path=vault)
    loaded = load(path=vault)
    assert [(c["name"], c["value"]) for c in loaded] == [(c["name"], c["value"]) for c in COOKIES]
    assert cookies_for_httpx(path=vault) == {"SID": "fake-sid-value",
                                             "SAPISID": "fake-sapisid-value"}


def test_saved_file_mode_is_0600(tmp_path):
    vault = _vault(tmp_path)
    save(COOKIES, path=vault)
    mode = os.stat(vault).st_mode & 0o777
    assert mode == 0o600, f"vault must be 0600 after save, got {oct(mode)}"


def test_load_from_missing_vault_raises(tmp_path):
    with pytest.raises(CookieVaultError) as exc:
        load(path=tmp_path / "nope.json")
    assert "bootstrap" in str(exc.value)


def test_save_inside_repo_refused(tmp_path, monkeypatch):
    import pushframe.google.vault as cv
    fake_root = tmp_path / "repo"
    fake_root.mkdir()
    monkeypatch.setattr(cv, "_REPO_ROOT", fake_root)
    with pytest.raises(CookieVaultError) as exc:
        save(COOKIES, path=fake_root / "leak.json")
    assert "inside the git repo" in str(exc.value)


def test_sync_path_caller_structurally_refused(tmp_path, monkeypatch):
    """A module named under the sync denylist cannot read the vault — the
    refusal names the offending module (D-06's structural boundary)."""
    import pushframe.google.vault as cv
    vault = _vault(tmp_path)
    save(COOKIES, path=vault)

    # Simulate the offending caller: a fake module object whose __name__
    # lands under the denylist, placed at the frame load() will inspect.
    import types
    fake_caller = types.ModuleType("pushframe.sync.engine")
    monkeypatch.setattr(cv, "_caller_module_name", lambda: fake_caller.__name__)

    with pytest.raises(CookieVaultError) as exc:
        load(path=vault)
    assert "pushframe.sync" in str(exc.value) and "denylist" in str(exc.value)


def test_no_playwright_import_in_test_graph():
    """The offline suite must never import playwright (isolation rule)."""
    assert "playwright" not in sys.modules, \
        "playwright must never appear in the offline test import graph"

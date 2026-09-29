"""Phase 20 (IDN-03): the one-time config migration
`~/.config/auraframes/` → `~/.config/pushframe/`.

All tests run against tmp_path homes via monkeypatch of the migration
module's OLD/NEW constants — the real `~/.config` is never touched. The
migration must be: one-shot (idempotent), non-destructive (old dir kept),
mode-preserving (the 0600 vault stays 0600), and silent on fresh machines.
"""
import json
import os
import stat
from pathlib import Path

import pytest

import pushframe.migration as migration
from pushframe.migration import (
    migrate_config_dir,
    migration_notice,
    should_migrate,
)


@pytest.fixture
def homes(tmp_path, monkeypatch):
    """Point the migration at tmp directories; return (old, new) paths."""
    old = tmp_path / "cfg" / "auraframes"
    new = tmp_path / "cfg" / "pushframe"
    monkeypatch.setattr(migration, "OLD_CONFIG_DIR", old)
    monkeypatch.setattr(migration, "NEW_CONFIG_DIR", new)
    return old, new


def _make_vault(directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    vault = directory / "google-cookies.json"
    vault.write_text(json.dumps([{"name": "SID", "value": "x"}]))
    os.chmod(vault, 0o600)
    return vault


def test_fresh_machine_is_silent_noop(homes):
    old, new = homes
    assert not old.exists()
    assert should_migrate() is False
    assert migrate_config_dir() is None
    assert migration_notice() is None
    assert not new.exists()


def test_migration_copies_state_and_keeps_old_directory(homes):
    old, new = homes
    vault = _make_vault(old)
    manifest = old / "google-manifest.json"
    manifest.write_text("{}")
    profile = old / "chrome-profile"
    (profile / "Default").mkdir(parents=True)
    (profile / "Default" / "Cookies").write_text("sqlite-bytes")
    (old / "budget-abc123.json").write_text("{}")

    notice = migration_notice()

    assert notice is not None and str(new) in notice and "auraframes" in notice
    assert (new / "google-cookies.json").read_text() == vault.read_text()
    assert (new / "google-manifest.json").exists()
    assert (new / "chrome-profile" / "Default" / "Cookies").read_text() == "sqlite-bytes"
    assert (new / "budget-abc123.json").exists()
    # non-destructive: the old directory keeps everything
    assert vault.exists() and manifest.exists() and profile.exists()


def test_migration_preserves_0600_on_the_vault(homes):
    old, new = homes
    _make_vault(old)

    migrate_config_dir()

    mode = stat.S_IMODE((new / "google-cookies.json").stat().st_mode)
    assert mode == 0o600


def test_migration_is_idempotent(homes):
    old, new = homes
    _make_vault(old)

    first = migration_notice()
    assert first is not None

    # a second run must be a silent no-op (new dir exists now)
    assert migration_notice() is None
    assert migrate_config_dir() is None
    # and must not duplicate or alter the copied state
    entries = sorted(p.name for p in new.iterdir())
    assert entries == ["google-cookies.json"]


def test_never_overwrites_an_existing_new_directory(homes):
    old, new = homes
    _make_vault(old)
    new.mkdir(parents=True)
    sentinel = new / "user-created.json"
    sentinel.write_text("keep me")

    assert migration_notice() is None
    assert sentinel.read_text() == "keep me"
    assert not (new / "google-cookies.json").exists()


def test_main_triggers_migration_once(monkeypatch, capsys, tmp_path):
    """The CLI entry point runs the migration before dispatch and prints the
    notice exactly once; an unknown command still exits non-zero after it."""
    old = tmp_path / "cfg" / "auraframes"
    new = tmp_path / "cfg" / "pushframe"
    _make_vault(old)
    monkeypatch.setattr(migration, "OLD_CONFIG_DIR", old)
    monkeypatch.setattr(migration, "NEW_CONFIG_DIR", new)
    monkeypatch.setenv("PUSHFRAME_EMAIL", "u@example.invalid")
    monkeypatch.setenv("PUSHFRAME_PASSWORD", "pw")

    from pushframe.cli import main

    # --help exits via argparse's SystemExit(0); the migration notice must
    # already have been printed and the copy done before that.
    with pytest.raises(SystemExit) as excinfo:
        main(["status", "--help"])
    assert excinfo.value.code == 0
    out = capsys.readouterr().out
    assert "Config migrated" in out and str(new) in out
    assert new.is_dir()

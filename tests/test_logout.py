"""Phase 24 (SEC-03, D-02): `pushframe logout` deletes the stored TOKEN and
only the token — email, settings, mode 0600 all survive. Idempotent. Token
material never appears in output."""
import json
import stat

import pytest

from pushframe.utils import settings


@pytest.fixture
def cfg_path(tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    monkeypatch.setattr(settings, "CONFIG_PATH", path)
    return path


def _seed(cfg_path):
    from pushframe import config_store
    config_store.update(email="me@example.invalid", auth_token="tok-xyz",  # noqa: S105
                        user_id="u-7", default_frame="Cadre")
    config_store.update(settings={"AURA_COUNTRY": "FR"})


def test_logout_removes_token_keeps_everything_else(cfg_path, capsys):
    _seed(cfg_path)
    from pushframe.cli import run_logout
    assert run_logout() == 0

    data = json.loads(cfg_path.read_text())
    assert "auth_token" not in data            # noqa: S105 — gone entirely
    assert "user_id" not in data
    assert data["email"] == "me@example.invalid"      # kept (D-02)
    assert data["settings"]["AURA_COUNTRY"] == "FR"   # settings kept
    assert data["default_frame"] == "Cadre"           # pairs-like keys kept
    mode = stat.S_IMODE(cfg_path.stat().st_mode)
    assert mode == 0o600                       # mode preserved
    out = capsys.readouterr().out
    assert "tok-xyz" not in out                # noqa: S105 — never printed


def test_logout_is_idempotent(cfg_path, capsys):
    _seed(cfg_path)
    from pushframe.cli import run_logout
    assert run_logout() == 0
    assert run_logout() == 0                   # second run: still 0, no error
    out = capsys.readouterr().out
    assert "no stored session" in out.lower()


def test_logout_without_config_is_clean(cfg_path, capsys):
    from pushframe.cli import run_logout
    assert run_logout() == 0
    assert "no stored session" in capsys.readouterr().out.lower()


def test_logout_via_cli_dispatch(cfg_path, capsys, monkeypatch):
    """main() dispatch wiring: `pushframe logout` reaches run_logout."""
    _seed(cfg_path)
    import sys
    monkeypatch.setattr(sys, "argv", ["pushframe", "logout"])
    from pushframe.cli import main
    assert main() == 0
    data = json.loads(cfg_path.read_text())
    assert "auth_token" not in data  # noqa: S105

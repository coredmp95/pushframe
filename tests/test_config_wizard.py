"""Phase 23 (CFG-01..04): the pushframe config command family.

The wizard is login-tested (D-03): nothing is written unless the live
login succeeds. show prints the EFFECTIVE config with secrets redacted
and per-key sources; import migrates an .env without storing keys the
environment already resolves; set/get validate against known settings.
"""
import io
import json
import stat

import httpx
import pytest

from pushframe import config_store
from pushframe.utils import settings


@pytest.fixture
def cfg_path(tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    monkeypatch.setattr(settings, "CONFIG_PATH", path)
    return path


@pytest.fixture
def fake_login_ok(monkeypatch):
    """Patch the wizard's login seam: credentials OK, token returned."""
    def _login(email, password):
        assert password == 's3cret'  # noqa: S105 - fixture contract
        return {"email": email, "auth_token": "tok-abc-123",  # noqa: S105
                "frames": [{"name": "Cadre de Fabrice", "id": "f1"}]}
    monkeypatch.setattr("pushframe.cli._wizard_login", _login)


@pytest.fixture
def fake_login_fail(monkeypatch):
    def _login(email, password):
        raise RuntimeError("Aura login failed: invalid credentials")
    monkeypatch.setattr("pushframe.cli._wizard_login", _login)


def _feed(monkeypatch, lines):
    it = iter(lines)
    monkeypatch.setattr("builtins.input", lambda *a: next(it))
    import getpass as gp
    monkeypatch.setattr(gp, "getpass", lambda *a: next(it))


def test_wizard_success_writes_0600_config(cfg_path, monkeypatch, fake_login_ok):
    _feed(monkeypatch, ["coredmp95@gmail.com", "s3cret", "1", "n"])  # noqa: S105
    from pushframe.cli import run_config
    rc = run_config(wizard_args=[], stdin_isatty=True)
    assert rc == 0
    data = json.loads(cfg_path.read_text())
    assert data["email"] == "coredmp95@gmail.com"
    assert data["auth_token"] == "tok-abc-123"  # noqa: S105
    assert data["default_frame"] == "Cadre de Fabrice"
    assert data["debug"] is False
    mode = stat.S_IMODE(cfg_path.stat().st_mode)
    assert mode == 0o600


def test_wizard_failed_login_writes_nothing(cfg_path, monkeypatch, fake_login_fail, capsys):
    _feed(monkeypatch, ["coredmp95@gmail.com", "wrong-pass"])  # noqa: S105
    from pushframe.cli import run_config
    rc = run_config(wizard_args=[], stdin_isatty=True)
    assert rc == 1
    assert not cfg_path.exists()
    assert "invalid credentials" in capsys.readouterr().out


def test_wizard_non_tty_fails_loud(cfg_path, monkeypatch):
    import sys
    monkeypatch.setattr(sys, "stdin", io.StringIO())  # not a tty
    from pushframe.cli import run_config
    rc = run_config(wizard_args=[], stdin_isatty=False)
    assert rc == 1


def test_show_redacts_secrets_and_lists_sources(cfg_path, capsys):
    config_store.update(email="me@example.com", auth_token="tok-secret-9",  # noqa: S105
                        default_frame="Cadre")
    from pushframe.cli import run_config
    rc = run_config(wizard_args=["show"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "tok-secret-9" not in out  # noqa: S105
    assert "me@example.com" in out
    assert "file" in out and "default" in out


def test_import_maps_env_keys_and_skips_env_resolved(cfg_path, monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("PUSHFRAME_LOCALE", "de-DE")  # env-resolved: must NOT be stored
    envfile = tmp_path / ".env"
    envfile.write_text("PUSHFRAME_LOCALE=fr-FR\nPUSHFRAME_COUNTRY=FR\n")
    from pushframe.cli import run_config
    rc = run_config(wizard_args=["import", "--file", str(envfile)])
    assert rc == 0
    data = json.loads(cfg_path.read_text())
    assert "LOCALE" not in data.get("settings", {})   # env wins, not stored
    assert data["settings"]["AURA_COUNTRY"] == "FR"   # file-only key stored
    out = capsys.readouterr().out
    assert "env" in out and "file" in out


def test_journey_wizard_then_status_without_any_env(cfg_path, monkeypatch, capsys):
    """Roadmap §23 criterion 1, hermetic end-to-end: the wizard stores a
    session, then `status` works with ZERO env vars set — no login call on
    the status path (the stored token session is resumed). This is the
    same flow the container journey replays against a real install.
    """
    from pushframe.cli import run_config
    monkeypatch.setattr('pushframe.cli._wizard_login', lambda e, p: {
        'email': e, 'auth_token': 'journey-tok', 'user_id': 'u-1',
        'frames': [{'name': 'Cadre', 'id': 'c-1'}]})
    _feed(monkeypatch, ['me@example.invalid', 'pw-journey', '1', 'n'])  # noqa: S105
    assert run_config(wizard_args=[], stdin_isatty=True) == 0

    from pushframe.utils import settings
    from pushframe import config_store
    assert config_store.load()['email'] == 'me@example.invalid'

    for var in ('PUSHFRAME_EMAIL', 'AURA_EMAIL', 'PUSHFRAME_PASSWORD', 'AURA_PASSWORD'):
        monkeypatch.delenv(var, raising=False)
    from tests.offline import offline_aura
    aura = offline_aura(overrides={
        '/v5/login.json': httpx.Response(500, json={'error': 'status must not log in'})})
    from pushframe.cli import run_status
    assert run_status(aura=aura) == 0
    out = capsys.readouterr().out
    assert 'Logged in as me@example.invalid' in out
    assert 'journey-tok' not in out


def test_import_golden_same_effective_config_as_env_path(cfg_path, monkeypatch, capsys, tmp_path):
    """Roadmap §23 criterion 3: import an .env, then resolve the same keys
    with env vars instead — the effective values must be identical.
    (This run has no env set for the two keys: file-only vs env-only.)
    """
    envfile = tmp_path / ".env"
    envfile.write_text("PUSHFRAME_LOCALE=fr-FR\nPUSHFRAME_COUNTRY=FR\n")
    from pushframe.cli import run_config
    assert run_config(wizard_args=["import", "--file", str(envfile)]) == 0

    from pushframe.utils import settings
    from pushframe import config_store as store
    assert store.setting("LOCALE") == "fr-FR"          # stored by import
    assert store.setting("AURA_COUNTRY") == "FR"

    # golden: a machine using ONLY env vars resolves the same values
    monkeypatch.setenv("PUSHFRAME_LOCALE", "fr-FR")
    monkeypatch.setenv("PUSHFRAME_COUNTRY", "FR")
    assert settings.LOCALE == store.setting("LOCALE")
    assert settings.AURA_COUNTRY == store.setting("AURA_COUNTRY")


def test_set_get_path_and_unknown_key(cfg_path, capsys):
    from pushframe.cli import run_config
    assert run_config(wizard_args=["set", "AURA_WRITE_BUDGET_CAPACITY", "60"]) == 0
    assert run_config(wizard_args=["get", "AURA_WRITE_BUDGET_CAPACITY"]) == 0
    assert "60" in capsys.readouterr().out
    assert run_config(wizard_args=["path"]) == 0
    rc = run_config(wizard_args=["set", "NOT_A_KEY", "x"])
    assert rc == 1
    assert "LOCALE" in capsys.readouterr().out  # known keys listed
    # auth_token is wizard-only
    assert run_config(wizard_args=["set", "auth_token", "x"]) == 1  # noqa: S105

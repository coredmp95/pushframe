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
    # Identity provisioning (5.1.1): the first wizard run on a fresh store
    # must persist a unique device id — never the shared all-zeros default.
    identity = data["settings"]["DEVICE_IDENTIFIER"]
    assert identity != "0000000000000000"
    assert identity.count("-") == 4 and len(identity) == 36
    mode = stat.S_IMODE(cfg_path.stat().st_mode)
    assert mode == 0o600


def test_wizard_provisioned_identity_survives_save_and_rerun(cfg_path, monkeypatch, fake_login_ok):
    """The provisioned id is written BEFORE the login and must survive the
    wizard's final save (no settings-key clobber), and a second run must
    keep it byte-identical (no re-roll — device ids are stable)."""
    _feed(monkeypatch, ["coredmp95@gmail.com", "s3cret", "1", "n"])  # noqa: S105
    from pushframe.cli import run_config
    assert run_config(wizard_args=[], stdin_isatty=True) == 0
    first = json.loads(cfg_path.read_text())["settings"]["DEVICE_IDENTIFIER"]

    _feed(monkeypatch, ["coredmp95@gmail.com", "", "", "", ""])  # noqa: S105
    assert run_config(wizard_args=[], stdin_isatty=True) == 0
    second = json.loads(cfg_path.read_text())["settings"]["DEVICE_IDENTIFIER"]
    assert first == second


def test_wizard_respects_explicit_device_identity(cfg_path, monkeypatch, fake_login_ok):
    """An explicit id (file or env) is never second-guessed — provisioning
    only fills the ABSENCE of one."""
    config_store.update(settings={"DEVICE_IDENTIFIER": "my-explicit-id"})
    _feed(monkeypatch, ["coredmp95@gmail.com", "s3cret", "1", "n"])  # noqa: S105
    from pushframe.cli import run_config
    assert run_config(wizard_args=[], stdin_isatty=True) == 0
    assert json.loads(cfg_path.read_text())["settings"]["DEVICE_IDENTIFIER"] \
        == "my-explicit-id"


def test_wizard_provisioning_keeps_sibling_settings(cfg_path, monkeypatch, fake_login_ok):
    """Provisioning must not clobber sibling file settings (update(
    settings=...) REPLACES the map; the load→mutate→save path preserves
    them)."""
    config_store.update(settings={"LOCALE": "fr-FR"})
    _feed(monkeypatch, ["coredmp95@gmail.com", "s3cret", "1", "n"])  # noqa: S105
    from pushframe.cli import run_config
    assert run_config(wizard_args=[], stdin_isatty=True) == 0
    st = json.loads(cfg_path.read_text())["settings"]
    assert st["LOCALE"] == "fr-FR"
    assert st["DEVICE_IDENTIFIER"] != "0000000000000000"


def test_wizard_enter_keeps_existing_email(cfg_path, monkeypatch, capsys):
    """CLI contract (venus, 5.1.2): the banner promises "Enter keeps it"
    and Enter must keep it — an empty answer on a configured install keeps
    the stored email and offers the stored token (Enter again finishes the
    wizard with ZERO API calls). A fresh install (no stored email) still
    aborts named on an empty answer."""
    from pushframe.cli import run_config
    config_store.update(email='coredmp95@gmail.com', auth_token='tok-keep')  # noqa: S105

    _feed(monkeypatch, ['', ''])  # Enter email → Enter keep-token → done
    assert run_config(wizard_args=[], stdin_isatty=True) == 0
    data = json.loads(cfg_path.read_text())
    assert data['email'] == 'coredmp95@gmail.com'
    assert data['auth_token'] == 'tok-keep'  # noqa: S105
    assert 'config saved' in capsys.readouterr().out

    # fresh install: no stored email — empty Enter aborts, writes no session
    cfg_path.unlink()
    _feed(monkeypatch, [''])
    assert run_config(wizard_args=[], stdin_isatty=True) == 1
    data = json.loads(cfg_path.read_text())
    assert 'email' not in data and 'auth_token' not in data  # noqa: S105
    assert 'no email given' in capsys.readouterr().out


def test_wizard_frame_number_enter_skips_prompt_contract(cfg_path, monkeypatch, fake_login_ok, capsys):
    """Prompt-contract audit: 'default frame number (Enter to skip)' — a
    bare Enter must skip and store NO default_frame; a valid index selects
    the frame."""
    from pushframe.cli import run_config

    # Enter skips: no default_frame lands in the file
    _feed(monkeypatch, ['me@example.invalid', 's3cret', '', ''])  # noqa: S105
    assert run_config(wizard_args=[], stdin_isatty=True) == 0
    data = json.loads(cfg_path.read_text())
    assert 'default_frame' not in data

    # a valid index selects the frame
    cfg_path.unlink()
    _feed(monkeypatch, ['me@example.invalid', 's3cret', '1', ''])  # noqa: S105
    assert run_config(wizard_args=[], stdin_isatty=True) == 0
    assert json.loads(cfg_path.read_text())['default_frame'] == 'Cadre de Fabrice'


def test_wizard_frame_number_out_of_range_re_asked_until_valid_or_skip(cfg_path, monkeypatch, fake_login_ok, capsys):
    """The loop (wart fix): an out-of-range or non-numeric answer is
    RE-ASKED with the named remedy — 'invalid choice, pick 1-N or Enter to
    skip' — until a valid number is picked or an explicit Enter skips."""
    from pushframe.cli import run_config

    # invalid '9' → re-ask → '1' selected
    prompts = []
    answers = iter(['me@example.invalid', '9', '1', ''])

    def fake_input(prompt=''):
        prompts.append(prompt)
        return next(answers)

    monkeypatch.setattr('builtins.input', fake_input)
    monkeypatch.setattr('getpass.getpass', lambda *a: 's3cret')  # noqa: S105
    assert run_config(wizard_args=[], stdin_isatty=True) == 0
    data = json.loads(cfg_path.read_text())
    assert data['default_frame'] == 'Cadre de Fabrice'
    assert prompts.count('invalid choice, pick 1-1 or Enter to skip: ') == 1
    assert any(p.endswith('default frame number (Enter to skip): ')
               for p in prompts)

    # invalid '0' → re-ask → explicit Enter skips (session still saved)
    cfg_path.unlink()
    prompts.clear()
    answers = iter(['me@example.invalid', '0', '', ''])
    assert run_config(wizard_args=[], stdin_isatty=True) == 0
    data = json.loads(cfg_path.read_text())
    assert 'default_frame' not in data and data['auth_token']  # noqa: S105
    assert prompts.count('invalid choice, pick 1-1 or Enter to skip: ') == 1


def test_show_warns_on_all_zeros_device_identity(cfg_path, capsys, monkeypatch):
    """Identity hygiene (5.1.1): config show names the shared all-zeros
    device id with its remedy; a provisioned or explicit id silences it."""
    from pushframe.cli import run_config
    monkeypatch.delenv("PUSHFRAME_DEVICE_IDENTIFIER", raising=False)
    monkeypatch.delenv("AURA_DEVICE_IDENTIFIER", raising=False)

    assert run_config(wizard_args=["show"]) == 0
    out = capsys.readouterr().out
    assert "WARNING" in out and "uuidgen" in out

    config_store.update(
        settings={"DEVICE_IDENTIFIER": "45ce0cfa-fe12-4850-b8de-f3ca56fbb36d"})
    assert run_config(wizard_args=["show"]) == 0
    assert "WARNING" not in capsys.readouterr().out


def test_wizard_failed_login_writes_nothing(cfg_path, monkeypatch, fake_login_fail, capsys):
    """D-03, amended for 5.1.1 identity provisioning: a failed login
    persists NO session facts and NO default_frame — the only pre-login
    write allowed is the account-independent device identity block."""
    _feed(monkeypatch, ["coredmp95@gmail.com", "wrong-pass"])  # noqa: S105
    from pushframe.cli import run_config
    rc = run_config(wizard_args=[], stdin_isatty=True)
    assert rc == 1
    assert "invalid credentials" in capsys.readouterr().out
    assert cfg_path.exists()  # identity block only
    data = json.loads(cfg_path.read_text())
    assert "email" not in data and "auth_token" not in data  # noqa: S105
    assert "default_frame" not in data
    assert "DEVICE_IDENTIFIER" in data.get("settings", {})


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

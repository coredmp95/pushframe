"""Phase 24 (SEC-01/02): establish_session — the one session path.

Precedence (24-RESEARCH §1):
  1. env password present            → real login (override, discouraged)
  2. config has auth_token           → resume_session(email, token, uid) — NO login call
  3. TTY                             → one password prompt, login, PERSIST token only
  4. nothing                         → named error with remedy (no credentials)

SEC-01 hard rule asserted here: the password is NEVER persisted — file
content checks, not just behavior checks.
"""
import json
import stat

import httpx
import pytest

from pushframe.utils import settings
from pushframe import config_store


@pytest.fixture
def cfg_path(tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    monkeypatch.setattr(settings, "CONFIG_PATH", path)
    return path


def _clear_creds(monkeypatch):
    for var in ("PUSHFRAME_EMAIL", "AURA_EMAIL",
                "PUSHFRAME_PASSWORD", "AURA_PASSWORD"):
        monkeypatch.delenv(var, raising=False)


def _store_session(cfg_path, email="vaulted@example.invalid"):
    config_store.update(email=email, auth_token="tok-abc",  # noqa: S105
                        user_id="u-1")


def test_token_session_resumes_without_login_call(cfg_path, monkeypatch):
    """Criterion 1's core: a stored session is used AS-IS — zero login calls."""
    _clear_creds(monkeypatch)
    _store_session(cfg_path)
    monkeypatch.setattr("pushframe.session._wizard_login",
                        lambda *a, **k: pytest.fail("login must NOT be called"))

    from pushframe.session import establish_session
    aura = establish_session(aura=_RecordingAura())
    assert aura.resumed_with == {"email": "vaulted@example.invalid",
                                 "auth_token": "tok-abc",  # noqa: S105
                                 "user_id": "u-1"}


class _RecordingAura:
    """Stand-in recording which session method was used."""
    resumed_with = None

    def login(self, email=None, password=None):
        self.logged_in = (email, password)
        self.auth_token = "fresh-tok"  # noqa: S105
        self.user_id = "u-9"
        return self

    def resume_session(self, email=None, auth_token=None, user_id=None):
        type(self).resumed_with = {"email": email, "auth_token": auth_token,
                                   "user_id": user_id}
        return self


def test_env_password_still_wins_the_override(cfg_path, monkeypatch):
    _clear_creds(monkeypatch)
    _store_session(cfg_path)  # a stored session exists but env overrides
    monkeypatch.setenv("PUSHFRAME_EMAIL", "env@example.invalid")
    monkeypatch.setenv("PUSHFRAME_PASSWORD", "env-pw")  # noqa: S105

    from pushframe.session import establish_session
    aura = _RecordingAura()
    monkeypatch.setattr("pushframe.aura.Aura", lambda: aura)
    establish_session(aura=aura)
    assert aura.logged_in == ("env@example.invalid", "env-pw")  # noqa: S105


def test_tty_prompt_logs_in_and_persists_token_never_password(cfg_path, monkeypatch):
    _clear_creds(monkeypatch)
    monkeypatch.setattr("pushframe.session._wizard_login",
                        lambda e, p: {"email": e, "auth_token": "new-tok",  # noqa: S105
                                      "user_id": "u-2", "frames": []})
    monkeypatch.setattr("builtins.input", lambda *a: "me@example.invalid")
    import getpass as gp
    monkeypatch.setattr(gp, "getpass", lambda *a: "the-password")  # noqa: S105

    from pushframe.session import establish_session
    establish_session(aura=_RecordingAura(), stdin_isatty=True)

    data = json.loads(cfg_path.read_text())
    assert data["email"] == "me@example.invalid"
    assert data["auth_token"] == "new-tok"  # noqa: S105
    assert data["user_id"] == "u-2"
    # SEC-01: the password must NOT be anywhere in the file
    assert "the-password" not in cfg_path.read_text()  # noqa: S105
    assert "password" not in json.loads(cfg_path.read_text())


def test_no_credentials_named_error_with_remedy(cfg_path, monkeypatch):
    _clear_creds(monkeypatch)
    from pushframe.session import establish_session, NoCredentialsError
    with pytest.raises(NoCredentialsError) as exc:
        establish_session(aura=_RecordingAura(), stdin_isatty=False)
    assert "pushframe config" in str(exc.value)


def test_expired_token_tty_prompts_once_and_persists_new_token(cfg_path, monkeypatch):
    """Criterion 2, establishment half: stored token 401s → ONE prompt →
    new token persisted → session continues (no second prompt)."""
    _clear_creds(monkeypatch)
    _store_session(cfg_path)

    from pushframe import session as session_mod
    prompts = []

    def _resume(self, email=None, auth_token=None, user_id=None):
        raise RuntimeError("Client error '401 Unauthorized' for url '...'")

    monkeypatch.setattr(_RecordingAura, "resume_session", _resume)

    def _fake_login(e, p):
        prompts.append(p)
        return {"email": e, "auth_token": "refreshed-tok",  # noqa: S105
                "user_id": "u-1", "frames": []}

    monkeypatch.setattr(session_mod, "_wizard_login", _fake_login)
    monkeypatch.setattr("builtins.input", lambda *a: "vaulted@example.invalid")
    import getpass as gp
    monkeypatch.setattr(gp, "getpass", lambda *a: "typed-pw")  # noqa: S105

    aura = _RecordingAura()
    # resume fails once (expired), then the prompt path logs in
    established = session_mod.establish_session(aura=aura, stdin_isatty=True)
    assert prompts == ["typed-pw"]  # noqa: S105 — exactly ONE prompt
    data = json.loads(cfg_path.read_text())
    assert data["auth_token"] == "refreshed-tok"  # noqa: S105
    assert established is aura


def test_expired_token_non_tty_named_error_no_prompt(cfg_path, monkeypatch):
    _clear_creds(monkeypatch)
    _store_session(cfg_path)

    from pushframe import session as session_mod
    from pushframe.session import SessionExpiredError

    def _resume(self, email=None, auth_token=None, user_id=None):
        raise RuntimeError("Client error '401 Unauthorized' for url '...'")

    def _fail(*a, **k):
        raise AssertionError("no prompt allowed in non-TTY")

    monkeypatch.setattr(_RecordingAura, "resume_session", _resume)
    monkeypatch.setattr(session_mod, "_wizard_login", _fail)

    with pytest.raises(SessionExpiredError) as exc:
        session_mod.establish_session(aura=_RecordingAura(), stdin_isatty=False)
    assert "pushframe config" in str(exc.value) or "logout" in str(exc.value)


def test_resume_401_propagates_into_the_tty_path():
    """The expired-token detection keys off the resume call raising with a
    401 signature — other resume failures (corrupt config) are NOT swallowed
    into a password prompt."""
    from pushframe import session as session_mod
    assert session_mod._is_auth_failure(RuntimeError(
        "Client error '401 Unauthorized' for url '...'"))
    assert not session_mod._is_auth_failure(ValueError("corrupt config"))


def test_prompt_login_bare_enter_keeps_stored_email(cfg_path, monkeypatch, capsys):
    """Prompt-contract audit: the session-path login prompt shows the same
    "current email — Enter keeps it" banner as the wizard, and a bare Enter
    KEEPS it — exactly one login call, none with an empty email."""
    _clear_creds(monkeypatch)
    _store_session(cfg_path, email="vaulted@example.invalid")

    from pushframe import session as session_mod
    logins = []

    def _fake_login(e, p):
        logins.append((e, p))
        return {"email": e, "auth_token": "fresh-tok",  # noqa: S105
                "user_id": "u-1", "frames": []}

    monkeypatch.setattr(session_mod, "_wizard_login", _fake_login)
    monkeypatch.setattr("builtins.input", lambda *a: "")  # bare Enter
    import getpass as gp
    monkeypatch.setattr(gp, "getpass", lambda *a: "typed-pw")  # noqa: S105

    aura = session_mod._prompt_login(_RecordingAura(), True)

    out = capsys.readouterr().out
    assert "current email: vaulted@example.invalid" in out
    assert "Enter keeps it" in out
    assert logins == [("vaulted@example.invalid", "typed-pw")]  # noqa: S105
    assert aura is not None
    assert json.loads(cfg_path.read_text())["auth_token"] == "fresh-tok"  # noqa: S105


def test_prompt_login_empty_email_on_fresh_install_aborts_named(cfg_path, monkeypatch, capsys):
    """The other branch of the same contract: with NO stored email, a bare
    Enter aborts named BEFORE the password question and before any login —
    establish_session surfaces it as the named no-credentials error."""
    _clear_creds(monkeypatch)

    from pushframe import session as session_mod
    from pushframe.session import NoCredentialsError

    monkeypatch.setattr(
        session_mod, "_wizard_login",
        lambda *a, **k: pytest.fail("no login call with an empty email"))
    monkeypatch.setattr("builtins.input", lambda *a: "")

    with pytest.raises(NoCredentialsError) as exc:
        session_mod._prompt_login_or_fail(_RecordingAura(), True)
    assert "no email given" in capsys.readouterr().out
    assert "login aborted" in str(exc.value)

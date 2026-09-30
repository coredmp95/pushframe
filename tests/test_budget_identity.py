"""The account-identity duality (venus 2026-09-30, debug
gsync-apply-budget-none-crash): auth resolves env-then-stored-session,
while the write-budget layer read env-ONLY — a token-session host (venus:
no env vars) crashed `google-sync --apply` on `email.encode()` AFTER the
operator confirmed the plan. The fix is ONE resolver
(`session.account_email()`) wired into every budget call site, and a
never-crash `_build_write_budget(None)`.

Regression shape (must NEVER reproduce): the crash happened after the
interactive confirmation, with a valid stored session and an empty
environment — the exact intended venus posture.
"""
import hashlib
import json

import pytest

from pushframe.utils import settings


@pytest.fixture
def token_session_no_env(tmp_path, monkeypatch):
    """The venus posture: stored session, zero env identity, state dir
    pinned under tmp (the budget file must land there, not in $HOME)."""
    monkeypatch.setattr(settings, "CONFIG_PATH", tmp_path / "config.json")
    from pushframe import config_store
    config_store.update(email="coredmp95@gmail.com", auth_token="tok-x",
                        user_id="u-1")
    for var in ("PUSHFRAME_EMAIL", "AURA_EMAIL",
                "PUSHFRAME_PASSWORD", "AURA_PASSWORD"):
        monkeypatch.delenv(var, raising=False)
    import pushframe.cli as cli_mod
    monkeypatch.setattr(cli_mod, "AURA_STATE_DIR", tmp_path / "state")
    return tmp_path


def test_account_email_resolves_stored_session(token_session_no_env):
    from pushframe.session import account_email
    assert account_email() == "coredmp95@gmail.com"


def test_account_email_env_overrides_stored(token_session_no_env, monkeypatch):
    monkeypatch.setenv("PUSHFRAME_EMAIL", "override@example.invalid")
    from pushframe.session import account_email
    assert account_email() == "override@example.invalid"


def test_account_email_none_without_any_identity(token_session_no_env, monkeypatch):
    from pushframe import config_store
    import os
    config_store.update(email=None) if hasattr(config_store, "update") else None
    # remove the stored email by rewriting the config without it
    cfg = json.loads((token_session_no_env / "config.json").read_text())
    cfg.pop("email", None)
    (token_session_no_env / "config.json").write_text(json.dumps(cfg))
    from pushframe.session import account_email
    assert account_email() is None


def test_build_write_budget_none_email_skips_never_crashes(token_session_no_env,
                                                           capsys):
    """The crash line itself: email=None must SKIP pacing (named on stderr),
    never raise — the operator has already confirmed the apply at that
    point."""
    from pushframe.cli import _build_write_budget
    budget = _build_write_budget(None, ignore_budget=False)
    assert budget is None
    assert 'no account identity' in capsys.readouterr().err


def test_build_write_budget_email_keys_state_file(token_session_no_env):
    from pushframe.cli import _build_write_budget
    budget = _build_write_budget("coredmp95@gmail.com", ignore_budget=False)
    assert budget is not None
    expected = hashlib.sha1(b"coredmp95@gmail.com").hexdigest()[:12]
    # load() does not create the file until the first save — the PATH is
    # the keyed contract:
    assert budget.path.parent == token_session_no_env / "state"
    assert budget.path.name == f"budget-{expected}.json"


def test_gsync_apply_budget_line_survives_no_env(token_session_no_env):
    """Direct probe of the gsync crash expression, post-fix: the resolver
    feeds a real email from the stored session — no AttributeError path."""
    from pushframe.session import account_email
    from pushframe.cli import _build_write_budget
    # Exactly what gsync.py's apply path now runs:
    budget = _build_write_budget(account_email(), ignore_budget=False)
    assert budget is not None  # identity exists → pacing active, keyed

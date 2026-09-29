"""Offline tests for `pushframe status`'s Google section (plan 17-02 T1).

Zero network: a real GoogleSession over a MockTransport (fixture home page)
is injected via the `google_session` DI seam; the never-print-cookie
guarantee (LGS-03/D-02, T-17-05) is proven by grepping the output for
planted fake cookie values.
"""
from __future__ import annotations

import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pushframe.cli import _google_status_section, run_status  # noqa: E402
from pushframe.google.client import GoogleSession  # noqa: E402
from tests.offline import offline_aura  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"

COOKIES = [
    {"name": "SID", "value": "FAKE-SID-VALUE-absent-from-output",
     "domain": ".google.com", "path": "/"},
    {"name": "SAPISID", "value": "FAKE-SAPISID-VALUE-absent-from-output",
     "domain": ".google.com", "path": "/"},
]


def _linked_session() -> GoogleSession:
    html = (FIXTURES / "google_share_page_sample.html").read_text()
    return GoogleSession(COOKIES, transport=httpx.MockTransport(
        lambda request: httpx.Response(200, text=html)))


def _dead_session() -> GoogleSession:
    return GoogleSession(COOKIES, transport=httpx.MockTransport(
        lambda request: httpx.Response(200, text="<html>marketing</html>")))


def test_status_google_section_linked_shows_email(monkeypatch, capsys):
    monkeypatch.setenv('AURA_EMAIL', 'you@example.invalid')
    monkeypatch.setenv('AURA_PASSWORD', 'pw')
    rc = run_status(aura=offline_aura(), google_session=_linked_session())
    assert rc == 0
    out = capsys.readouterr().out
    assert 'Google:' in out
    assert 'linked: yes' in out
    assert 'account: synthetic.account@example.com' in out
    assert 'session: usable' in out


def test_status_google_section_dead_session(monkeypatch, capsys):
    monkeypatch.setenv('AURA_EMAIL', 'you@example.invalid')
    monkeypatch.setenv('AURA_PASSWORD', 'pw')
    rc = run_status(aura=offline_aura(), google_session=_dead_session())
    assert rc == 0
    out = capsys.readouterr().out
    assert 'linked: no' in out
    assert 'session: expired' in out


def test_status_google_section_never_prints_cookie_values(monkeypatch, capsys):
    """T-17-05: plant cookie values in the session; they must never appear
    in the output (LGS-03/D-02's never-print rule)."""
    monkeypatch.setenv('AURA_EMAIL', 'you@example.invalid')
    monkeypatch.setenv('AURA_PASSWORD', 'super-secret-pw')
    run_status(aura=offline_aura(), google_session=_linked_session())
    out = capsys.readouterr().out
    assert 'FAKE-SID-VALUE-absent-from-output' not in out
    assert 'FAKE-SAPISID-VALUE-absent-from-output' not in out
    assert 'super-secret-pw' not in out


def test_google_status_section_no_vault_is_linked_no(monkeypatch):
    """Vault absent -> `linked: no` with ZERO network calls (the vault load
    itself is the only probe; it fails fast)."""
    monkeypatch.setattr("pushframe.google.vault.DEFAULT_VAULT_PATH",
                        Path("/nonexistent/prod-vault.json"))
    monkeypatch.setattr("pushframe.google.vault.LEGACY_VAULT_PATH",
                        Path("/nonexistent/legacy-vault.json"))
    lines = _google_status_section(None)
    assert 'linked: no' in lines[1]
    assert len(lines) == 3  # header + linked: no + the google-link hint


def test_google_status_section_unreachable_session_degrades_honestly():
    class _Boom:
        def is_linked(self):
            raise httpx.ConnectError("network down")

    lines = _google_status_section(_Boom())
    assert any('unreachable' in l for l in lines), \
        "a failing session check must print an honest state, never a guess"

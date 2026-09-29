"""Offline tests for the GoogleSession client (plan 17-01 T1).

Zero network: every test runs through an injected httpx.MockTransport
(TEST-02) — proving the full-jar/UA requirements and the linked-check,
at-token and email extraction against the synthetic fixture.
"""
from __future__ import annotations

import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pushframe.google.client import GoogleSession, GoogleSessionError  # noqa: E402
from pushframe.google.parsers import parse_af_initdata  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"
SHARE_PAGE = FIXTURES / "google_share_page_sample.html"

COOKIES = [
    {"name": "SID", "value": "fake-sid", "domain": ".google.com", "path": "/"},
    {"name": "SAPISID", "value": "fake-sapisid", "domain": ".google.com", "path": "/"},
]


def _session(html: str, *, status: int = 200,
             cookies: list[dict] | None = COOKIES) -> GoogleSession:
    """A GoogleSession over a MockTransport answering one canned home page."""
    return GoogleSession(
        cookies if cookies is not None else [],
        transport=httpx.MockTransport(
            lambda request: httpx.Response(status, text=html)),
    )


def test_session_builds_full_jar_from_records():
    """The jar must carry domain+path (live-proven: a flattened dict reads as
    anonymous). Assert on the jar's internal store shape."""
    session = _session(SHARE_PAGE.read_text())
    jar = session.http.cookies
    names = set(jar.jar)
    cookie_by_name = {c.name: c for c in names}
    assert {"SID", "SAPISID"} <= set(cookie_by_name)
    for c in jar.jar:
        assert c.domain == ".google.com" and c.path == "/", \
            "full-jar records must preserve domain+path (live requirement)"


def test_is_linked_true_on_fixture_home_page():
    session = _session(SHARE_PAGE.read_text())
    assert session.is_linked() is True


def test_is_linked_false_on_anonymous_redirect():
    """A dead session: the marketing page carries no SNlM0e — the live-proven
    anonymous signal. is_linked() must return False, not raise."""
    anon = "<html><head><title>Google Photos</title></head><body>marketing</body></html>"
    session = _session(anon)
    assert session.is_linked() is False
    assert session.at_token() is None


def test_at_token_extraction():
    session = _session(SHARE_PAGE.read_text())
    assert session.at_token() == "SYNTH-AT-TOKEN-0001"


def test_account_email_extraction():
    session = _session(SHARE_PAGE.read_text())
    assert session.account_email() == "synthetic.account@example.com"


def test_home_http_error_fails_loud():
    """HTTP != 200 on the home page: home_text() raises (never a silent
    pass); is_linked() converts that into a clean False."""
    session = _session("gone", status=503)
    with pytest.raises(GoogleSessionError):
        session.home_text()
    assert session.is_linked() is False


def test_session_parses_share_page_through_same_transport():
    """The session's http client serves share pages too; the package parser
    walks them (the duality the enumerator relies on)."""
    session = _session(SHARE_PAGE.read_text())
    resp = session.http.get("https://photos.google.com/share/FAKE")
    assert resp.status_code == 200
    items = parse_af_initdata(resp.text)
    assert len(items) == 4


def test_authorization_header_sapisidhash_shape():
    session = _session(SHARE_PAGE.read_text())
    header = session.authorization_header()
    assert header is not None and header.startswith("SAPISIDHASH ")
    ms, digest = header.removeprefix("SAPISIDHASH ").split("_", 1)
    assert ms.isdigit() and len(digest) == 40  # sha1 hex


def test_authorization_header_none_without_sapisid():
    session = _session(SHARE_PAGE.read_text(), cookies=[COOKIES[0]])
    assert session.authorization_header() is None


def test_from_vault_reads_through_vault_boundary(tmp_path):
    """from_vault() routes through vault.load() — the denylist stays the
    enforcement point even for the package's own session builder."""
    vault_path = tmp_path / "vault.json"
    from pushframe.google import vault as google_vault
    google_vault.save(COOKIES, path=vault_path)
    session = GoogleSession.from_vault(path=vault_path)
    assert session.sapisid == "fake-sapisid"

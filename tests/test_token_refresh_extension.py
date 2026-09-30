"""One-shot TTY token refresh — extension to every verb (venus 2026-09-30).

status got the refresh first; this file proves the SAME shared gate
(session.frames_read_with_refresh + refresh_if_auto) serves inspect,
sync, push (the same run_sync handler as sync) and google-sync: a dead
stored token costs ONE re-login prompt and the run continues — never a
second attempt, never a prompt off-TTY.
"""
import json

import httpx
import pytest

from pushframe import cli
from tests.offline import offline_aura, FIXTURES_DIR

TRIP_BODY = {"error": True, "message": "Request Unauthenticated", "logout": True}


@pytest.fixture
def stored_session(tmp_path, monkeypatch):
    from pushframe.utils import settings
    monkeypatch.setattr(settings, "CONFIG_PATH", tmp_path / "config.json")
    from pushframe import config_store
    config_store.update(email="vaulted@example.invalid", auth_token="tok-dead",
                        user_id="u-1")
    return config_store


@pytest.fixture
def tty_refresh_env(monkeypatch):
    """Stored-session posture: no env creds (they would disable the
    auto-refresh), a TTY, and the wizard login seam counting its calls."""
    for var in ("PUSHFRAME_EMAIL", "AURA_EMAIL", "PUSHFRAME_PASSWORD", "AURA_PASSWORD"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)

    logins = []
    import getpass as gp
    monkeypatch.setattr(gp, "getpass", lambda *a: "typed-pw")  # noqa: S105
    monkeypatch.setattr("builtins.input", lambda *a: "vaulted@example.invalid")

    def fake_wizard_login(email, password):
        logins.append((email, password))
        return {"email": email, "auth_token": "fresh-tok",  # noqa: S105
                "user_id": "u-1", "frames": []}

    monkeypatch.setattr("pushframe.cli._wizard_login", fake_wizard_login)
    return logins


def _frames_401_once_route(state):
    """frames.json: the trip-shaped 401 exactly once, then the fixture."""
    fixture = (FIXTURES_DIR / "frames.json").read_text()

    def route(request):
        state["calls"] += 1
        if state["calls"] == 1:
            return httpx.Response(401, json=TRIP_BODY)
        return httpx.Response(200, content=fixture)

    return route


def test_inspect_refreshes_dead_token_once_and_lists(monkeypatch, capsys,
                                                     stored_session, tty_refresh_env):
    state = {"calls": 0}
    aura = offline_aura(overrides={"/v5/frames.json": _frames_401_once_route(state)})

    rc = cli.run_inspect("Fake", aura=aura)

    assert rc == 0
    out = capsys.readouterr().out
    assert "Fake Frame" in out
    assert tty_refresh_env == [("vaulted@example.invalid", "typed-pw")]  # noqa: S105
    assert state["calls"] == 2  # refused once, re-read once
    assert stored_session.load()["auth_token"] == "fresh-tok"  # noqa: S105


@pytest.mark.parametrize("verb", ["sync", "push"])
def test_sync_and_push_refresh_dead_token_once(monkeypatch, capsys, tmp_path,
                                               stored_session, tty_refresh_env, verb):
    state = {"calls": 0}
    aura = offline_aura(overrides={"/v5/frames.json": _frames_401_once_route(state)})
    (tmp_path / "new.jpg").write_bytes(b"new-photo-bytes")

    # Dry run: the frames read is the only authenticated call either verb
    # needs before the plan prints — the refresh contract is fully proven
    # without touching the write seams (covered by test_cli_apply).
    rc = cli.run_sync(str(tmp_path), "Fake", aura=aura, verb=verb)

    assert rc == 0
    out = capsys.readouterr().out
    assert "To upload: 1" in out
    assert tty_refresh_env == [("vaulted@example.invalid", "typed-pw")]  # noqa: S105
    assert state["calls"] == 2
    assert stored_session.load()["auth_token"] == "fresh-tok"  # noqa: S105


def test_google_sync_refreshes_dead_token_once_and_applies(monkeypatch, capsys,
                                                           tmp_path, stored_session,
                                                           tty_refresh_env):
    from pushframe.gsync import run_google_sync

    # Google-side harness copied from test_gsync_execute's apply test (the
    # canonical shape: listing 1-2-3, frame already holds photo 1, so the
    # apply uploads exactly photos 2+3).
    import tests.test_gsync_execute as g
    router = g._GoogleRouter(item_bodies={2: g._jpeg(2), 3: g._jpeg(3)})
    aura = g._FakeAura(g._assets_for(1))

    # The dead-token refusal: get_frames 401s once, then the real fake.
    state = {"calls": 0}

    class _Wrap:
        def __init__(self, inner):
            self._inner = inner

        def get_frames(self):
            state["calls"] += 1
            if state["calls"] == 1:
                req = httpx.Request("GET", "https://api.pushd.com/v5/frames.json")
                resp = httpx.Response(401, json=TRIP_BODY, request=req)
                raise httpx.HTTPStatusError(
                    '401 Unauthorized for https://api.pushd.com/v5/frames.json '
                    '— server body: {"error": true, "message": "Request '
                    'Unauthenticated", "logout": true}',
                    request=req, response=resp)
            return self._inner.get_frames()

        def __getattr__(self, name):
            return getattr(self._inner, name)

    aura.frame_api = _Wrap(aura.frame_api)

    # The fake's zero-arg login() must accept the wizard's email=/password=
    # kwargs (the real Aura.login does) or the one-shot refresh dies on a
    # TypeError and the run fails classified-401 instead of re-reading.
    from types import MethodType

    def _fake_login(self, email=None, password=None):
        self.login_called = True

    aura.login = MethodType(_fake_login, aura)

    rc = run_google_sync("Cadre", "Fabrice", apply=True, yes=True,
                         session=g._google_session(router), aura=aura,
                         s3_client=g._FakeS3(), sqs_client=g._FakeSQS(),
                         cache_dir=tmp_path / "cache",
                         manifest_path=tmp_path / "manifest.json")

    assert rc == 0
    out = capsys.readouterr().out
    assert "Applied: 2 uploaded" in out
    assert tty_refresh_env == [("vaulted@example.invalid", "typed-pw")]  # noqa: S105
    assert state["calls"] == 2
    assert stored_session.load()["auth_token"] == "fresh-tok"  # noqa: S105

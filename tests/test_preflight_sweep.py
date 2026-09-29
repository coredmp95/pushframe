"""Phase 24 (PRF-02): the traceback-free sweep.

Every documented foreseeable failure mode maps to a named, one-screen
error WITH the next action — parametrized over the inventory that lives in
.planning/phases/24-.../24-CONTEXT.md (§Failure modes). A new mode = one
inventory entry + one parametrize case, nowhere else.

The contract per case: handler returns non-zero, captured output contains
the named marker AND the remedy substring, and NO 'Traceback' anywhere.
"""
import json

import httpx
import pytest

from pushframe.utils import settings


# --- the inventory (mirrors 24-CONTEXT §Failure modes) -----------------------

CASES = [
    # (id, callable-spec, kwargs, marker, remedy)
    pytest.param(
        "gsync_no_vault", {},
        "no Google session vault", "google-link",
        id="mode-5-vault-missing"),
    pytest.param(
        "sync_dir", {"dir": "/definitely/not/here-xyz"},
        "does not exist", "photos",
        id="mode-7-dir-missing"),
    pytest.param(
        "inspect_no_creds", {},
        "not authenticated", "pushframe config",
        id="mode-8-inspect-no-creds"),
    pytest.param(
        "gsync_no_creds", {"album": "Cadre", "frame": "Fabrice"},
        "google-sync failed", "google-link",
        id="mode-8-gsync-no-creds"),
    pytest.param(
        "sync_dir_no_creds", {"dir": "."},
        "not authenticated", "pushframe config",
        id="mode-8-sync-no-creds"),
]


# --- fixture helpers ---------------------------------------------------------

@pytest.fixture
def no_creds(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "CONFIG_PATH", tmp_path / "config.json")
    for var in ("PUSHFRAME_EMAIL", "AURA_EMAIL",
                "PUSHFRAME_PASSWORD", "AURA_PASSWORD"):
        monkeypatch.delenv(var, raising=False)


# --- callables ---------------------------------------------------------------

def gsync_no_vault(capsys, no_creds):
    from pushframe.gsync import run_google_sync
    return run_google_sync("Album", "Frame", apply=False, yes=True,
                           is_interactive=False)


class _AuthedAura:
    """Injected (DI contract: call sites never re-authenticate) — enough to
    reach the source-dir preflight, which fails before any network call."""

    def login(self, email=None, password=None):
        return self

    def resume_session(self, email=None, auth_token=None, user_id=None):
        return self


def sync_dir(capsys, no_creds, dir):
    from pushframe.cli import run_sync
    return run_sync(dir, "Fake", aura=_AuthedAura())


def sync_dir_no_creds(capsys, no_creds, dir):
    from pushframe.cli import run_sync
    return run_sync(dir, "Fake")


def inspect_no_creds(capsys, no_creds):
    from pushframe.cli import run_inspect
    return run_inspect("whatever")


def gsync_no_creds(capsys, no_creds, album, frame):
    # vault exists (mode 5 covered separately) — the Aura session then fails
    # named because no credentials of any kind are available.
    vault = settings.CONFIG_PATH.parent / "google-cookies.json"
    vault.parent.mkdir(parents=True, exist_ok=True)
    vault.write_text(json.dumps({"SID": "x"}))  # minimal-looking vault
    from pushframe.gsync import run_google_sync
    return run_google_sync(album, frame, apply=False, yes=True,
                           is_interactive=False)


class _MuteAura:
    """Aura stub good enough to reach the dir preflight (which fails first)."""


# --- the sweep ---------------------------------------------------------------

@pytest.mark.parametrize("spec,kwargs,marker,remedy", CASES)
def test_no_traceback_named_error_with_remedy(capsys, no_creds, spec, kwargs,
                                              marker, remedy):
    rc = globals()[spec](capsys, no_creds, **kwargs)
    captured = capsys.readouterr()
    both = captured.out + captured.err
    assert rc != 0, f"{spec}: expected failure"
    assert marker in both, f"{spec}: missing named marker {marker!r}"
    assert remedy in both, f"{spec}: missing remedy {remedy!r}"
    assert "Traceback" not in both, f"{spec}: RAW TRACEBACK LEAKED"

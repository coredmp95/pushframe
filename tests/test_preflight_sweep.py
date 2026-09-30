"""Phase 24 (PRF-02): the traceback-free sweep.

Every documented foreseeable failure mode maps to a named, one-screen
error WITH the next action — parametrized over the inventory that lives in
.planning/phases/24-.../24-CONTEXT.md (§Failure modes). A new mode = one
inventory entry + one parametrize case, nowhere else.

The contract per case: handler returns non-zero, captured output contains
the named marker AND the remedy substring, and NO 'Traceback' anywhere.

2026-09-30 (venus live + CI): there is deliberately NO `gsync_no_creds`
case — the Google gate stands BEFORE the Aura gate by design, so a vault-less
gsync run fails Google-side named long before any Aura credential check,
and a form-valid fake vault pushes the run past the gate onto the REAL
Google network with a bogus cookie (the 5.1.2 network guard covers pushd,
not google.com). The Aura-side no-creds contract is proven by
`sync_dir_no_creds` / `inspect_no_creds` (same establish_session surface);
gsync's Google-side named shapes by `gsync_no_vault`, `pair_unknown`,
`all_zero_pairs`.
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
        "sync_dir_no_creds", {"dir": "."},
        "not authenticated", "pushframe config",
        id="mode-8-sync-no-creds"),
    # --- phase 25 additions (25-CONTEXT §Failure modes) ---
    pytest.param(
        "pair_unknown", {"pair": "ghost"},
        'unknown pair "ghost"', "Known pairs",
        id="mode-13-pair-unknown"),
    pytest.param(
        "all_zero_pairs", {},
        "no pairs configured", "config pair add",
        id="mode-14-all-zero-pairs"),
    pytest.param(
        "schedule_no_systemd", {},
        "systemd", "enable-linger",
        id="mode-15-no-systemd-session"),
    pytest.param(
        "pair_duplicate", {"name": "dup", "album": "A", "frame": "F"},
        "already exists", "remove it first",
        id="mode-16-pair-duplicate"),
]


# --- fixture helpers ---------------------------------------------------------

@pytest.fixture
def no_creds(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "CONFIG_PATH", tmp_path / "config.json")
    for var in ("PUSHFRAME_EMAIL", "AURA_EMAIL",
                "PUSHFRAME_PASSWORD", "AURA_PASSWORD"):
        monkeypatch.delenv(var, raising=False)
    # The vault preflight is pinned AWAY from the real machine's vault: after
    # the 2026-09-30 path fix this suite would otherwise depend on whether
    # the dev box happens to hold a healthy vault (venus live-proven bug).
    monkeypatch.setenv("PUSHFRAME_VAULT_PATH", str(tmp_path / "no-vault.json"))


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


class _MuteAura:
    """Aura stub good enough to reach the dir preflight (which fails first)."""


# --- phase 25 callables ------------------------------------------------------

def pair_unknown(capsys, no_creds, pair):
    from pushframe.gsync import run_google_sync
    return run_google_sync("Album", "Frame", apply=False, pair=pair,
                           session=_MuteSession(), aura=_MuteAura())


def all_zero_pairs(capsys, no_creds):
    from pushframe.gsync import run_google_sync
    return run_google_sync("Album", "--all", apply=False, run_all=True,
                           session=_MuteSession(), aura=_MuteAura())


def schedule_no_systemd(capsys, no_creds):
    import pytest as _pytest
    from pushframe import schedule as sch
    mp = _pytest.MonkeyPatch()
    mp.setattr(sch, "UNIT_DIR", no_creds_parent / "units")
    mp.setattr(sch, "systemd_user_session_ok", lambda: False)
    try:
        return sch.schedule_add("nightly", pair="whatever", every="1d")
    finally:
        mp.undo()


def pair_duplicate(capsys, no_creds, name, album, frame):
    from pushframe import pairs as pairs_mod
    pairs_mod.pair_add(name, album=album, frame=frame)
    rc = 0
    try:
        pairs_mod.pair_add(name, album=album, frame=frame)
    except Exception as e:
        print(f'pair not added: {e}')
        rc = 1
    return rc


def _MuteSession():
    return object()


import pathlib
no_creds_parent = pathlib.Path("/tmp")


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

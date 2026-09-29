"""Offline tests for `pushframe inspect` (pushframe.cli.run_inspect /
resolve_frame). Calls run_inspect() directly (never main()) so
load_dotenv() is not invoked and a filesystem .env cannot interfere.

Unmarked (no @pytest.mark.live) — this is the default pytest suite, runs
with zero network access and no real credentials.
"""
import copy
import json

import httpx
import pytest
from loguru import logger

from pushframe.cli import run_inspect
from tests.offline import FIXTURES_DIR, offline_aura


@pytest.fixture(autouse=True)
def _reset_loguru():
    # loguru's `logger` is a process-global singleton and Aura._init_logger()
    # accumulates sinks across constructions; reset around each test so the
    # stderr assertions below are deterministic and a sink bound to a
    # torn-down capsys buffer from a prior test can't fire in a later one.
    logger.remove()
    yield
    logger.remove()


def _single_frame():
    """Copy of frames.json's single Frame payload -- mutate id/name on the
    copy per test, never edit the shared fixture file (Pitfall 2)."""
    data = json.loads((FIXTURES_DIR / "frames.json").read_text())
    return copy.deepcopy(data["frames"][0])


def _frames_response(*frames):
    return httpx.Response(200, json={"frames": list(frames)})


def test_inspect_success_unique_name_match(monkeypatch, capsys):
    monkeypatch.setenv('AURA_EMAIL', 'you@example.invalid')
    monkeypatch.setenv('AURA_PASSWORD', 'super-secret-pw')

    rc = run_inspect('Fake', aura=offline_aura())

    assert rc == 0
    out = capsys.readouterr().out
    assert 'Fake Frame' in out
    assert 'Fake Tester' in out
    assert 'fake-user@example.invalid' in out
    assert 'Fake Contributor' in out
    assert 'Assets: 3' in out
    assert 'asset-fake-001' in out
    assert 'fake-asset-001.jpg' in out
    assert 'super-secret-pw' not in out


def test_inspect_id_fallback_when_no_name_matches(monkeypatch, capsys):
    monkeypatch.setenv('AURA_EMAIL', 'you@example.invalid')
    monkeypatch.setenv('AURA_PASSWORD', 'super-secret-pw')

    living_room = _single_frame()
    living_room['id'] = 'frame-fake-living-room'
    living_room['name'] = 'Living Room'
    bedroom = _single_frame()
    bedroom['id'] = 'frame-fake-bedroom'
    bedroom['name'] = 'Bedroom'

    # The default frame_detail.json route is a static fixture keyed only on
    # path shape, not on the requested frame id -- override the exact detail
    # path for 'frame-fake-bedroom' so this test proves both resolve_frame's
    # id fallback AND that get_frame() was called with the *resolved* id
    # (only that specific path override would return 'Bedroom' metadata).
    bedroom_detail = {
        'frame': copy.deepcopy(bedroom),
        'total_asset_count': 3,
    }
    aura = offline_aura(overrides={
        '/v5/frames.json': _frames_response(living_room, bedroom),
        '/v5/frames/frame-fake-bedroom.json': httpx.Response(200, json=bedroom_detail),
    })

    rc = run_inspect('frame-fake-bedroom', aura=aura)

    assert rc == 0
    out = capsys.readouterr().out
    assert 'Bedroom' in out
    assert 'Living Room' not in out


def test_inspect_ambiguous_name_lists_names_and_ids(monkeypatch, capsys):
    monkeypatch.setenv('AURA_EMAIL', 'you@example.invalid')
    monkeypatch.setenv('AURA_PASSWORD', 'super-secret-pw')

    kitchen = _single_frame()
    kitchen['id'] = 'frame-fake-kitchen'
    kitchen['name'] = 'Kitchen'
    kitchen_upstairs = _single_frame()
    kitchen_upstairs['id'] = 'frame-fake-kitchen-2'
    kitchen_upstairs['name'] = 'Kitchen 2 Upstairs'

    aura = offline_aura(overrides={
        '/v5/frames.json': _frames_response(kitchen, kitchen_upstairs),
    })

    rc = run_inspect('kitchen', aura=aura)

    assert rc == 1
    out = capsys.readouterr().out
    assert 'Kitchen' in out
    assert 'Kitchen 2 Upstairs' in out
    assert 'frame-fake-kitchen' in out
    assert 'frame-fake-kitchen-2' in out
    # The id-fallback must NOT be attempted on an ambiguous name match --
    # no metadata/photo block should print.
    assert 'Owner:' not in out
    assert 'Photos' not in out


def test_inspect_not_found_lists_available_frame_names(monkeypatch, capsys):
    monkeypatch.setenv('AURA_EMAIL', 'you@example.invalid')
    monkeypatch.setenv('AURA_PASSWORD', 'super-secret-pw')

    rc = run_inspect('does-not-exist-xyz', aura=offline_aura())

    assert rc == 1
    out = capsys.readouterr().out
    assert 'Fake Frame' in out


def test_inspect_tolerates_unprocessed_placeholder_asset(monkeypatch, capsys):
    """Regression: `inspect` must not crash when the frame's asset list
    contains a mid-server-side-processing placeholder (null data_uti/file_name/
    taken_at/dimensions, `good_resolution` absent). Before the model fix this
    raised 8 pydantic ValidationErrors inside get_all_assets and the CLI printed
    'Failed to inspect frame' with rc=1. See
    .planning/debug/resolved/inspect-asset-null-fields.md.
    """
    monkeypatch.setenv('AURA_EMAIL', 'you@example.invalid')
    monkeypatch.setenv('AURA_PASSWORD', 'super-secret-pw')

    unprocessed = json.loads((FIXTURES_DIR / "asset_unprocessed.json").read_text())
    unprocessed.pop("_comment", None)
    # A single page (next_page_cursor null) so get_all_assets stops after it.
    assets_response = httpx.Response(200, json={
        "assets": [unprocessed],
        "next_page_cursor": None,
    })
    aura = offline_aura(overrides={
        '/v5/frames/frame-fake-0001/assets.json': assets_response,
    })

    rc = run_inspect('Fake', aura=aura)

    assert rc == 0, capsys.readouterr().out
    out = capsys.readouterr().out
    assert 'Failed to inspect frame' not in out
    assert 'asset-unprocessed-0001' in out
    # taken_at is null -> taken_at_dt is None, printed as the literal 'None'
    # rather than crashing on parse_aura_dt(None).
    assert 'None' in out


def test_inspect_no_credentials_exits_nonzero_named(tmp_path, monkeypatch, capsys):
    """Phase 24: with no aura injected and no credentials anywhere, inspect
    fails NAMED (remedy) — the env-override login-failure shape is owned by
    establish_session (test_session.py) since the call sites never
    re-authenticate an injected aura (DI contract)."""
    from pushframe.utils import settings
    monkeypatch.setattr(settings, 'CONFIG_PATH', tmp_path / 'config.json')
    for var in ('AURA_EMAIL', 'AURA_PASSWORD', 'PUSHFRAME_EMAIL', 'PUSHFRAME_PASSWORD'):
        monkeypatch.delenv(var, raising=False)

    rc = run_inspect('Fake')

    assert rc == 1
    out = capsys.readouterr().out
    assert 'not authenticated' in out
    assert 'pushframe config' in out


def test_inspect_quiet_by_default_suppresses_verbose_stderr(monkeypatch, capsys):
    monkeypatch.setenv('AURA_EMAIL', 'you@example.invalid')
    monkeypatch.setenv('AURA_PASSWORD', 'super-secret-pw')

    rc = run_inspect('Fake', aura=offline_aura())

    assert rc == 0
    captured = capsys.readouterr()
    assert 'Fake Frame' in captured.out
    # The two loguru markers Client.get/post write for every HTTP call must
    # not reach stderr by default (this is the RED assertion pre-fix).
    assert 'request to' not in captured.err
    assert 'Response (' not in captured.err

"""Offline tests for `pushframe sync` (pushframe.cli.run_sync). Calls
run_sync() directly (never main()) so load_dotenv() is not invoked and a
filesystem .env cannot interfere.

Unmarked (no @pytest.mark.live) — this is the default pytest suite, runs
with zero network access and no real credentials.
"""
import copy
import json

import httpx
import pytest
from loguru import logger

from pushframe.aws.s3client import get_md5
from pushframe import cli
from pushframe.cli import run_sync
from tests.offline import FIXTURES_DIR, offline_aura

FRAME_ID = 'frame-fake-0001'
ASSETS_PATH = f'/v5/frames/{FRAME_ID}/assets.json'


@pytest.fixture(autouse=True)
def _reset_loguru(tmp_path_factory, monkeypatch):
    # loguru's `logger` is a process-global singleton and Aura._init_logger()
    # accumulates sinks across constructions; reset around each test so a
    # sink bound to a torn-down capsys buffer from a prior test can't fire.
    #
    # WR-04: `_configure_cli_logging` (debug=False, the default every test
    # here uses) calls `os.makedirs('logs/', exist_ok=True)` and writes to
    # `logs/file_{time}.log` relative to the process cwd -- not `tmp_path`.
    # chdir into a dedicated scratch dir (NOT the test's own `tmp_path`,
    # which several tests pass directly as `dir_arg` to `run_sync` -- a
    # `logs/` dir created inside it would be picked up by `scan_directory`
    # and inflate `skipped_non_image`) so this side effect lands in an
    # isolated sandbox instead of the real project working tree.
    monkeypatch.chdir(tmp_path_factory.mktemp('cli-logging-cwd'))
    logger.remove()
    yield
    logger.remove()


def _asset_payload(**overrides):
    """Copy of assets_page1.json's single Asset payload -- mutate fields on
    the copy per test, never edit the shared fixture file."""
    data = json.loads((FIXTURES_DIR / "assets_page1.json").read_text())
    asset = copy.deepcopy(data["assets"][0])
    asset.update(overrides)
    return asset


def _assets_response(*assets):
    return httpx.Response(200, json={'assets': list(assets), 'next_page_cursor': None})


def _env(monkeypatch):
    monkeypatch.setenv('AURA_EMAIL', 'you@example.invalid')
    monkeypatch.setenv('AURA_PASSWORD', 'super-secret-pw')


def test_sync_classifies_upload_and_unchanged_by_hash(tmp_path, monkeypatch, capsys):
    _env(monkeypatch)

    unchanged_bytes = b'unchanged-photo-bytes'
    upload_bytes = b'upload-photo-bytes'
    (tmp_path / 'existing.jpg').write_bytes(unchanged_bytes)
    (tmp_path / 'new.jpg').write_bytes(upload_bytes)

    frame_asset = _asset_payload(id='asset-existing', md5_hash=get_md5(unchanged_bytes))
    aura = offline_aura(overrides={ASSETS_PATH: _assets_response(frame_asset)})

    rc = run_sync(str(tmp_path), 'Fake', aura=aura)

    assert rc == 0
    out = capsys.readouterr().out
    assert 'To upload: 1' in out
    assert 'To hide: 0' in out
    assert 'Unchanged: 1' in out
    assert 'new.jpg' in out
    assert 'existing.jpg' not in out


def test_sync_delete_candidate_shows_id_and_date_no_filename(tmp_path, monkeypatch, capsys):
    _env(monkeypatch)

    frame_asset = _asset_payload(
        id='asset-to-delete',
        md5_hash=get_md5(b'no-local-match-bytes'),
        taken_at='2024-03-11T12:00:00.000Z',
        file_name='secret-name-should-not-appear.jpg',
    )
    aura = offline_aura(overrides={ASSETS_PATH: _assets_response(frame_asset)})

    rc = run_sync(str(tmp_path), 'Fake', aura=aura)

    assert rc == 0
    out = capsys.readouterr().out
    assert 'To hide: 1' in out
    assert 'asset-to-delete' in out
    assert '2024-03-11' in out
    assert 'secret-name-should-not-appear.jpg' not in out


def test_sync_lists_full_upload_and_delete_without_truncation(tmp_path, monkeypatch, capsys):
    _env(monkeypatch)

    (tmp_path / 'first.jpg').write_bytes(b'first-upload-bytes')
    (tmp_path / 'second.jpg').write_bytes(b'second-upload-bytes')

    aura = offline_aura(overrides={ASSETS_PATH: _assets_response()})

    rc = run_sync(str(tmp_path), 'Fake', aura=aura)

    assert rc == 0
    out = capsys.readouterr().out
    assert 'To upload: 2' in out
    assert 'first.jpg' in out
    assert 'second.jpg' in out


def test_sync_skips_non_image_files_with_summary_note(tmp_path, monkeypatch, capsys):
    _env(monkeypatch)

    (tmp_path / 'notes.txt').write_bytes(b'not-a-photo')

    aura = offline_aura(overrides={ASSETS_PATH: _assets_response()})

    rc = run_sync(str(tmp_path), 'Fake', aura=aura)

    assert rc == 0
    out = capsys.readouterr().out
    assert 'To upload: 0' in out
    assert '1 non-photo files skipped' in out
    assert 'notes.txt' not in out


def test_sync_hashless_frame_asset_not_deleted(tmp_path, monkeypatch, capsys):
    _env(monkeypatch)

    video_asset = _asset_payload(id='asset-video-no-hash', md5_hash=None)
    aura = offline_aura(overrides={ASSETS_PATH: _assets_response(video_asset)})

    rc = run_sync(str(tmp_path), 'Fake', aura=aura)

    assert rc == 0
    out = capsys.readouterr().out
    assert 'To hide: 0' in out
    assert 'asset-video-no-hash' not in out
    assert '1 frame assets without a content hash' in out


def test_sync_ambiguous_name_returns_1(tmp_path, monkeypatch, capsys):
    _env(monkeypatch)

    data = json.loads((FIXTURES_DIR / "frames.json").read_text())
    kitchen = copy.deepcopy(data["frames"][0])
    kitchen['id'] = 'frame-fake-kitchen'
    kitchen['name'] = 'Kitchen'
    kitchen_upstairs = copy.deepcopy(data["frames"][0])
    kitchen_upstairs['id'] = 'frame-fake-kitchen-2'
    kitchen_upstairs['name'] = 'Kitchen 2 Upstairs'

    aura = offline_aura(overrides={
        '/v5/frames.json': httpx.Response(200, json={'frames': [kitchen, kitchen_upstairs]}),
    })

    rc = run_sync(str(tmp_path), 'kitchen', aura=aura)

    assert rc == 1
    out = capsys.readouterr().out
    assert 'Kitchen' in out
    assert 'Kitchen 2 Upstairs' in out
    assert 'To upload' not in out


def test_sync_not_found_returns_1(tmp_path, monkeypatch, capsys):
    _env(monkeypatch)

    rc = run_sync(str(tmp_path), 'does-not-exist-xyz', aura=offline_aura())

    assert rc == 1
    out = capsys.readouterr().out
    assert 'Fake Frame' in out
    assert 'To upload' not in out


def test_sync_no_credentials_returns_1_named(tmp_path, monkeypatch, capsys):
    """Phase 24: no aura injected + no credentials anywhere = named failure
    with the remedy (DI contract: call sites never re-authenticate an
    injected aura; the source-dir preflight precedes any network anyway)."""
    from pushframe.utils import settings
    monkeypatch.setattr(settings, 'CONFIG_PATH', tmp_path / 'config.json')
    for var in ('AURA_EMAIL', 'AURA_PASSWORD', 'PUSHFRAME_EMAIL', 'PUSHFRAME_PASSWORD'):
        monkeypatch.delenv(var, raising=False)

    rc = run_sync(str(tmp_path), 'Fake')

    assert rc == 1
    out = capsys.readouterr().out
    assert 'not authenticated' in out
    assert 'pushframe config' in out


# --- removal-mode flags (HIDE-05, D-02/D-03) --------------------------------

def test_delete_and_hard_delete_are_mutually_exclusive():
    """Passing both would make the run's destructiveness ambiguous, so
    argparse rejects it at parse time (V5) rather than picking a winner."""
    parser = cli.build_parser()

    with pytest.raises(SystemExit):
        parser.parse_args(['sync', '.', '--frame', 'Fake', '--delete', '--hard-delete'])


def test_sync_defaults_to_hide_with_neither_flag():
    args = cli.build_parser().parse_args(['sync', '.', '--frame', 'Fake'])

    assert args.delete is False
    assert args.hard_delete is False


def test_push_has_no_removal_flags():
    """push is upload-only, so the removal tiers are not offered there."""
    parser = cli.build_parser()

    with pytest.raises(SystemExit):
        parser.parse_args(['push', '.', '--frame', 'Fake', '--delete'])

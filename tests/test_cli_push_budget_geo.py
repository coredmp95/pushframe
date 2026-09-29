"""Offline tests for the Phase 09 (proactive-write-rate-limiter-geo-guard)
CLI wiring (Plan 09-02, ANTI-06): the four `push`-only override flags
(`--max-wait`/`--no-wait`/`--country`/`--ignore-budget`), `run_sync`'s
`budget`/`geo_check` construction for BOTH verbs by default, and the two new
`GeoMismatchError`/`BudgetExhausted` exception branches.

Extends `tests/test_cli_apply.py`'s `_patch_execute_plan` pattern -- calls
`run_sync()` directly (never `main()`), monkeypatches `execute_plan` on the
`pushframe.cli` namespace, and never touches the network, AWS, or a real
`~/.config` path (`AURA_STATE_DIR` is monkeypatched to `tmp_path`).
"""
import json
from pathlib import Path

import pytest
from loguru import logger

import pushframe.cli as cli
from pushframe.ratelimit import BudgetExhausted, GeoMismatchError, WriteBudget
from pushframe.sync import ExecutionResult
from tests.offline import offline_aura

FRAME_ID = 'frame-fake-0001'
FRAME_NAME = 'Fake Frame'
ASSETS_PATH = f'/v5/frames/{FRAME_ID}/assets.json'
EMAIL = 'you@example.invalid'


@pytest.fixture(autouse=True)
def _reset_loguru(tmp_path_factory, monkeypatch):
    monkeypatch.chdir(tmp_path_factory.mktemp('cli-push-budget-geo-cwd'))
    logger.remove()
    yield
    logger.remove()


@pytest.fixture(autouse=True)
def _state_dir(tmp_path, monkeypatch):
    # Never let a test's WriteBudget.load()/save() touch a real
    # ~/.config/pushframe path.
    monkeypatch.setattr(cli, 'AURA_STATE_DIR', tmp_path)
    return tmp_path


def _env(monkeypatch):
    monkeypatch.setenv('AURA_EMAIL', EMAIL)
    monkeypatch.setenv('AURA_PASSWORD', 'super-secret-pw')


import httpx


def _assets_response(*assets):
    return httpx.Response(200, json={'assets': list(assets), 'next_page_cursor': None})


class _FakeS3Client:
    def __init__(self, *args, **kwargs):
        pass


class _FakeSQSClient:
    def __init__(self, *args, **kwargs):
        pass


def _patch_aws_clients(monkeypatch):
    monkeypatch.setattr(cli, 'S3Client', _FakeS3Client)
    monkeypatch.setattr(cli, 'SQSClient', _FakeSQSClient)


def _patch_execute_plan(monkeypatch, result=None, raises=None):
    """Extends test_cli_apply.py's _patch_execute_plan to also record the
    new budget/geo_check/wait_on_budget/max_wait_seconds kwargs (or raise a
    given exception instead of returning)."""
    calls = []

    def fake_execute_plan(plan, aura, frame_id, *, s3_client, sqs_client, progress=None, on_wait=None, on_error=None,
                          batch_size=None, chunk_delay_seconds=None, budget=None, geo_check=None,
                          wait_on_budget=None, max_wait_seconds=None, removal_mode=None):
        calls.append({
            'plan': plan,
            'frame_id': frame_id,
            'batch_size': batch_size,
            'chunk_delay_seconds': chunk_delay_seconds,
            'budget': budget,
            'geo_check': geo_check,
            'wait_on_budget': wait_on_budget,
            'max_wait_seconds': max_wait_seconds,
        })
        if raises is not None:
            raise raises
        return result if result is not None else ExecutionResult()

    monkeypatch.setattr(cli, 'execute_plan', fake_execute_plan)
    return calls


def _new_photo(tmp_path):
    (tmp_path / 'new.jpg').write_bytes(b'new-photo-bytes')


def test_push_apply_forwards_budget_and_geo_check_by_default(tmp_path, monkeypatch):
    # AURA_COUNTRY/AURA_GEO_FAIL_OPEN are read once at settings-module import
    # time and bound into cli.py's namespace (`from ... import AURA_COUNTRY`)
    # -- monkeypatch.setenv alone doesn't affect an already-bound name, so
    # tests patch the cli-module binding directly (mirrors how settings.py's
    # own reload-based tests handle the same import-time-read convention).
    _env(monkeypatch)
    monkeypatch.setattr(cli, 'AURA_COUNTRY', 'FR')
    _patch_aws_clients(monkeypatch)
    calls = _patch_execute_plan(monkeypatch)

    _new_photo(tmp_path)
    aura = offline_aura(overrides={ASSETS_PATH: _assets_response()})

    rc = cli.run_sync(str(tmp_path), 'Fake', apply=True, yes=True, aura=aura, verb='push')

    assert rc == 0
    assert len(calls) == 1
    assert isinstance(calls[0]['budget'], WriteBudget)
    assert calls[0]['geo_check'] is not None
    # Override kwargs not supplied -> not forwarded (execute_plan keeps its
    # own wait_on_budget=True / max_wait_seconds=3600.0 defaults).
    assert calls[0]['wait_on_budget'] is None
    assert calls[0]['max_wait_seconds'] is None


def test_push_apply_ignore_budget_forwards_no_budget_kwarg(tmp_path, monkeypatch):
    _env(monkeypatch)
    monkeypatch.setattr(cli, 'AURA_COUNTRY', 'FR')
    _patch_aws_clients(monkeypatch)
    calls = _patch_execute_plan(monkeypatch)

    _new_photo(tmp_path)
    aura = offline_aura(overrides={ASSETS_PATH: _assets_response()})

    rc = cli.run_sync(str(tmp_path), 'Fake', apply=True, yes=True, aura=aura,
                      verb='push', ignore_budget=True)

    assert rc == 0
    assert calls[0]['budget'] is None
    # geo_check is independent of --ignore-budget -- still built from AURA_COUNTRY.
    assert calls[0]['geo_check'] is not None


def test_push_no_wait_forwards_wait_on_budget_false(tmp_path, monkeypatch):
    _env(monkeypatch)
    _patch_aws_clients(monkeypatch)
    calls = _patch_execute_plan(monkeypatch)

    _new_photo(tmp_path)
    aura = offline_aura(overrides={ASSETS_PATH: _assets_response()})

    cli.run_sync(str(tmp_path), 'Fake', apply=True, yes=True, aura=aura,
                 verb='push', no_wait=True)

    assert calls[0]['wait_on_budget'] is False


def test_push_max_wait_forwards_max_wait_seconds(tmp_path, monkeypatch):
    _env(monkeypatch)
    _patch_aws_clients(monkeypatch)
    calls = _patch_execute_plan(monkeypatch)

    _new_photo(tmp_path)
    aura = offline_aura(overrides={ASSETS_PATH: _assets_response()})

    cli.run_sync(str(tmp_path), 'Fake', apply=True, yes=True, aura=aura,
                 verb='push', max_wait=120)

    assert calls[0]['max_wait_seconds'] == 120


def test_push_country_flag_builds_geo_check(tmp_path, monkeypatch):
    _env(monkeypatch)
    monkeypatch.setattr(cli, 'AURA_COUNTRY', None)
    _patch_aws_clients(monkeypatch)
    calls = _patch_execute_plan(monkeypatch)

    _new_photo(tmp_path)
    aura = offline_aura(overrides={ASSETS_PATH: _assets_response()})

    cli.run_sync(str(tmp_path), 'Fake', apply=True, yes=True, aura=aura,
                 verb='push', country='US')

    assert calls[0]['geo_check'] is not None


def test_push_no_country_anywhere_geo_check_is_none(tmp_path, monkeypatch):
    _env(monkeypatch)
    monkeypatch.setattr(cli, 'AURA_COUNTRY', None)
    _patch_aws_clients(monkeypatch)
    calls = _patch_execute_plan(monkeypatch)

    _new_photo(tmp_path)
    aura = offline_aura(overrides={ASSETS_PATH: _assets_response()})

    cli.run_sync(str(tmp_path), 'Fake', apply=True, yes=True, aura=aura, verb='push')

    assert calls[0]['geo_check'] is None


def test_sync_apply_no_new_flags_still_forwards_default_budget_and_geo_check(tmp_path, monkeypatch):
    _env(monkeypatch)
    monkeypatch.setattr(cli, 'AURA_COUNTRY', 'FR')
    _patch_aws_clients(monkeypatch)
    calls = _patch_execute_plan(monkeypatch)

    _new_photo(tmp_path)
    aura = offline_aura(overrides={ASSETS_PATH: _assets_response()})

    cli.run_sync(str(tmp_path), 'Fake', apply=True, yes=True, aura=aura)

    assert calls[0]['budget'] is not None
    assert calls[0]['geo_check'] is not None
    # But no override kwargs -- backward-compat preserved (mirrors
    # test_sync_defaults_do_not_override_execute_plan_defaults in
    # test_cli_apply.py for batch_size/chunk_delay_seconds).
    assert calls[0]['wait_on_budget'] is None
    assert calls[0]['max_wait_seconds'] is None
    assert calls[0]['batch_size'] is None
    assert calls[0]['chunk_delay_seconds'] is None


def test_geo_mismatch_error_produces_clean_message_and_exit_1(tmp_path, monkeypatch, capsys):
    _env(monkeypatch)
    _patch_aws_clients(monkeypatch)
    _patch_execute_plan(monkeypatch, raises=GeoMismatchError('BE', 'FR'))

    _new_photo(tmp_path)
    aura = offline_aura(overrides={ASSETS_PATH: _assets_response()})

    rc = cli.run_sync(str(tmp_path), 'Fake', apply=True, yes=True, aura=aura)

    assert rc == 1
    out = capsys.readouterr().out
    assert 'BE' in out
    assert 'FR' in out
    assert 'VPN' in out


def test_budget_exhausted_produces_clean_message_and_exit_1(tmp_path, monkeypatch, capsys):
    _env(monkeypatch)
    _patch_aws_clients(monkeypatch)
    _patch_execute_plan(monkeypatch, raises=BudgetExhausted(600.0))

    _new_photo(tmp_path)
    aura = offline_aura(overrides={ASSETS_PATH: _assets_response()})

    rc = cli.run_sync(str(tmp_path), 'Fake', apply=True, yes=True, aura=aura)

    assert rc == 1
    out = capsys.readouterr().out
    assert 'budget exhausted' in out.lower()
    assert '10 min' in out  # 600s / 60 = 10 min
    assert '--no-wait' in out


def test_state_file_path_derives_from_sha1_email_and_body_has_no_email(tmp_path, monkeypatch):
    import hashlib

    _env(monkeypatch)
    _patch_aws_clients(monkeypatch)

    # Drive the fake execute_plan to actually call budget.save() (the real
    # execute_plan does this after every normally-returning chunk) so the
    # state file lands on disk under the monkeypatched AURA_STATE_DIR.
    def fake_execute_plan(plan, aura, frame_id, *, s3_client, sqs_client, progress=None, on_wait=None, on_error=None,
                          batch_size=None, chunk_delay_seconds=None, budget=None, geo_check=None,
                          wait_on_budget=None, max_wait_seconds=None, removal_mode=None):
        if budget is not None:
            budget.save()
        return ExecutionResult()

    monkeypatch.setattr(cli, 'execute_plan', fake_execute_plan)

    _new_photo(tmp_path)
    aura = offline_aura(overrides={ASSETS_PATH: _assets_response()})

    cli.run_sync(str(tmp_path), 'Fake', apply=True, yes=True, aura=aura)

    expected_hash = hashlib.sha1(EMAIL.encode()).hexdigest()[:12]
    expected_path = tmp_path / f'budget-{expected_hash}.json'
    assert expected_path.exists()

    body = expected_path.read_text()
    assert EMAIL not in body
    assert 'super-secret-pw' not in body
    data = json.loads(body)
    assert set(data.keys()) == {'tokens', 'updated_at'}


def test_build_parser_push_exposes_new_flags_sync_does_not():
    parser = cli.build_parser()
    push_args = parser.parse_args(['push', '/some/dir', '--frame', 'Fake', '--max-wait', '10',
                                   '--no-wait', '--country', 'US', '--ignore-budget'])
    assert push_args.max_wait == 10.0
    assert push_args.no_wait is True
    assert push_args.country == 'US'
    assert push_args.ignore_budget is True

    with pytest.raises(SystemExit):
        parser.parse_args(['sync', '/some/dir', '--frame', 'Fake', '--max-wait', '10'])

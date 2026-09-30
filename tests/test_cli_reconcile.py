"""Offline tests for `pushframe reconcile` (pushframe.cli.run_reconcile) and
the placeholder-count line `run_inspect` gained alongside it (Phase 11 Plan
03, Tasks 2-3). Calls the handlers directly (never main()), copying
tests/test_cli_inspect.py's conventions exactly.

Unmarked (no @pytest.mark.live) -- this is the default pytest suite, runs
with zero network access and no real credentials.
"""
import copy
import json

import httpx
import pytest
from loguru import logger

import pushframe.cli as cli_module
from pushframe.cli import build_parser, run_inspect, run_reconcile
from pushframe.reconcile import find_placeholders
from tests.offline import FIXTURES_DIR, offline_aura

FRAME_ID = 'frame-fake-0001'
ASSETS_PATH = f'/v5/frames/{FRAME_ID}/assets.json'
SELECT_ASSET_PATH = f'/v5/frames/{FRAME_ID}/select_asset.json'
REMOVE_ASSET_PATH = f'/v5/frames/{FRAME_ID}/remove_asset.json'
EXCLUDE_ASSET_PATH = f'/v5/frames/{FRAME_ID}/exclude_asset'
BATCH_UPDATE_PATH = '/v5/assets/batch_update.json'
DELETE_ASSET_PATH_PREFIX = '/v5/assets/'

RECENT_TOKEN = '__RECENT_CREATED_AT_TOKEN__'
_OLD_FILL = '2020-01-01T00:00:00.000Z'


@pytest.fixture(autouse=True)
def _reset_loguru():
    # loguru's `logger` is a process-global singleton; reset around each
    # test so the stderr/capsys assertions stay deterministic.
    logger.remove()
    yield
    logger.remove()


def _placeholder_assets_payload(recent_created_at: str = _OLD_FILL) -> dict:
    """Load tests/fixtures/assets_placeholders.json, substituting the
    recently-created row's placeholder token for `recent_created_at` (a
    hardcoded date would age -- see the fixture's own _comment)."""
    data = json.loads((FIXTURES_DIR / 'assets_placeholders.json').read_text())
    for entry in data['assets']:
        if entry.get('created_at') == RECENT_TOKEN:
            entry['created_at'] = recent_created_at
    return data


def _placeholder_assets_response(recent_created_at: str = _OLD_FILL) -> httpx.Response:
    return httpx.Response(200, json=_placeholder_assets_payload(recent_created_at))


def _empty_assets_response() -> httpx.Response:
    return httpx.Response(200, json={'assets': [], 'next_page_cursor': None})


def _write_paths_hit(aura) -> list:
    """Every POST/PUT/DELETE request recorded in aura._client.history
    against a frame/asset MUTATION path -- used to prove a report-only run
    never reaches a write path. `login.json` is itself a POST but is not a
    frame/asset mutation, so it is deliberately excluded here."""
    return [
        r.request for r in aura._client.history
        if r.request.method in ('POST', 'PUT', 'DELETE')
        and r.request.url.path != '/v5/login.json'
    ]


# ---------------------------------------------------------------------------
# Test 8: run_reconcile reports stuck/recently-created/unknown-age counts.
# ---------------------------------------------------------------------------

def test_run_reconcile_reports_bucket_counts(monkeypatch, capsys):
    monkeypatch.setenv('AURA_EMAIL', 'you@example.invalid')
    monkeypatch.setenv('AURA_PASSWORD', 'super-secret-pw')

    aura = offline_aura(overrides={ASSETS_PATH: _placeholder_assets_response()})

    rc = run_reconcile('Fake', aura=aura)

    assert rc == 0
    out = capsys.readouterr().out
    assert 'Placeholder rows:' in out
    assert 'stuck (older than' in out
    assert 'recently created' in out
    assert 'creation time unknown' in out


# ---------------------------------------------------------------------------
# Test 9: run_reconcile without remove=True makes no write request at all.
# ---------------------------------------------------------------------------

def test_run_reconcile_report_only_makes_no_write_request(monkeypatch, capsys):
    monkeypatch.setenv('AURA_EMAIL', 'you@example.invalid')
    monkeypatch.setenv('AURA_PASSWORD', 'super-secret-pw')

    aura = offline_aura(overrides={ASSETS_PATH: _placeholder_assets_response()})

    rc = run_reconcile('Fake', aura=aura, remove=False)

    assert rc == 0
    assert _write_paths_hit(aura) == []


# ---------------------------------------------------------------------------
# Test 10: run_inspect's placeholder-count line equals find_placeholders'
# count for the same fixture -- one function, two callers.
# ---------------------------------------------------------------------------

def test_run_inspect_placeholder_count_matches_find_placeholders(monkeypatch, capsys):
    monkeypatch.setenv('AURA_EMAIL', 'you@example.invalid')
    monkeypatch.setenv('AURA_PASSWORD', 'super-secret-pw')

    payload = _placeholder_assets_payload()
    aura = offline_aura(overrides={ASSETS_PATH: httpx.Response(200, json=payload)})

    rc = run_inspect('Fake', aura=aura)

    assert rc == 0
    out = capsys.readouterr().out

    from pushframe.models.asset import Asset
    assets = [Asset(**a) for a in payload['assets']]
    expected_count = find_placeholders(assets).placeholder_count

    assert f'Placeholder rows: {expected_count}' in out


# ---------------------------------------------------------------------------
# Test 11: run_reconcile shares run_inspect's ambiguous/not_found branches.
# ---------------------------------------------------------------------------

def _single_frame():
    data = json.loads((FIXTURES_DIR / 'frames.json').read_text())
    return copy.deepcopy(data['frames'][0])


def _frames_response(*frames):
    return httpx.Response(200, json={'frames': list(frames)})


def test_run_reconcile_ambiguous_name_lists_candidates_and_returns_1(monkeypatch, capsys):
    monkeypatch.setenv('AURA_EMAIL', 'you@example.invalid')
    monkeypatch.setenv('AURA_PASSWORD', 'super-secret-pw')

    kitchen = _single_frame()
    kitchen['id'] = 'frame-fake-kitchen'
    kitchen['name'] = 'Kitchen'
    kitchen_upstairs = _single_frame()
    kitchen_upstairs['id'] = 'frame-fake-kitchen-2'
    kitchen_upstairs['name'] = 'Kitchen 2 Upstairs'

    aura = offline_aura(overrides={'/v5/frames.json': _frames_response(kitchen, kitchen_upstairs)})

    rc = run_reconcile('kitchen', aura=aura)

    assert rc == 1
    out = capsys.readouterr().out
    assert 'Kitchen' in out
    assert 'Kitchen 2 Upstairs' in out
    assert 'frame-fake-kitchen' in out


def test_run_reconcile_not_found_lists_available_frames_and_returns_1(monkeypatch, capsys):
    monkeypatch.setenv('AURA_EMAIL', 'you@example.invalid')
    monkeypatch.setenv('AURA_PASSWORD', 'super-secret-pw')

    rc = run_reconcile('does-not-exist-xyz', aura=offline_aura())

    assert rc == 1
    out = capsys.readouterr().out
    assert 'Fake Frame' in out


# ---------------------------------------------------------------------------
# Test 12: an empty asset listing aborts with a named error, not a zero.
# ---------------------------------------------------------------------------

def test_run_reconcile_empty_listing_refuses_to_report_zero(monkeypatch, capsys):
    monkeypatch.setenv('AURA_EMAIL', 'you@example.invalid')
    monkeypatch.setenv('AURA_PASSWORD', 'super-secret-pw')

    aura = offline_aura(overrides={ASSETS_PATH: _empty_assets_response()})

    rc = run_reconcile('Fake', aura=aura)

    assert rc == 1
    out = capsys.readouterr().out
    assert 'Placeholder rows: 0' not in out
    assert 'cannot be distinguished' in out


# ---------------------------------------------------------------------------
# Test 13: build_parser's reconcile defaults.
# ---------------------------------------------------------------------------

def test_reconcile_parser_defaults():
    args = build_parser().parse_args(['reconcile', '--frame', 'x'])

    assert args.remove is False
    assert args.yes is False
    assert args.mechanism == 'remove'
    assert args.max_age_hours == 24.0


def test_reconcile_parser_flags_are_settable():
    args = build_parser().parse_args([
        'reconcile', '--frame', 'x', '--remove', '--yes',
        '--mechanism', 'hard-delete', '--max-age-hours', '2.5',
    ])

    assert args.remove is True
    assert args.yes is True
    assert args.mechanism == 'hard-delete'
    assert args.max_age_hours == 2.5


def test_reconcile_listed_in_root_help(capsys):
    with pytest.raises(SystemExit):
        build_parser().parse_args(['--help'])
    out = capsys.readouterr().out
    assert 'reconcile' in out


def test_run_reconcile_no_credentials_exits_nonzero_named(tmp_path, monkeypatch, capsys):
    """Phase 24: no aura injected + no credentials anywhere = named failure
    (DI contract: call sites never re-authenticate an injected aura)."""
    from pushframe.utils import settings
    monkeypatch.setattr(settings, 'CONFIG_PATH', tmp_path / 'config.json')
    for var in ('AURA_EMAIL', 'AURA_PASSWORD', 'PUSHFRAME_EMAIL', 'PUSHFRAME_PASSWORD'):
        monkeypatch.delenv(var, raising=False)

    rc = run_reconcile('Fake')

    assert rc == 1
    out = capsys.readouterr().out
    assert 'not authenticated' in out
    assert 'pushframe config' in out


# ===========================================================================
# Task 3: the --remove continuation
# ===========================================================================

# ---------------------------------------------------------------------------
# Test 14: apply_reconciliation is never reachable from run_reconcile
# without remove=True -- patched to raise, a report-only run still
# succeeds, proving it was never called.
# ---------------------------------------------------------------------------

def test_run_reconcile_report_only_never_calls_apply_reconciliation(monkeypatch, capsys):
    monkeypatch.setenv('AURA_EMAIL', 'you@example.invalid')
    monkeypatch.setenv('AURA_PASSWORD', 'super-secret-pw')

    def _boom(*args, **kwargs):
        raise AssertionError('apply_reconciliation must not be called when remove=False')

    monkeypatch.setattr(cli_module, 'apply_reconciliation', _boom)

    aura = offline_aura(overrides={ASSETS_PATH: _placeholder_assets_response()})

    rc = run_reconcile('Fake', aura=aura, remove=False)

    assert rc == 0


# ---------------------------------------------------------------------------
# Test 19: run_reconcile(remove=True, yes=False) on non-interactive stdin
# fails closed -- prints the message, returns 1, issues no write request.
# ---------------------------------------------------------------------------

def test_run_reconcile_remove_without_yes_noninteractive_fails_closed(monkeypatch, capsys):
    monkeypatch.setenv('AURA_EMAIL', 'you@example.invalid')
    monkeypatch.setenv('AURA_PASSWORD', 'super-secret-pw')
    monkeypatch.setattr('sys.stdin.isatty', lambda: False)

    aura = offline_aura(overrides={ASSETS_PATH: _placeholder_assets_response()})

    rc = run_reconcile('Fake', aura=aura, remove=True, yes=False)

    assert rc == 1
    out = capsys.readouterr().out
    assert '--remove requires --yes when running non-interactively' in out
    assert _write_paths_hit(aura) == []


def test_run_reconcile_remove_yes_end_to_end_removes_stuck_rows(monkeypatch, capsys):
    monkeypatch.setenv('AURA_EMAIL', 'you@example.invalid')
    monkeypatch.setenv('AURA_PASSWORD', 'super-secret-pw')
    # A real WriteBudget on first use starts at 0 tokens and would make
    # apply_reconciliation's budget.acquire() wait for real (minutes, per
    # AURA_WRITE_BUDGET_REFILL_PER_MIN's default) -- neutralize it here so
    # this test proves the CLI's wiring, not the budget's own timing
    # (budget costing itself is covered offline in tests/test_reconcile.py
    # via a `_FakeBudget`, and forwarding-to-a-real-WriteBudget is covered
    # in tests/test_cli_push_budget_geo.py's identical pattern).
    monkeypatch.setattr(cli_module, '_build_write_budget', lambda email, ignore_budget: None)

    aura = offline_aura(overrides={
        ASSETS_PATH: _placeholder_assets_response(),
        REMOVE_ASSET_PATH: httpx.Response(200, json={'number_failed': 0}),
    })

    rc = run_reconcile('Fake', aura=aura, remove=True, yes=True, mechanism='remove')

    assert rc == 0
    out = capsys.readouterr().out
    assert 'Removed: 3 succeeded, 0 failed' in out


def test_run_reconcile_hard_delete_interactive_gate_rejects_everything_but_the_count(monkeypatch, capsys):
    """Prompt-contract audit: the hard-delete gate demands the VERBATIM row
    count — the prompt says so, the reflex 'y' is rejected, any other text
    too, and NO write path is touched on any wrong answer."""
    monkeypatch.setenv('AURA_EMAIL', 'you@example.invalid')
    monkeypatch.setenv('AURA_PASSWORD', 'super-secret-pw')
    monkeypatch.setattr('sys.stdin.isatty', lambda: True)

    aura = offline_aura(overrides={ASSETS_PATH: _placeholder_assets_response()})

    for wrong in ('y', '12', ''):  # 3 stuck rows in the fixture; none of these is '3'
        prompts = []

        def fake_input(prompt='', _p=prompts):
            _p.append(prompt)
            return wrong

        monkeypatch.setattr('builtins.input', fake_input)
        rc = run_reconcile('Fake', aura=aura, remove=True, yes=False,
                           mechanism='hard-delete')
        assert rc == 0
        assert 'Verbatim to confirm' in prompts[-1]
        assert '(3)' in prompts[-1]
        assert 'Aborted.' in capsys.readouterr().out
        assert _write_paths_hit(aura) == []


def test_run_reconcile_hard_delete_proceeds_on_the_exact_count(monkeypatch, capsys):
    monkeypatch.setenv('AURA_EMAIL', 'you@example.invalid')
    monkeypatch.setenv('AURA_PASSWORD', 'super-secret-pw')
    monkeypatch.setattr('sys.stdin.isatty', lambda: True)
    monkeypatch.setattr(cli_module, '_build_write_budget', lambda email, ignore_budget: None)
    monkeypatch.setattr('builtins.input', lambda *_: '3')  # exactly the fixture's stuck count
    # The offline router has no canned DELETE /v5/assets/{id} route — prove
    # the GATE lets the exact count through by observing the primitive's
    # calls instead (the endpoint's own behavior is covered in test_reconcile).
    import pushframe.reconcile as reconcile_mod
    sent = []

    def _fake_hard_delete(aura_, frame_id, chunk):
        sent.extend(asset.id for asset in chunk)
        return [object()] * len(chunk)

    monkeypatch.setitem(reconcile_mod._RECONCILE_PRIMITIVE, 'hard-delete',
                        _fake_hard_delete)

    aura = offline_aura(overrides={ASSETS_PATH: _placeholder_assets_response()})

    rc = run_reconcile('Fake', aura=aura, remove=True, yes=False,
                       mechanism='hard-delete')

    assert rc == 0
    assert len(sent) == 3


def test_run_reconcile_remove_interactive_y_n_gate(monkeypatch, capsys):
    """Prompt-contract audit: the default (remove) mechanism's interactive
    gate is a real y/N — the prompt says so, a non-y answer aborts before
    any write."""
    monkeypatch.setenv('AURA_EMAIL', 'you@example.invalid')
    monkeypatch.setenv('AURA_PASSWORD', 'super-secret-pw')
    monkeypatch.setattr('sys.stdin.isatty', lambda: True)
    prompts = []

    def fake_input(prompt=''):
        prompts.append(prompt)
        return 'n'

    monkeypatch.setattr('builtins.input', fake_input)

    aura = offline_aura(overrides={ASSETS_PATH: _placeholder_assets_response()})

    rc = run_reconcile('Fake', aura=aura, remove=True, yes=False, mechanism='remove')

    assert rc == 0
    assert 'Proceed? [y/N]' in prompts[-1]
    assert 'Aborted.' in capsys.readouterr().out
    assert _write_paths_hit(aura) == []

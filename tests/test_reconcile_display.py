"""Display sweep (261018-evx follow-up): `pushframe reconcile --remove`
used to go fully silent between the probe prompt and the final summary
line -- the apply loop had nothing to say while it worked chunk by chunk.

Two checks:
  1. the wiring: run_reconcile hands a progress callback into
     apply_reconciliation (the way inspect/sync/push already do);
  2. the visible contract: one ✓ line per removed row printing BEFORE the
     final summary, naming the asset id.

Doctor's one pacing note before the write probe lives in
tests/test_doctor.py's scope; this file covers reconcile only.

Unmarked (default offline suite, zero network, no real credentials) --
same conventions as tests/test_cli_reconcile.py, which this file borrows
its fixture plumbing from verbatim.
"""
import httpx

import pushframe.cli as cli_module
from pushframe.cli import run_reconcile
from tests.offline import FIXTURES_DIR, offline_aura

FRAME_ID = 'frame-fake-0001'
ASSETS_PATH = f'/v5/frames/{FRAME_ID}/assets.json'
REMOVE_ASSET_PATH = f'/v5/frames/{FRAME_ID}/remove_asset.json'

RECENT_TOKEN = '__RECENT_CREATED_AT_TOKEN__'
_OLD_FILL = '2020-01-01T00:00:00.000Z'


def _placeholder_assets_payload(recent_created_at: str = _OLD_FILL) -> dict:
    """Same fixture hydration as test_cli_reconcile's helper: substitute
    the recently-created row's placeholder token for `recent_created_at`
    (a hardcoded date would age -- see the fixture's own _comment)."""
    import json
    data = json.loads((FIXTURES_DIR / 'assets_placeholders.json').read_text())
    for entry in data['assets']:
        if entry.get('created_at') == RECENT_TOKEN:
            entry['created_at'] = recent_created_at
    return data


def _clean_remove_env(monkeypatch):
    """Env credentials + a neutralized WriteBudget (apply_reconciliation
    would otherwise make budget.acquire() wait real minutes on first use;
    budget timing is tested in tests/test_reconcile.py via a _FakeBudget).
    Patched via cli_module (the module run_reconcile reads), matching
    test_cli_reconcile's identical pattern."""
    monkeypatch.setenv('AURA_EMAIL', 'you@example.invalid')
    monkeypatch.setenv('AURA_PASSWORD', 'super-secret-pw')
    monkeypatch.setattr(cli_module, '_build_write_budget', lambda email, ignore_budget: None)


def test_run_reconcile_wires_progress_into_apply_reconciliation(monkeypatch, capsys):
    """The wiring proof: apply_reconciliation is CALLED with a progress
    callable -- not the silent structural default"""
    _clean_remove_env(monkeypatch)

    seen = {}
    _real_apply = cli_module.apply_reconciliation

    def spy(result, aura, frame_id, *args, **kwargs):
        seen['progress'] = kwargs.get('progress')
        return _real_apply(result, aura, frame_id, *args, **kwargs)

    monkeypatch.setattr(cli_module, 'apply_reconciliation', spy)

    remove_ok = httpx.Response(200, json={'number_failed': 0})
    aura = offline_aura(overrides={
        ASSETS_PATH: httpx.Response(200, json=_placeholder_assets_payload()),
        REMOVE_ASSET_PATH: remove_ok,
    })

    rc = run_reconcile('Fake', aura=aura, remove=True, yes=True, mechanism='remove')
    assert rc == 0
    assert callable(seen['progress'])


def test_run_reconcile_remove_emits_one_progress_line_per_row(monkeypatch, capsys):
    """The visible contract: one ✓ line per removed row, printed BEFORE
    the final summary, each naming its asset id."""
    _clean_remove_env(monkeypatch)

    remove_ok = httpx.Response(200, json={'number_failed': 0})
    aura = offline_aura(overrides={
        ASSETS_PATH: httpx.Response(200, json=_placeholder_assets_payload()),
        REMOVE_ASSET_PATH: remove_ok,
    })

    rc = run_reconcile('Fake', aura=aura, remove=True, yes=True, mechanism='remove')
    assert rc == 0

    out = capsys.readouterr().out
    assert 'Removed: 3 succeeded, 0 failed' in out  # fixture's stuck count
    progress_lines = [line for line in out.splitlines() if '✓ removed' in line]
    assert len(progress_lines) == 3
    # every stuck row named exactly once, and before the summary line
    summary_idx = out.index('Removed: 3 succeeded')
    for line in progress_lines:
        assert out.index(line) < summary_idx

"""Offline tests for the Phase 09 (proactive-write-rate-limiter-geo-guard)
`budget`/`geo_check`/`wait_on_budget`/`max_wait_seconds`/`clock` integration
in `pushframe.sync.execute_plan` (Plan 09-02, ANTI-03/ANTI-04).

Mirrors `tests/test_execute_plan.py`'s harness (`offline_aura`, duck-typed
S3/SQS fakes) but drives a FAKE budget object (records `acquire`/`save`/
`reconcile_tripped` calls) instead of the real `WriteBudget`, so these tests
assert call sequencing without real timing -- `WriteBudget` itself is
already fully unit-tested offline in `tests/test_ratelimit.py` (Plan 09-01).
"""
import httpx
import pytest
from loguru import logger
from PIL import Image

from pushframe.client import RateLimitError
from pushframe.models.asset import Asset, AssetPartialId
from pushframe.sync import ConsecutiveWriteFailureError, SyncPlan, execute_plan
from tests.offline import offline_aura

FRAME_ID = 'frame-fake-0001'
SELECT_ASSET_PATH = f'/v5/frames/{FRAME_ID}/select_asset.json'
REMOVE_ASSET_PATH = f'/v5/frames/{FRAME_ID}/remove_asset.json'
# No `.json` suffix -- deliberate, matches the app and is live-confirmed.
EXCLUDE_ASSET_PATH = f'/v5/frames/{FRAME_ID}/exclude_asset'
BATCH_UPDATE_PATH = '/v5/assets/batch_update.json'


@pytest.fixture(autouse=True)
def _reset_loguru():
    logger.remove()
    yield
    logger.remove()


def _write_jpeg(path, color=(255, 0, 0)):
    Image.new('RGB', (4, 4), color).save(path, format='JPEG')


def _asset(id_):
    return Asset.model_construct(id=id_, md5_hash='deadbeef', taken_at='2024-03-11T12:00:00.000Z')


def _default_overrides():
    return {
        SELECT_ASSET_PATH: httpx.Response(200, json={'number_failed': 0}),
        REMOVE_ASSET_PATH: httpx.Response(200, json={'number_failed': 0}),
        EXCLUDE_ASSET_PATH: httpx.Response(200, json={'number_failed': 0}),
        BATCH_UPDATE_PATH: httpx.Response(200, json={
            'ids': ['local-id'],
            'successes': [{'id': 'new-asset-id', 'local_identifier': 'local-id'}],
        }),
    }


class _FakeS3Client:
    def __init__(self):
        self.upload_calls = []

    def upload_file(self, data: bytes, extension: str):
        self.upload_calls.append((data, extension))
        return f'uploaded-{len(self.upload_calls)}{extension}', 'fake-md5-hash'


class _FakeSQSClient:
    def __init__(self):
        self.queue_url_requests = []

    def get_queue_url(self, frame_id: str):
        self.queue_url_requests.append(frame_id)
        return f'https://sqs.fake/{frame_id}'

    def receive_message(self, queue_url, wait_time_seconds=5):
        return {}


class _FakeBudget:
    """Records `acquire`/`save`/`reconcile_tripped` calls in order, without
    any real token-bucket math -- `WriteBudget` itself is unit-tested in
    `tests/test_ratelimit.py`. This test file only asserts execute_plan's
    call SEQUENCING against the budget's public surface."""

    def __init__(self):
        self.acquire_calls: list[dict] = []
        self.save_calls = 0
        self.reconcile_calls: list = []

    def acquire(self, n, *, wait, max_wait, now, sleep, on_wait=None):
        self.acquire_calls.append({'n': n, 'wait': wait, 'max_wait': max_wait, 'now': now})

    def save(self):
        self.save_calls += 1

    def reconcile_tripped(self, now):
        self.reconcile_calls.append(now)


def _install_ack_all_batch_update(aura):
    calls: list = []

    def _fake_batch_update(assets):
        from pushframe.api.assetApi import BatchUpdateResult
        items = assets if isinstance(assets, list) else [assets]
        calls.append(items)
        ids = [item.local_identifier for item in items]
        successes = [{'id': f'new-{lid}', 'local_identifier': lid} for lid in ids]
        return BatchUpdateResult(ids, [AssetPartialId(**s) for s in successes], [])

    aura.asset_api.batch_update = _fake_batch_update
    return calls


def test_upload_only_plan_acquires_2_per_chunk_and_saves_after_each_chunk(tmp_path):
    path_a = tmp_path / 'a.jpg'
    path_b = tmp_path / 'b.jpg'
    _write_jpeg(path_a)
    _write_jpeg(path_b)
    plan = SyncPlan(to_upload=[path_a, path_b], to_delete=[])

    aura = offline_aura(overrides=_default_overrides())
    _install_ack_all_batch_update(aura)
    budget = _FakeBudget()

    result = execute_plan(
        plan, aura, FRAME_ID, s3_client=_FakeS3Client(), sqs_client=_FakeSQSClient(),
        sleep=lambda *_: None, batch_size=1, budget=budget, clock=lambda: 'fixed-now',
    )

    assert result.upload_succeeded == 2
    # 2 chunks (batch_size=1) -> 2 acquire(2, ...) calls, one per chunk.
    assert len(budget.acquire_calls) == 2
    assert all(c['n'] == 2 for c in budget.acquire_calls)
    assert all(c['now'] == 'fixed-now' for c in budget.acquire_calls)
    # Save after each normally-returning chunk.
    assert budget.save_calls == 2
    assert budget.reconcile_calls == []


def test_delete_only_plan_acquires_1_per_chunk(tmp_path):
    plan = SyncPlan(to_upload=[], to_delete=[_asset('a1'), _asset('a2')])

    aura = offline_aura(overrides=_default_overrides())
    budget = _FakeBudget()

    result = execute_plan(
        plan, aura, FRAME_ID, s3_client=_FakeS3Client(), sqs_client=_FakeSQSClient(),
        sleep=lambda *_: None, batch_size=1, budget=budget, clock=lambda: 'fixed-now',
    )

    assert result.delete_succeeded == 2
    # 2 chunks (batch_size=1) -> 2 acquire(1, ...) calls, one per chunk.
    assert len(budget.acquire_calls) == 2
    assert all(c['n'] == 1 for c in budget.acquire_calls)
    assert budget.save_calls == 2


def test_geo_check_called_exactly_once_before_any_write_including_delete_only(tmp_path):
    plan = SyncPlan(to_upload=[], to_delete=[_asset('a1')])

    aura = offline_aura(overrides=_default_overrides())
    calls = []

    def geo_check():
        calls.append('checked')

    result = execute_plan(
        plan, aura, FRAME_ID, s3_client=_FakeS3Client(), sqs_client=_FakeSQSClient(),
        sleep=lambda *_: None, geo_check=geo_check,
    )

    assert calls == ['checked']
    assert result.delete_succeeded == 1


def test_geo_mismatch_aborts_before_any_write_call(tmp_path, monkeypatch):
    path_a = tmp_path / 'a.jpg'
    _write_jpeg(path_a)
    plan = SyncPlan(to_upload=[path_a], to_delete=[_asset('a1')])

    aura = offline_aura(overrides=_default_overrides())

    def _boom(*args, **kwargs):
        raise AssertionError('write call must not happen after a geo mismatch')

    monkeypatch.setattr(aura.frame_api, 'select_asset', _boom)
    monkeypatch.setattr(aura.frame_api, 'remove_asset', _boom)
    monkeypatch.setattr(aura.asset_api, 'batch_update', _boom)

    class _Mismatch(Exception):
        pass

    def geo_check():
        raise _Mismatch('country mismatch')

    s3 = _FakeS3Client()

    with pytest.raises(_Mismatch):
        execute_plan(
            plan, aura, FRAME_ID, s3_client=s3, sqs_client=_FakeSQSClient(),
            sleep=lambda *_: None, geo_check=geo_check,
        )

    # No S3 upload, no select_asset/batch_update/remove_asset -- aborted
    # before anything was written.
    assert s3.upload_calls == []


def test_rate_limit_error_upload_chunk_reconciles_and_saves_before_raising(tmp_path, monkeypatch):
    path_a = tmp_path / 'a.jpg'
    _write_jpeg(path_a)
    plan = SyncPlan(to_upload=[path_a], to_delete=[])

    aura = offline_aura(overrides=_default_overrides())

    def _rate_limited(*args, **kwargs):
        raise RateLimitError(429, retry_after=60, server_message='too many')

    monkeypatch.setattr(aura.frame_api, 'select_asset', _rate_limited)

    budget = _FakeBudget()

    with pytest.raises(RateLimitError):
        execute_plan(
            plan, aura, FRAME_ID, s3_client=_FakeS3Client(), sqs_client=_FakeSQSClient(),
            sleep=lambda *_: None, budget=budget, clock=lambda: 'tripped-now',
        )

    assert budget.reconcile_calls == ['tripped-now']
    assert budget.save_calls == 1


def test_rate_limit_error_delete_chunk_reconciles_and_saves_before_raising(monkeypatch):
    plan = SyncPlan(to_upload=[], to_delete=[_asset('a1')])

    aura = offline_aura(overrides=_default_overrides())

    def _rate_limited(*args, **kwargs):
        raise RateLimitError(429, retry_after=60, server_message='too many')

    monkeypatch.setattr(aura.frame_api, 'remove_asset', _rate_limited)

    budget = _FakeBudget()

    with pytest.raises(RateLimitError):
        execute_plan(
            plan, aura, FRAME_ID, s3_client=_FakeS3Client(), sqs_client=_FakeSQSClient(),
            sleep=lambda *_: None, budget=budget, clock=lambda: 'tripped-now',
            removal_mode='delete',  # pinned: this test fakes remove_asset
        )

    assert budget.reconcile_calls == ['tripped-now']
    assert budget.save_calls == 1


def test_consecutive_write_failure_reconciles_and_saves_before_raising(monkeypatch):
    # Drive 5 unbroken delete-chunk failures (MAX_CONSECUTIVE_WRITE_FAILURES
    # default) via a chunk-level remove_asset failure, one asset per chunk
    # so each failed chunk counts once toward the run.
    assets = [_asset(f'bad-{i}') for i in range(5)]
    plan = SyncPlan(to_upload=[], to_delete=assets)

    aura = offline_aura(overrides=_default_overrides())

    def _failing_remove_asset(frame_id, asset_partial_ids):
        raise RuntimeError('simulated systemic cut-off')

    monkeypatch.setattr(aura.frame_api, 'remove_asset', _failing_remove_asset)

    budget = _FakeBudget()

    with pytest.raises(ConsecutiveWriteFailureError):
        execute_plan(
            plan, aura, FRAME_ID, s3_client=_FakeS3Client(), sqs_client=_FakeSQSClient(),
            sleep=lambda *_: None, batch_size=1, budget=budget, clock=lambda: 'tripped-now',
            removal_mode='delete',  # pinned: this test fakes remove_asset
        )

    # The reconcile+save happens exactly once, from inside note_failure() at
    # the 5th (threshold-crossing) chunk, whose raise propagates immediately
    # -- it never reaches the loop body's own post-try/except save(). The
    # first 4 chunks each returned normally (an ordinary caught per-chunk
    # failure, not yet at the threshold) and so also called the normal
    # per-chunk save() -- 4 + 1 = 5 total.
    assert budget.reconcile_calls == ['tripped-now']
    assert budget.save_calls == 5


def test_backward_compat_no_budget_no_geo_check_touches_nothing_new(tmp_path):
    # Calling execute_plan with NO budget/geo_check (the pre-Phase-09
    # default) must still work exactly as before -- this is the byte-for-byte
    # no-op guarantee.
    path_a = tmp_path / 'a.jpg'
    _write_jpeg(path_a)
    plan = SyncPlan(to_upload=[path_a], to_delete=[_asset('a1')])

    aura = offline_aura(overrides=_default_overrides())
    _install_ack_all_batch_update(aura)

    result = execute_plan(
        plan, aura, FRAME_ID, s3_client=_FakeS3Client(), sqs_client=_FakeSQSClient(),
        sleep=lambda *_: None,
    )

    assert result.upload_succeeded == 1
    assert result.delete_succeeded == 1
    assert result.upload_failures == []
    assert result.delete_failures == []


# --- removal-mode budget accounting (HIDE-03, Pitfall 3) --------------------

def _install_removal_spies(aura):
    aura.frame_api.exclude_asset = lambda frame_id, ids: 0
    aura.frame_api.remove_asset = lambda frame_id, ids: 0
    aura.frame_api.select_asset = lambda frame_id, ids: 0
    aura.asset_api.delete_asset = lambda asset: None


@pytest.mark.parametrize('removal_mode', ['hide', 'delete'])
def test_batch_removal_modes_acquire_one_token_per_chunk(removal_mode):
    """hide and delete are batch endpoints -- one request per chunk."""
    plan = SyncPlan(to_upload=[], to_delete=[_asset('a1'), _asset('a2')])
    aura = offline_aura(overrides=_default_overrides())
    _install_removal_spies(aura)
    budget = _FakeBudget()

    execute_plan(plan, aura, FRAME_ID, s3_client=_FakeS3Client(), sqs_client=_FakeSQSClient(),
                 sleep=lambda *_: None, budget=budget, clock=lambda: 'fixed-now',
                 removal_mode=removal_mode)

    assert [c['n'] for c in budget.acquire_calls] == [1]


def test_hard_delete_acquires_one_token_per_asset_not_per_chunk():
    """delete_asset has no batch form, so a 2-asset chunk really is 2 requests.
    Charging the budget 1 would let a hard delete run it dry unnoticed and
    re-trip the anti-abuse lockout (Pitfall 3)."""
    plan = SyncPlan(to_upload=[], to_delete=[_asset('a1'), _asset('a2'), _asset('a3')])
    aura = offline_aura(overrides=_default_overrides())
    _install_removal_spies(aura)
    budget = _FakeBudget()

    execute_plan(plan, aura, FRAME_ID, s3_client=_FakeS3Client(), sqs_client=_FakeSQSClient(),
                 sleep=lambda *_: None, budget=budget, clock=lambda: 'fixed-now',
                 removal_mode='hard_delete')

    assert [c['n'] for c in budget.acquire_calls] == [3]


def test_reshow_chunk_acquires_one_token():
    plan = SyncPlan(to_upload=[], to_delete=[], to_reshow=[_asset('r1'), _asset('r2')])
    aura = offline_aura(overrides=_default_overrides())
    _install_removal_spies(aura)
    budget = _FakeBudget()

    execute_plan(plan, aura, FRAME_ID, s3_client=_FakeS3Client(), sqs_client=_FakeSQSClient(),
                 sleep=lambda *_: None, budget=budget, clock=lambda: 'fixed-now')

    assert [c['n'] for c in budget.acquire_calls] == [1]

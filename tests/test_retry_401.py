"""Offline tests for `execute_plan`'s HTTP-401 verify-then-retry path
(Phase 11 Plan 01, Tasks 1-3 -- REL-01/REL-02/REL-03/REL-04, MOD-03).

Follows `tests/test_execute_plan.py`'s header-docstring, `_reset_loguru`
autouse fixture, `_FakeS3Client`/`_FakeSQSClient` and `_write_jpeg`
conventions. Drives `execute_plan` through `offline_aura(overrides=...)`
with STATEFUL callable overrides on the write endpoints (`tests/offline.py`'s
callable-override extension) so a single MockTransport route can answer
differently across successive calls (401 then 200), and drives the verify
probe via the injected `asset_probe=` keyword rather than routing
`/v5/assets/asset_for_local_identifier.json`, so landed/absent/inconclusive
are controlled directly rather than depending on a second live-shaped fixture.

Zero network access, zero AWS credentials -- no real S3Client/SQSClient/
Client transport is ever constructed here.
"""
import json
from datetime import datetime, timezone

import httpx
import pytest
from loguru import logger
from PIL import Image

import pushframe.cli as cli
from pushframe.client import AuraError, AuthenticationError, RateLimitError, WriteEndpointError
from pushframe.models.asset import Asset, AssetPartial
from pushframe.ratelimit import BudgetExhausted, WriteBudget
from pushframe.sync import ConsecutiveWriteFailureError, ExecutionResult, SyncPlan, execute_plan
from tests.offline import offline_aura

# Fixed instant used as `clock()` for real-`WriteBudget` tests below --
# every acquire() call sees the SAME `now`, so elapsed-time refill is always
# 0 and the token math in each assertion is exact (mirrors
# tests/test_ratelimit.py's T0 convention).
T0 = datetime(2024, 1, 1, tzinfo=timezone.utc)

FRAME_ID = 'frame-fake-0001'
SELECT_ASSET_PATH = f'/v5/frames/{FRAME_ID}/select_asset.json'
EXCLUDE_ASSET_PATH = f'/v5/frames/{FRAME_ID}/exclude_asset'
REMOVE_ASSET_PATH = f'/v5/frames/{FRAME_ID}/remove_asset.json'
BATCH_UPDATE_PATH = '/v5/assets/batch_update.json'
LOGIN_PATH = '/v5/login.json'


@pytest.fixture(autouse=True)
def _reset_loguru():
    # loguru's `logger` is a process-global singleton; execute_plan's
    # trailing debug log could otherwise fire against a sink torn down by a
    # prior test (see tests/test_execute_plan.py's identical rationale).
    logger.remove()
    yield
    logger.remove()


def _write_jpeg(path, color=(255, 0, 0)):
    Image.new('RGB', (4, 4), color).save(path, format='JPEG')


def _asset(id_):
    return Asset.model_construct(id=id_, md5_hash='deadbeef', taken_at='2024-03-11T12:00:00.000Z')


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


def _sequenced_responses(*responses):
    """A MockTransport-callable override returning each `httpx.Response` in
    `responses` in order, repeating the LAST one once exhausted -- how these
    tests drive an endpoint that 401s on its first call and succeeds after."""
    state = {'i': 0}

    def handler(request: httpx.Request) -> httpx.Response:
        i = min(state['i'], len(responses) - 1)
        state['i'] += 1
        return responses[i]

    return handler


def _batch_update_recorder(*, fail_first: bool = False, always_fail: bool = False):
    """A MockTransport-callable override for `/v5/assets/batch_update.json`
    that records each call's sent `assets` list (for payload-shape
    assertions) and acknowledges every local_identifier it was sent --
    unless `fail_first` (401 on call 1 only) or `always_fail` (401 on every
    call) is set."""
    payloads: list = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        payloads.append(body['assets'])
        if always_fail or (fail_first and len(payloads) == 1):
            return httpx.Response(401, json={'error': 'unauthorized'})
        sent_ids = [a['local_identifier'] for a in body['assets']]
        return httpx.Response(200, json={
            'ids': sent_ids,
            'successes': [{'id': f'new-{lid}', 'local_identifier': lid} for lid in sent_ids],
        })

    return handler, payloads


def _ordered_probe(fates: list):
    """A fake `asset_probe` callable driving `_probe_landed`'s three-way
    classification directly, keyed by CALL ORDER (which matches `prepped`'s
    sorted-path order, since `_probe_landed` is fed `list(by_lid)` and
    `by_lid` is built from `prepped` in order) rather than by the real
    (uuid-generated, unpredictable) local_identifier values.

    Each entry in `fates` is `'landed'`, `'absent'`, or an int HTTP status
    code to raise as an inconclusive probe (any non-404 status)."""
    state = {'i': 0}

    def probe(local_identifier: str):
        fate = fates[state['i']]
        state['i'] += 1
        if fate == 'landed':
            return None
        status_code = 404 if fate == 'absent' else fate
        request = httpx.Request('GET', 'https://api.pushd.com/v5/assets/asset_for_local_identifier.json')
        response = httpx.Response(status_code, request=request)
        raise httpx.HTTPStatusError(f'probe returned {status_code}', request=request, response=response)

    return probe


def _unreachable_probe(local_identifier: str):
    raise AssertionError(f'probe should not have been called for {local_identifier}')


# ---------------------------------------------------------------------------
# Task 1: end-to-end 401 verify-then-retry for one upload chunk
# ---------------------------------------------------------------------------

def test_401_on_select_asset_recovers_after_relogin_and_resend(tmp_path):
    path = tmp_path / 'a.jpg'
    _write_jpeg(path)
    plan = SyncPlan(to_upload=[path], to_delete=[])

    select_asset_handler = _sequenced_responses(
        httpx.Response(401, json={'error': 'unauthorized'}),
        httpx.Response(200, json={'number_failed': 0}),
    )
    batch_update_handler, batch_payloads = _batch_update_recorder()
    aura = offline_aura(overrides={
        SELECT_ASSET_PATH: select_asset_handler,
        BATCH_UPDATE_PATH: batch_update_handler,
    })

    result = execute_plan(
        plan, aura, FRAME_ID, s3_client=_FakeS3Client(), sqs_client=_FakeSQSClient(),
        sleep=lambda *_: None, throttle_seconds=0, chunk_delay_seconds=0,
        asset_probe=_ordered_probe(['absent']),
    )

    assert result.upload_succeeded == 1
    assert result.upload_failures == []
    login_requests = [r for r in aura._client.history if r.request.url.path == LOGIN_PATH]
    assert len(login_requests) == 1
    assert len(batch_payloads) == 1  # only the retry's resend reaches batch_update


def test_two_item_chunk_401_resends_only_the_genuinely_absent_item(tmp_path):
    path_a = tmp_path / 'a.jpg'
    path_b = tmp_path / 'b.jpg'
    _write_jpeg(path_a)
    _write_jpeg(path_b)
    plan = SyncPlan(to_upload=[path_a, path_b], to_delete=[])

    batch_update_handler, batch_payloads = _batch_update_recorder(fail_first=True)
    aura = offline_aura(overrides={
        SELECT_ASSET_PATH: httpx.Response(200, json={'number_failed': 0}),
        BATCH_UPDATE_PATH: batch_update_handler,
    })

    # sorted(to_upload) => a.jpg, b.jpg -- probe call order matches: A
    # (a.jpg) already landed, B (b.jpg) genuinely absent.
    result = execute_plan(
        plan, aura, FRAME_ID, s3_client=_FakeS3Client(), sqs_client=_FakeSQSClient(),
        sleep=lambda *_: None, throttle_seconds=0, chunk_delay_seconds=0,
        asset_probe=_ordered_probe(['landed', 'absent']),
    )

    assert result.items_already_landed == 1
    assert result.upload_succeeded == 2
    assert result.upload_failures == []
    # First batch_update payload carried BOTH prepped items and 401'd; the
    # SECOND (the retry's resend) carries strictly fewer -- only item B.
    assert len(batch_payloads) == 2
    assert len(batch_payloads[0]) == 2
    assert len(batch_payloads[1]) == 1
    assert len(batch_payloads[1]) < len(batch_payloads[0])


def test_failed_relogin_raises_authentication_error_and_makes_no_further_write_calls(tmp_path):
    path = tmp_path / 'a.jpg'
    _write_jpeg(path)
    plan = SyncPlan(to_upload=[path], to_delete=[])

    select_calls: list = []

    def _select_asset_401(request: httpx.Request) -> httpx.Response:
        select_calls.append(request)
        return httpx.Response(401, json={'error': 'unauthorized'})

    batch_calls: list = []

    def _batch_update_recorder_only(request: httpx.Request) -> httpx.Response:
        batch_calls.append(request)
        return httpx.Response(200, json={'ids': [], 'successes': []})

    aura = offline_aura(overrides={
        SELECT_ASSET_PATH: _select_asset_401,
        BATCH_UPDATE_PATH: _batch_update_recorder_only,
    })

    def _failing_relogin():
        raise RuntimeError('bad credentials')

    with pytest.raises(AuthenticationError):
        execute_plan(
            plan, aura, FRAME_ID, s3_client=_FakeS3Client(), sqs_client=_FakeSQSClient(),
            sleep=lambda *_: None, throttle_seconds=0, chunk_delay_seconds=0,
            relogin=_failing_relogin, asset_probe=_unreachable_probe,
        )

    assert len(select_calls) == 1  # only the original, failing call
    assert batch_calls == []


def test_second_401_after_relogin_attributes_all_and_makes_no_third_attempt(tmp_path):
    path_a = tmp_path / 'a.jpg'
    path_b = tmp_path / 'b.jpg'
    _write_jpeg(path_a)
    _write_jpeg(path_b)
    plan = SyncPlan(to_upload=[path_a, path_b], to_delete=[])

    batch_update_handler, batch_payloads = _batch_update_recorder(always_fail=True)
    aura = offline_aura(overrides={
        SELECT_ASSET_PATH: httpx.Response(200, json={'number_failed': 0}),
        BATCH_UPDATE_PATH: batch_update_handler,
    })

    result = execute_plan(
        plan, aura, FRAME_ID, s3_client=_FakeS3Client(), sqs_client=_FakeSQSClient(),
        sleep=lambda *_: None, throttle_seconds=0, chunk_delay_seconds=0,
        relogin=lambda: None, asset_probe=_ordered_probe(['absent', 'absent']),
    )

    assert len(result.upload_failures) == 2
    assert {p for p, _ in result.upload_failures} == {path_a, path_b}
    # First attempt's batch_update + the ONE retry's resend -- no third.
    assert len(batch_payloads) == 2


def test_inconclusive_probe_is_never_resent_and_is_attributed_as_failure(tmp_path):
    path = tmp_path / 'a.jpg'
    _write_jpeg(path)
    plan = SyncPlan(to_upload=[path], to_delete=[])

    select_asset_handler = _sequenced_responses(
        httpx.Response(401, json={'error': 'unauthorized'}),
        httpx.Response(200, json={'number_failed': 0}),
    )
    batch_calls: list = []

    def _batch_update_recorder_only(request: httpx.Request) -> httpx.Response:
        batch_calls.append(request)
        return httpx.Response(200, json={'ids': [], 'successes': []})

    aura = offline_aura(overrides={
        SELECT_ASSET_PATH: select_asset_handler,
        BATCH_UPDATE_PATH: _batch_update_recorder_only,
    })

    result = execute_plan(
        plan, aura, FRAME_ID, s3_client=_FakeS3Client(), sqs_client=_FakeSQSClient(),
        sleep=lambda *_: None, throttle_seconds=0, chunk_delay_seconds=0,
        relogin=lambda: None, asset_probe=_ordered_probe([500]),
    )

    assert result.upload_succeeded == 0
    assert len(result.upload_failures) == 1
    failed_path, reason = result.upload_failures[0]
    assert failed_path == path
    assert 'inconclusive' in reason
    assert batch_calls == []  # never resent -- an ambiguous probe must not re-send


def test_chunk_where_every_file_fails_prep_issues_no_select_asset_call(tmp_path):
    # '.png' has no data_uti mapping yet (D-11/D-12 land in a later plan),
    # so _prep_upload fails closed on it without ever needing the file to
    # exist -- the whole point of this test is that the chunk never reaches
    # a network call at all.
    bad_path = tmp_path / 'photo.png'
    plan = SyncPlan(to_upload=[bad_path], to_delete=[])

    select_calls: list = []

    def _select_asset_recorder(request: httpx.Request) -> httpx.Response:
        select_calls.append(request)
        return httpx.Response(200, json={'number_failed': 0})

    aura = offline_aura(overrides={SELECT_ASSET_PATH: _select_asset_recorder})

    result = execute_plan(
        plan, aura, FRAME_ID, s3_client=_FakeS3Client(), sqs_client=_FakeSQSClient(),
        sleep=lambda *_: None, throttle_seconds=0, chunk_delay_seconds=0,
    )

    assert len(result.upload_failures) == 1
    assert select_calls == []
    assert result.chunks_retried == 0


# ---------------------------------------------------------------------------
# Task 2: retry budget accounting and runtime visibility
# ---------------------------------------------------------------------------

def _real_budget(tmp_path, tokens: float, refill_per_min: float = 1.0) -> WriteBudget:
    """A real `WriteBudget` (not the sequencing-only fake in
    tests/test_execute_plan_budget_geo.py) so these tests assert on the
    REAL token math, not a recorded call shape -- construction mirrors
    tests/test_ratelimit.py's shape."""
    return WriteBudget(capacity=1000.0, refill_per_min=refill_per_min,
                       path=tmp_path / 'budget.json', tokens=tokens, updated_at=T0)


def test_retried_upload_chunk_consumes_5_budget_tokens_total(tmp_path):
    path = tmp_path / 'a.jpg'
    _write_jpeg(path)
    plan = SyncPlan(to_upload=[path], to_delete=[])

    select_asset_handler = _sequenced_responses(
        httpx.Response(401, json={'error': 'unauthorized'}),
        httpx.Response(200, json={'number_failed': 0}),
    )
    batch_update_handler, _ = _batch_update_recorder()
    aura = offline_aura(overrides={
        SELECT_ASSET_PATH: select_asset_handler,
        BATCH_UPDATE_PATH: batch_update_handler,
    })
    budget = _real_budget(tmp_path, tokens=100.0)

    result = execute_plan(
        plan, aura, FRAME_ID, s3_client=_FakeS3Client(), sqs_client=_FakeSQSClient(),
        sleep=lambda *_: None, throttle_seconds=0, chunk_delay_seconds=0,
        asset_probe=_ordered_probe(['absent']),
        budget=budget, clock=lambda: T0, wait_on_budget=False,
    )

    assert result.upload_succeeded == 1
    # 2 (first attempt: select_asset + batch_update) + 1 (re-login) + 2
    # (retried resend: select_asset + batch_update) = 5.
    assert budget.tokens == 100.0 - 5


def test_all_items_already_landed_consumes_3_tokens_no_resend_charge(tmp_path):
    path_a = tmp_path / 'a.jpg'
    path_b = tmp_path / 'b.jpg'
    _write_jpeg(path_a)
    _write_jpeg(path_b)
    plan = SyncPlan(to_upload=[path_a, path_b], to_delete=[])

    batch_update_handler, batch_payloads = _batch_update_recorder(fail_first=True)
    aura = offline_aura(overrides={
        SELECT_ASSET_PATH: httpx.Response(200, json={'number_failed': 0}),
        BATCH_UPDATE_PATH: batch_update_handler,
    })
    budget = _real_budget(tmp_path, tokens=100.0)

    result = execute_plan(
        plan, aura, FRAME_ID, s3_client=_FakeS3Client(), sqs_client=_FakeSQSClient(),
        sleep=lambda *_: None, throttle_seconds=0, chunk_delay_seconds=0,
        asset_probe=_ordered_probe(['landed', 'landed']),
        budget=budget, clock=lambda: T0, wait_on_budget=False,
    )

    assert result.items_already_landed == 2
    assert result.upload_succeeded == 2
    # 2 (first attempt) + 1 (re-login) = 3 -- the retry's 2 are never
    # acquired because nothing was re-sent (absent is empty).
    assert budget.tokens == 100.0 - 3
    assert len(batch_payloads) == 1  # only the first, 401'd attempt


def test_probe_itself_never_charges_budget_regardless_of_chunk_size(tmp_path):
    paths = [tmp_path / f'{i}.jpg' for i in range(3)]
    for p in paths:
        _write_jpeg(p)
    plan = SyncPlan(to_upload=paths, to_delete=[])

    batch_update_handler, _ = _batch_update_recorder(fail_first=True)
    aura = offline_aura(overrides={
        SELECT_ASSET_PATH: httpx.Response(200, json={'number_failed': 0}),
        BATCH_UPDATE_PATH: batch_update_handler,
    })
    budget = _real_budget(tmp_path, tokens=100.0)

    result = execute_plan(
        plan, aura, FRAME_ID, s3_client=_FakeS3Client(), sqs_client=_FakeSQSClient(),
        sleep=lambda *_: None, throttle_seconds=0, chunk_delay_seconds=0,
        asset_probe=_ordered_probe(['landed', 'landed', 'landed']),
        budget=budget, clock=lambda: T0, wait_on_budget=False,
    )

    assert result.items_already_landed == 3
    # Still only 3 tokens total (2 first attempt + 1 re-login) across a
    # 3-item chunk -- the probe adds nothing per item, proving it is free
    # regardless of chunk size.
    assert budget.tokens == 100.0 - 3


def test_budget_exhausted_propagates_from_retry_acquire_with_no_bypass(tmp_path):
    path = tmp_path / 'a.jpg'
    _write_jpeg(path)
    plan = SyncPlan(to_upload=[path], to_delete=[])

    select_asset_handler = _sequenced_responses(
        httpx.Response(401, json={'error': 'unauthorized'}),
        httpx.Response(200, json={'number_failed': 0}),
    )
    batch_update_handler, _ = _batch_update_recorder()
    aura = offline_aura(overrides={
        SELECT_ASSET_PATH: select_asset_handler,
        BATCH_UPDATE_PATH: batch_update_handler,
    })
    # Enough for the first attempt (2) + re-login (1) = 3, but NOT enough
    # for the retry's resend (needs 2 more) -- refill_per_min=0 forces an
    # immediate BudgetExhausted on the deficit rather than a wait.
    budget = _real_budget(tmp_path, tokens=3.0, refill_per_min=0.0)

    with pytest.raises(BudgetExhausted):
        execute_plan(
            plan, aura, FRAME_ID, s3_client=_FakeS3Client(), sqs_client=_FakeSQSClient(),
            sleep=lambda *_: None, throttle_seconds=0, chunk_delay_seconds=0,
            asset_probe=_ordered_probe(['absent']),
            budget=budget, clock=lambda: T0, wait_on_budget=False,
        )


def test_run_sync_prints_retries_line_unconditionally_even_when_zero(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv('AURA_EMAIL', 'you@example.invalid')
    monkeypatch.setenv('AURA_PASSWORD', 'super-secret-pw')
    monkeypatch.chdir(tmp_path)

    class _FakeS3ClientCtor:
        def __init__(self, *a, **k):
            pass

    class _FakeSQSClientCtor:
        def __init__(self, *a, **k):
            pass

    monkeypatch.setattr(cli, 'S3Client', _FakeS3ClientCtor)
    monkeypatch.setattr(cli, 'SQSClient', _FakeSQSClientCtor)

    def _fake_execute_plan(plan, aura, frame_id, *, s3_client, sqs_client, progress=None,
                           on_wait=None, **kwargs):
        return ExecutionResult()

    monkeypatch.setattr(cli, 'execute_plan', _fake_execute_plan)

    upload_dir = tmp_path / 'photos'
    upload_dir.mkdir()
    _write_jpeg(upload_dir / 'new.jpg')

    assets_path = f'/v5/frames/{FRAME_ID}/assets.json'
    aura = offline_aura(overrides={
        assets_path: httpx.Response(200, json={'assets': [], 'next_page_cursor': None}),
    })

    rc = cli.run_sync(str(upload_dir), 'Fake', apply=True, yes=True, aura=aura, debug=False)

    assert rc == 0
    out = capsys.readouterr().out
    assert 'Retries: 0 chunk(s) retried after a 401, 0 item(s) already landed' in out


# ---------------------------------------------------------------------------
# Task 3: extend the retry to the re-show and removal loops, and complete
# the exception hierarchy
# ---------------------------------------------------------------------------

def test_reshow_chunk_401_recovers_after_relogin_and_resend():
    plan = SyncPlan(to_upload=[], to_delete=[], to_reshow=[_asset('r1'), _asset('r2')])

    select_asset_handler = _sequenced_responses(
        httpx.Response(401, json={'error': 'unauthorized'}),
        httpx.Response(200, json={'number_failed': 0}),
    )
    aura = offline_aura(overrides={SELECT_ASSET_PATH: select_asset_handler})

    result = execute_plan(
        plan, aura, FRAME_ID, s3_client=_FakeS3Client(), sqs_client=_FakeSQSClient(),
        sleep=lambda *_: None, throttle_seconds=0, chunk_delay_seconds=0,
        relogin=lambda: None,
    )

    assert result.reshow_succeeded == 2
    assert result.reshow_failures == []
    assert result.chunks_retried == 1


def test_removal_chunk_hide_mode_401_recovers_after_relogin_and_resend():
    plan = SyncPlan(to_upload=[], to_delete=[_asset('a1'), _asset('a2')], to_reshow=[])

    exclude_handler = _sequenced_responses(
        httpx.Response(401, json={'error': 'unauthorized'}),
        httpx.Response(200, json={'number_failed': 0}),
    )
    aura = offline_aura(overrides={EXCLUDE_ASSET_PATH: exclude_handler})

    result = execute_plan(
        plan, aura, FRAME_ID, s3_client=_FakeS3Client(), sqs_client=_FakeSQSClient(),
        sleep=lambda *_: None, throttle_seconds=0, chunk_delay_seconds=0,
        relogin=lambda: None, removal_mode='hide',
    )

    assert result.delete_succeeded == 2
    assert result.delete_failures == []


def test_removal_chunk_second_401_attributes_all_and_makes_no_third_attempt():
    plan = SyncPlan(to_upload=[], to_delete=[_asset('a1'), _asset('a2')], to_reshow=[])

    exclude_calls: list = []

    def _exclude_always_401(request: httpx.Request) -> httpx.Response:
        exclude_calls.append(request)
        return httpx.Response(401, json={'error': 'unauthorized'})

    aura = offline_aura(overrides={EXCLUDE_ASSET_PATH: _exclude_always_401})

    result = execute_plan(
        plan, aura, FRAME_ID, s3_client=_FakeS3Client(), sqs_client=_FakeSQSClient(),
        sleep=lambda *_: None, throttle_seconds=0, chunk_delay_seconds=0,
        relogin=lambda: None, removal_mode='hide',
    )

    assert result.delete_succeeded == 0
    assert {aid for aid, _ in result.delete_failures} == {'a1', 'a2'}
    # First attempt + one retry -- no third attempt.
    assert len(exclude_calls) == 2


def test_rate_limit_error_is_auraerror_subclass_and_still_aborts_the_run(tmp_path):
    assert issubclass(RateLimitError, AuraError)
    assert issubclass(ConsecutiveWriteFailureError, AuraError)

    path = tmp_path / 'a.jpg'
    _write_jpeg(path)
    plan = SyncPlan(to_upload=[path], to_delete=[])

    aura = offline_aura(overrides={
        SELECT_ASSET_PATH: httpx.Response(429, json={'message': 'slow down'}),
    })

    with pytest.raises(RateLimitError):
        execute_plan(
            plan, aura, FRAME_ID, s3_client=_FakeS3Client(), sqs_client=_FakeSQSClient(),
            sleep=lambda *_: None, throttle_seconds=0, chunk_delay_seconds=0,
        )


def test_batch_update_and_delete_asset_raise_write_endpoint_error_on_error_envelope():
    aura_batch = offline_aura(overrides={
        BATCH_UPDATE_PATH: httpx.Response(200, json={'error': 'invalid'}),
    })
    partial = AssetPartial(local_identifier='local-id-1')
    with pytest.raises(WriteEndpointError):
        aura_batch.asset_api.batch_update(partial)

    delete_path = '/v5/assets/asset-1.json'
    aura_delete = offline_aura(overrides={
        delete_path: httpx.Response(200, json={'error': 'forbidden'}),
    })
    asset = Asset.model_construct(id='asset-1', local_identifier=None)
    with pytest.raises(WriteEndpointError):
        aura_delete.asset_api.delete_asset(asset)

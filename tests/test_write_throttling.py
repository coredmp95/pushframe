"""Offline tests for the write-call throttling and rate-limit batch-abort
behavior in `pushframe.sync.execute_plan` (select-asset-401-unauthorized
preventive fix, parts 1 & 3; rewritten for the batched write-path semantics,
quick task 260708-fyr).

Proves:
  * each upload CHUNK issues exactly 2 throttled write network calls
    (select_asset + batch_update, not one round-trip per file -- the double
    select_asset is gone);
  * each delete CHUNK issues exactly 1 throttled write network call
    (remove_asset);
  * `throttle_seconds=0` disables pacing entirely;
  * a `RateLimitError` from any write aborts the WHOLE batch (propagates,
    is not recorded as one of N per-item failures, and stops further items)
    -- the anti-abuse back-off behavior;
  * the consecutive-failure-run backstop still fires, now counting
    attributed per-file (upload) / per-chunk (delete) failures rather than
    per-round-trip failures.

All through `tests/offline.py`'s MockTransport harness + duck-typed S3/SQS
fakes: zero network, no AWS credentials, and an injected fake `sleep` so no
real time passes.
"""
import httpx
import pytest
from loguru import logger
from PIL import Image

from pushframe.client import RateLimitError
from pushframe.models.asset import Asset
from pushframe.sync import (
    ConsecutiveWriteFailureError,
    MAX_CONSECUTIVE_WRITE_FAILURES,
    SyncPlan,
    WRITE_CHUNK_DELAY_SECONDS,
    WRITE_THROTTLE_SECONDS,
    execute_plan,
)
from tests.offline import offline_aura

FRAME_ID = 'frame-fake-0001'
SELECT_ASSET_PATH = f'/v5/frames/{FRAME_ID}/select_asset.json'
REMOVE_ASSET_PATH = f'/v5/frames/{FRAME_ID}/remove_asset.json'
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


def _ok_overrides():
    return {
        SELECT_ASSET_PATH: httpx.Response(200, json={'number_failed': 0}),
        REMOVE_ASSET_PATH: httpx.Response(200, json={'number_failed': 0}),
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
    def get_queue_url(self, frame_id: str):
        return f'https://sqs.fake/{frame_id}'

    def receive_message(self, queue_url, wait_time_seconds=5):
        return {}


class _SleepRecorder:
    def __init__(self):
        self.calls = []

    def __call__(self, seconds):
        self.calls.append(seconds)


def _ack_all_batch_update(aura):
    """Install a batch_update fake that acknowledges every local_identifier
    it is sent -- the batched-mode equivalent of the always-succeeds
    MockTransport override, since a real per-payload echo can't be done via
    MockTransport's path-only routing."""
    from pushframe.api.assetApi import BatchUpdateResult
    from pushframe.models.asset import AssetPartialId

    def _fake(assets):
        items = assets if isinstance(assets, list) else [assets]
        ids = [item.local_identifier for item in items]
        successes = [{'id': f'new-{lid}', 'local_identifier': lid} for lid in ids]
        return BatchUpdateResult(ids, [AssetPartialId(**s) for s in successes], [])

    aura.asset_api.batch_update = _fake


# ---------------------------------------------------------------------------
# Throttling (part 1)
# ---------------------------------------------------------------------------

def test_one_upload_chunk_issues_two_throttled_write_calls(tmp_path):
    path_a = tmp_path / 'a.jpg'
    _write_jpeg(path_a)
    plan = SyncPlan(to_upload=[path_a], to_delete=[])
    sleep = _SleepRecorder()
    aura = offline_aura(overrides=_ok_overrides())
    _ack_all_batch_update(aura)

    execute_plan(
        plan, aura, FRAME_ID,
        s3_client=_FakeS3Client(), sqs_client=_FakeSQSClient(), sleep=sleep,
    )

    # One upload chunk issues 2 write network calls (select_asset +
    # batch_update -- the double select_asset is gone), each paced.
    assert sleep.calls == [WRITE_THROTTLE_SECONDS] * 2


def test_one_delete_chunk_issues_one_throttled_write_call():
    plan = SyncPlan(to_upload=[], to_delete=[_asset('x1'), _asset('x2')])
    sleep = _SleepRecorder()

    execute_plan(
        plan, offline_aura(overrides=_ok_overrides()), FRAME_ID,
        s3_client=_FakeS3Client(), sqs_client=_FakeSQSClient(), sleep=sleep,
    )

    # Both deletes chunk into ONE remove_asset call, paced once.
    assert sleep.calls == [WRITE_THROTTLE_SECONDS] * 1


def test_throttle_uses_supplied_interval(tmp_path):
    path_a = tmp_path / 'a.jpg'
    _write_jpeg(path_a)
    plan = SyncPlan(to_upload=[path_a], to_delete=[])
    sleep = _SleepRecorder()
    aura = offline_aura(overrides=_ok_overrides())
    _ack_all_batch_update(aura)

    execute_plan(
        plan, aura, FRAME_ID,
        s3_client=_FakeS3Client(), sqs_client=_FakeSQSClient(),
        throttle_seconds=2.5, sleep=sleep,
    )

    assert sleep.calls == [2.5, 2.5]


def test_throttle_seconds_zero_disables_sleeping(tmp_path):
    path_a = tmp_path / 'a.jpg'
    _write_jpeg(path_a)
    plan = SyncPlan(to_upload=[path_a], to_delete=[_asset('x1')])
    sleep = _SleepRecorder()
    aura = offline_aura(overrides=_ok_overrides())
    _ack_all_batch_update(aura)

    execute_plan(
        plan, aura, FRAME_ID,
        s3_client=_FakeS3Client(), sqs_client=_FakeSQSClient(),
        throttle_seconds=0, sleep=sleep,
        chunk_delay_seconds=0,  # isolate to the per-call throttle (this plan spans 2 chunks)
    )

    assert sleep.calls == []


# ---------------------------------------------------------------------------
# Rate-limit batch abort (part 3)
# ---------------------------------------------------------------------------

def test_rate_limited_upload_aborts_whole_batch(tmp_path):
    # 475 on select_asset must abort the entire apply, NOT be recorded as one
    # of N per-item upload failures (that was the confusing 120x-401 symptom).
    # Note: S3 prep happens per-file BEFORE the chunk's select_asset call in
    # the batched flow, so both files ARE uploaded to S3 before the abort --
    # unlike the old per-file flow, S3 upload count no longer proves "nothing
    # was attempted"; the batch_update call count does (never reached).
    path_a = tmp_path / 'a.jpg'
    path_b = tmp_path / 'b.jpg'
    _write_jpeg(path_a)
    _write_jpeg(path_b)
    plan = SyncPlan(to_upload=[path_a, path_b], to_delete=[_asset('x1')])

    overrides = _ok_overrides()
    overrides[SELECT_ASSET_PATH] = httpx.Response(475, json={'message': 'locked out'})
    aura = offline_aura(overrides=overrides)
    batch_update_calls: list = []
    original_batch_update = aura.asset_api.batch_update

    def _counting_batch_update(assets):
        batch_update_calls.append(assets)
        return original_batch_update(assets)

    aura.asset_api.batch_update = _counting_batch_update

    with pytest.raises(RateLimitError):
        execute_plan(
            plan, aura, FRAME_ID,
            s3_client=_FakeS3Client(), sqs_client=_FakeSQSClient(),
            throttle_seconds=0, sleep=lambda *_: None,
        )

    # Aborted at the chunk's select_asset -- batch_update never reached.
    assert batch_update_calls == []


def test_rate_limited_delete_aborts_and_does_not_record_per_item(monkeypatch):
    plan = SyncPlan(to_upload=[], to_delete=[_asset('x1'), _asset('x2')])

    overrides = _ok_overrides()
    overrides[REMOVE_ASSET_PATH] = httpx.Response(429, headers={'Retry-After': '60'},
                                                  json={'message': 'too many'})

    with pytest.raises(RateLimitError) as exc_info:
        execute_plan(
            plan, offline_aura(overrides=overrides), FRAME_ID,
            s3_client=_FakeS3Client(), sqs_client=_FakeSQSClient(),
            throttle_seconds=0, sleep=lambda *_: None,
            removal_mode='delete',  # pinned: this test rate-limits remove_asset
        )

    assert exc_info.value.status_code == 429
    assert exc_info.value.retry_after == 60


def test_ordinary_per_item_failure_still_recorded_not_aborted(tmp_path):
    # A non-rate-limit failure (nonzero number_failed) must still be caught
    # at the chunk level and the loop continue -- the D-08 fail-loud-but-
    # continue behavior is preserved. select_asset's number_failed is a
    # count-only signal (batched), so it attributes ALL prepped files in the
    # chunk as failed rather than a single item.
    path_a = tmp_path / 'a.jpg'
    path_b = tmp_path / 'b.jpg'
    _write_jpeg(path_a)
    _write_jpeg(path_b)
    plan = SyncPlan(to_upload=[path_a, path_b], to_delete=[])

    overrides = _ok_overrides()
    overrides[SELECT_ASSET_PATH] = httpx.Response(200, json={'number_failed': 1})

    result = execute_plan(
        plan, offline_aura(overrides=overrides), FRAME_ID,
        s3_client=_FakeS3Client(), sqs_client=_FakeSQSClient(),
        throttle_seconds=0, sleep=lambda *_: None,
    )

    # Both items failed (chunk-level attribution, not aborted).
    assert result.upload_succeeded == 0
    assert len(result.upload_failures) == 2


# ---------------------------------------------------------------------------
# Consecutive-failure-run backstop (REOPENED-gap fix)
#
# The anti-abuse trip did NOT always announce itself with the 429/475 that
# RateLimitError catches -- the live regression saw a plain HTTP 401 on every
# write after the 7th succeeded, so execute_plan caught each of the 103
# post-trip failures per-item and kept hammering. These prove a RUN of N
# consecutive write failures (status-code agnostic) aborts the batch, while an
# isolated failure (surrounded by successes) never does. In the batched flow,
# a single fully-failed CHUNK attributes all its files as failures in one
# pass, so the run can cross the threshold within one chunk.
# ---------------------------------------------------------------------------

def _write_jpegs(tmp_path, n):
    paths = []
    for i in range(n):
        p = tmp_path / f'{i:02d}.jpg'
        _write_jpeg(p)
        paths.append(p)
    return paths


def test_run_of_plain_401_write_failures_aborts_batch(tmp_path):
    # The exact live regression: plain HTTP 401 (NOT 429/475, NOT a
    # RateLimitError) on select_asset. A run of attributed failures must
    # abort the whole batch after MAX_CONSECUTIVE_WRITE_FAILURES rather than
    # dutifully failing all N items one by one. With a single chunk of 7
    # files, select_asset's 401 (an httpx.HTTPStatusError, not a
    # RateLimitError) attributes files as failed in prepped order until the
    # backstop trips at exactly MAX.
    paths = _write_jpegs(tmp_path, 7)
    plan = SyncPlan(to_upload=paths, to_delete=[])

    overrides = _ok_overrides()
    overrides[SELECT_ASSET_PATH] = httpx.Response(401, json={'error': 'unauthorized'})
    s3 = _FakeS3Client()

    aura = offline_aura(overrides=overrides)
    with pytest.raises(ConsecutiveWriteFailureError) as exc_info:
        execute_plan(
            plan, aura, FRAME_ID,
            s3_client=s3, sqs_client=_FakeSQSClient(),
            throttle_seconds=0, sleep=lambda *_: None,
            relogin=aura.login,  # pinned: pre-phase-24 password default
        )

    err = exc_info.value
    assert err.count == MAX_CONSECUTIVE_WRITE_FAILURES
    # Aborted after exactly N attributed failures -- the remaining 2 files
    # in the chunk were prepped/S3-uploaded (S3 prep precedes the chunk's
    # select_asset in the batched flow) but never acknowledged/attributed.
    assert len(err.result.upload_failures) == MAX_CONSECUTIVE_WRITE_FAILURES
    # No file was ever acknowledged as succeeded.
    assert err.result.upload_succeeded == 0
    # The distinct message, not the RateLimitError "Retry after" wording.
    assert 'consecutive write failures' in str(err)


def test_interspersed_failures_do_not_trip_the_backstop(tmp_path, monkeypatch):
    # Failures scattered among successes (max run of 1) must NEVER abort -- an
    # isolated bad/expired asset ref or a one-off permissions edge is not a
    # lockout. 8 uploads split into 8 single-file chunks (batch_size=1) so
    # each chunk's batch_update outcome is independently controllable, odd
    # chunks failing so failures never run 2-in-a-row.
    paths = _write_jpegs(tmp_path, 8)
    plan = SyncPlan(to_upload=paths, to_delete=[])
    aura = offline_aura(overrides=_ok_overrides())

    from pushframe.api.assetApi import BatchUpdateResult
    from pushframe.models.asset import AssetPartialId

    def _flaky_batch_update(assets):
        items = assets if isinstance(assets, list) else [assets]
        assert len(items) == 1
        item = items[0]
        n = int(item.file_name.split('-')[1].split('.')[0])
        if n % 2 == 1:
            raise RuntimeError('isolated per-item failure')
        return BatchUpdateResult(
            [item.local_identifier],
            [AssetPartialId(id=f'new-{n}', local_identifier=item.local_identifier)],
            [],
        )

    monkeypatch.setattr(aura.asset_api, 'batch_update', _flaky_batch_update)

    result =        execute_plan(
            plan, aura, FRAME_ID,
            s3_client=_FakeS3Client(), sqs_client=_FakeSQSClient(),
            throttle_seconds=0, sleep=lambda *_: None,
            batch_size=1,
            relogin=aura.login,  # pinned: pre-phase-24 password default
        )

    # No abort: all 8 attempted, 4 succeeded, 4 recorded per-item (D-08 intact).
    assert result.upload_succeeded == 4
    assert len(result.upload_failures) == 4


def test_a_success_resets_the_consecutive_run(tmp_path, monkeypatch):
    # A run of 4 failures, then ONE success, then 4 more failures must NOT
    # abort even though 8 total failures occur -- only an unbroken run of N
    # trips it, so the counter must reset on success. Chunked at
    # batch_size=1 so each chunk's outcome is independently controllable.
    paths = _write_jpegs(tmp_path, 9)
    plan = SyncPlan(to_upload=paths, to_delete=[])
    aura = offline_aura(overrides=_ok_overrides())

    from pushframe.api.assetApi import BatchUpdateResult
    from pushframe.models.asset import AssetPartialId

    def _flaky_batch_update(assets):
        items = assets if isinstance(assets, list) else [assets]
        item = items[0]
        n = int(item.file_name.split('-')[1].split('.')[0])
        # Uploads 1-4 fail, upload 5 succeeds (reset), uploads 6-9 fail.
        if n != 5:
            raise RuntimeError('per-item failure')
        return BatchUpdateResult(
            [item.local_identifier],
            [AssetPartialId(id='new-5', local_identifier=item.local_identifier)],
            [],
        )

    monkeypatch.setattr(aura.asset_api, 'batch_update', _flaky_batch_update)

    result =        execute_plan(
            plan, aura, FRAME_ID,
            s3_client=_FakeS3Client(), sqs_client=_FakeSQSClient(),
            throttle_seconds=0, sleep=lambda *_: None,
            batch_size=1,
            relogin=aura.login,  # pinned: pre-phase-24 password default
        )

    assert result.upload_succeeded == 1
    assert len(result.upload_failures) == 8


def test_all_files_unacknowledged_in_one_chunk_aborts_without_double_counting(tmp_path):
    # A chunk where batch_update returns successfully (no raised exception)
    # but acknowledges NONE of the sent local_identifiers must abort via the
    # SAME note_failure()-raises-mid-attribution-loop path as an explicit
    # per-item failure -- and must NOT be mistaken for a whole-chunk Pushd
    # failure (that sibling except branch would otherwise re-catch the
    # ConsecutiveWriteFailureError raised here and double-attribute every
    # prepped file). Exactly MAX_CONSECUTIVE_WRITE_FAILURES failures must be
    # recorded, not 2x MAX.
    paths = _write_jpegs(tmp_path, MAX_CONSECUTIVE_WRITE_FAILURES)
    plan = SyncPlan(to_upload=paths, to_delete=[])
    aura = offline_aura(overrides=_ok_overrides())

    def _no_one_acknowledged(assets):
        from pushframe.api.assetApi import BatchUpdateResult
        items = assets if isinstance(assets, list) else [assets]
        ids = [item.local_identifier for item in items]
        return BatchUpdateResult(ids, [], list(ids))

    aura.asset_api.batch_update = _no_one_acknowledged

    with pytest.raises(ConsecutiveWriteFailureError) as exc_info:
        execute_plan(
            plan, aura, FRAME_ID,
            s3_client=_FakeS3Client(), sqs_client=_FakeSQSClient(),
            throttle_seconds=0, sleep=lambda *_: None,
        )

    err = exc_info.value
    assert err.count == MAX_CONSECUTIVE_WRITE_FAILURES
    assert len(err.result.upload_failures) == MAX_CONSECUTIVE_WRITE_FAILURES
    assert err.result.upload_succeeded == 0


def test_max_consecutive_failures_zero_disables_the_backstop(tmp_path):
    # 0 restores the pure unbounded per-item D-08 behaviour: every item is
    # attempted and recorded, no ConsecutiveWriteFailureError.
    paths = _write_jpegs(tmp_path, 6)
    plan = SyncPlan(to_upload=paths, to_delete=[])

    overrides = _ok_overrides()
    overrides[SELECT_ASSET_PATH] = httpx.Response(401, json={'error': 'unauthorized'})

    aura = offline_aura(overrides=overrides)
    result = execute_plan(
        plan, aura, FRAME_ID,
        s3_client=_FakeS3Client(), sqs_client=_FakeSQSClient(),
        throttle_seconds=0, sleep=lambda *_: None,
        max_consecutive_failures=0,
        relogin=aura.login,  # pinned: pre-phase-24 password default
    )

    assert result.upload_succeeded == 0
    assert len(result.upload_failures) == 6


def test_consecutive_run_spans_upload_and_delete_phases(tmp_path):
    # The run counter carries across the upload->delete boundary (reset only
    # on a success): 3 upload failures (below threshold, single chunk) then
    # delete failures push the run to N and abort in the delete loop.
    # Deletes are chunked at batch_size=1 so the abort lands mid-loop,
    # leaving the 3rd delete unattempted (matching the original per-item
    # partial-abort assertion).
    paths = _write_jpegs(tmp_path, 3)
    plan = SyncPlan(to_upload=paths, to_delete=[_asset('d1'), _asset('d2'), _asset('d3')])

    overrides = _ok_overrides()
    overrides[SELECT_ASSET_PATH] = httpx.Response(401, json={'error': 'unauthorized'})
    overrides[REMOVE_ASSET_PATH] = httpx.Response(401, json={'error': 'unauthorized'})

    aura = offline_aura(overrides=overrides)
    with pytest.raises(ConsecutiveWriteFailureError) as exc_info:
        execute_plan(
            plan, aura, FRAME_ID,
            s3_client=_FakeS3Client(), sqs_client=_FakeSQSClient(),
            throttle_seconds=0, sleep=lambda *_: None,
            batch_size=1,
            relogin=aura.login,  # pinned: pre-phase-24 password default
        )

    err = exc_info.value
    assert err.count == MAX_CONSECUTIVE_WRITE_FAILURES
    # 3 uploads failed (run 1-3), then 2 deletes failed (run 4-5 -> abort);
    # the 3rd delete is never attempted.
    assert len(err.result.upload_failures) == 3
    assert len(err.result.delete_failures) == 2


# ---------------------------------------------------------------------------
# Inter-chunk pacing (WRITE_CHUNK_DELAY_SECONDS)
# ---------------------------------------------------------------------------

class _WaitRecorder:
    def __init__(self):
        self.calls = []

    def __call__(self, remaining):
        self.calls.append(remaining)


def test_interchunk_pause_runs_between_upload_chunks(tmp_path):
    # With >1 chunk, a chunk_delay pause runs BETWEEN chunks: realized via the
    # injected sleep in 1s steps, surfacing a per-second on_wait countdown.
    # throttle_seconds=0 isolates the assertion to the inter-chunk pause only.
    paths = _write_jpegs(tmp_path, 4)  # batch_size=2 -> 2 chunks -> 1 gap
    plan = SyncPlan(to_upload=paths, to_delete=[])
    aura = offline_aura(overrides=_ok_overrides())
    _ack_all_batch_update(aura)
    sleep = _SleepRecorder()
    waits = _WaitRecorder()

    result = execute_plan(
        plan, aura, FRAME_ID,
        s3_client=_FakeS3Client(), sqs_client=_FakeSQSClient(),
        throttle_seconds=0, sleep=sleep, batch_size=2,
        chunk_delay_seconds=5.0, on_wait=waits,
    )

    assert result.upload_succeeded == 4
    # One 5s gap between the two chunks -> 5 one-second sleeps, no throttle noise.
    assert sleep.calls == [1.0] * 5
    # The countdown is surfaced each second, high to low, for the UI to render.
    assert waits.calls == [5.0, 4.0, 3.0, 2.0, 1.0]


def test_no_interchunk_pause_before_the_first_chunk(tmp_path):
    # A single chunk means no inter-chunk gap at all -- the pause is skipped
    # before the very first write chunk of the run.
    paths = _write_jpegs(tmp_path, 2)  # batch_size=2 -> exactly 1 chunk
    plan = SyncPlan(to_upload=paths, to_delete=[])
    aura = offline_aura(overrides=_ok_overrides())
    _ack_all_batch_update(aura)
    sleep = _SleepRecorder()
    waits = _WaitRecorder()

    execute_plan(
        plan, aura, FRAME_ID,
        s3_client=_FakeS3Client(), sqs_client=_FakeSQSClient(),
        throttle_seconds=0, sleep=sleep, batch_size=2,
        chunk_delay_seconds=5.0, on_wait=waits,
    )

    assert sleep.calls == []
    assert waits.calls == []


def test_chunk_delay_seconds_zero_disables_the_interchunk_pause(tmp_path):
    # chunk_delay_seconds=0 disables the pause even across multiple chunks.
    paths = _write_jpegs(tmp_path, 4)  # batch_size=2 -> 2 chunks
    plan = SyncPlan(to_upload=paths, to_delete=[])
    aura = offline_aura(overrides=_ok_overrides())
    _ack_all_batch_update(aura)
    sleep = _SleepRecorder()
    waits = _WaitRecorder()

    execute_plan(
        plan, aura, FRAME_ID,
        s3_client=_FakeS3Client(), sqs_client=_FakeSQSClient(),
        throttle_seconds=0, sleep=sleep, batch_size=2,
        chunk_delay_seconds=0, on_wait=waits,
    )

    assert sleep.calls == []
    assert waits.calls == []


def test_interchunk_pause_defaults_to_write_chunk_delay_seconds(tmp_path):
    # The default (no chunk_delay_seconds arg) paces at WRITE_CHUNK_DELAY_SECONDS.
    paths = _write_jpegs(tmp_path, 4)  # batch_size=2 -> 2 chunks -> 1 gap
    plan = SyncPlan(to_upload=paths, to_delete=[])
    aura = offline_aura(overrides=_ok_overrides())
    _ack_all_batch_update(aura)
    sleep = _SleepRecorder()

    execute_plan(
        plan, aura, FRAME_ID,
        s3_client=_FakeS3Client(), sqs_client=_FakeSQSClient(),
        throttle_seconds=0, sleep=sleep, batch_size=2,
    )

    assert sleep.calls == [1.0] * int(WRITE_CHUNK_DELAY_SECONDS)

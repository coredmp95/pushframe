"""Regression tests for the debug session breaker-counts-per-item-errors
(venus 2026-10-20, pushframe 5.1.38).

The lockout breaker (`MAX_CONSECUTIVE_WRITE_FAILURES`) aborted a 2231-upload
google-sync apply with 0 items written and the account-lockout advice. Its
"Last error" was `Unsupported image format: GIF (...)`: `_prep_upload`'s
LOCAL decode step rejects formats outside JPEG/PNG/HEIF before any S3/Pushd
call, and the prep loop fed that failure to the status-agnostic run counter.
Because successes are only credited after the chunk's `batch_update`, any
five such items inside one chunk tripped it -- they did not even need to be
adjacent.

The contract pinned here:
  * a failure of the item's OWN content (raised before any network call) is
    classified per-item by `classify_write_failure`: recorded in
    `ExecutionResult.upload_skipped`, reported via progress/on_error, and it
    neither increments nor resets the consecutive-failure run;
  * every failure that touched the write surface (Pushd chunk errors, S3
    upload errors) stays systemic and still trips the breaker;
  * GIF is reduced to its first frame re-encoded as JPEG (the MPO path), so
    the venus album's GIFs now upload.

Offline only: MockTransport-backed `offline_aura` + duck-typed S3/SQS fakes.
"""
import io

import httpx
import pytest
from loguru import logger
from PIL import Image

from pushframe.sync import (
    ConsecutiveWriteFailureError,
    MAX_CONSECUTIVE_WRITE_FAILURES,
    SyncPlan,
    execute_plan,
)
from tests.offline import offline_aura
from tests.test_execute_plan import (
    FRAME_ID,
    SELECT_ASSET_PATH,
    _default_overrides,
    _FakeS3Client,
    _FakeSQSClient,
    _install_ack_all_batch_update,
    _install_select_asset_recorder,
)


@pytest.fixture(autouse=True)
def _reset_loguru():
    logger.remove()
    yield
    logger.remove()


def _write(path, fmt, color=(10, 20, 30)):
    Image.new('RGB', (4, 4), color).save(path, format=fmt)
    return path


def _animated_gif(path, first=(200, 0, 0), second=(0, 0, 200)):
    a = Image.new('RGB', (6, 4), first)
    b = Image.new('RGB', (6, 4), second)
    a.save(path, format='GIF', save_all=True, append_images=[b], duration=100, loop=0)
    return path


def _run(plan, aura, s3=None, **kwargs):
    return execute_plan(
        plan, aura, FRAME_ID,
        s3_client=s3 if s3 is not None else _FakeS3Client(),
        sqs_client=_FakeSQSClient(), sleep=lambda *_: None,
        throttle_seconds=0, chunk_delay_seconds=0, **kwargs)


# --- 1. the venus symptom: unsupported content never trips the breaker -----

# Boundary neighbours around the threshold: one below, exactly at, one above,
# and well past twice the threshold (the run would have tripped twice over).
@pytest.mark.parametrize('n_unsupported', [
    MAX_CONSECUTIVE_WRITE_FAILURES - 1,
    MAX_CONSECUTIVE_WRITE_FAILURES,
    MAX_CONSECUTIVE_WRITE_FAILURES + 1,
    2 * MAX_CONSECUTIVE_WRITE_FAILURES + 2,
])
def test_unsupported_items_in_one_chunk_never_trip_the_breaker(tmp_path, n_unsupported):
    # WebP stays deliberately unsupported (D-12), so it is the stand-in for
    # "a format pushframe cannot upload". Named like the Google cache files
    # (media id, no suffix) and sorted FIRST so they open the chunk.
    unsupported = [_write(tmp_path / f'AF1Qip{i:03d}', 'WEBP') for i in range(n_unsupported)]
    good = [_write(tmp_path / f'ZZgood{i}', 'JPEG') for i in range(2)]
    plan = SyncPlan(to_upload=unsupported + good)
    aura = offline_aura(overrides=_default_overrides())
    _install_ack_all_batch_update(aura)
    s3 = _FakeS3Client()

    result = _run(plan, aura, s3=s3)

    assert result.upload_succeeded == 2
    assert result.upload_failures == []          # not "failed": skipped
    assert sorted(p for p, _ in result.upload_skipped) == sorted(unsupported)
    assert all('WEBP' in reason for _, reason in result.upload_skipped)
    assert len(s3.upload_calls) == 2             # nothing sent for a skip


def test_interleaved_unsupported_items_in_one_chunk_never_trip(tmp_path):
    # repro2: G J G J ... inside ONE chunk -- successes are credited only
    # after batch_update, so adjacency was never required to trip it.
    paths = []
    for i in range(11):
        fmt = 'WEBP' if i % 2 == 0 else 'JPEG'
        paths.append(_write(tmp_path / f'AF1Qip{i:03d}', fmt))
    aura = offline_aura(overrides=_default_overrides())
    _install_ack_all_batch_update(aura)

    result = _run(SyncPlan(to_upload=paths), aura)

    assert result.upload_succeeded == 5
    assert len(result.upload_skipped) == 6
    assert result.upload_failures == []


def test_chunk_of_only_unsupported_items_writes_nothing_and_does_not_abort(tmp_path):
    paths = [_write(tmp_path / f'AF1Qip{i:03d}', 'WEBP') for i in range(7)]
    aura = offline_aura(overrides=_default_overrides())
    select_calls = _install_select_asset_recorder(aura)
    batch_calls = _install_ack_all_batch_update(aura)

    result = _run(SyncPlan(to_upload=paths), aura)

    assert len(result.upload_skipped) == 7
    assert result.upload_succeeded == 0
    assert select_calls == [] and batch_calls == []   # zero Pushd writes


def test_undecodable_file_is_a_per_item_skip(tmp_path):
    empty = tmp_path / 'AF1Qip000'
    empty.write_bytes(b'')
    truncated = tmp_path / 'AF1Qip001'
    truncated.write_bytes(_write(tmp_path / 'src.jpg', 'JPEG').read_bytes()[:8])
    many = [empty, truncated] + [
        (tmp_path / f'AF1Qip{i:03d}') for i in range(2, 2 + MAX_CONSECUTIVE_WRITE_FAILURES)]
    for p in many[2:]:
        p.write_bytes(b'not an image at all')
    aura = offline_aura(overrides=_default_overrides())
    _install_ack_all_batch_update(aura)

    result = _run(SyncPlan(to_upload=many), aura)

    assert len(result.upload_skipped) == len(many)
    assert result.upload_failures == []


def test_skip_is_reported_through_progress_and_on_error(tmp_path):
    bad = _write(tmp_path / 'AF1Qip000', 'WEBP')
    good = _write(tmp_path / 'AF1Qip001', 'JPEG')
    aura = offline_aura(overrides=_default_overrides())
    _install_ack_all_batch_update(aura)
    progress, errors = [], []

    _run(SyncPlan(to_upload=[bad, good]), aura,
         progress=lambda *a: progress.append(a),
         on_error=lambda *a: errors.append(a))

    assert ('upload', bad, False) in progress
    assert ('upload', good, True) in progress
    assert len(progress) == 2                    # one call per resolved item
    assert errors and errors[0][1] == bad and 'WEBP' in errors[0][3]


# --- 2. systemic failures still feed the breaker ---------------------------

def test_skips_neither_increment_nor_reset_the_systemic_run(tmp_path, monkeypatch):
    # 4 systemic chunk failures, then a content skip, then a 5th systemic
    # failure: the skip carries no information about the account -- it must
    # not count (or the run would trip one item early) and must not reset
    # (or a lockout interleaved with odd files would never be caught).
    paths = [_write(tmp_path / f'{i:02d}.jpg', 'JPEG') for i in range(4)]
    paths.append(_write(tmp_path / '04.jpg', 'WEBP'))         # mislabeled WebP
    paths.append(_write(tmp_path / '05.jpg', 'JPEG'))
    paths.append(_write(tmp_path / '06.jpg', 'JPEG'))          # never reached
    aura = offline_aura(overrides=_default_overrides())

    def _down(assets):
        raise RuntimeError('batch_update exploded (systemic)')

    monkeypatch.setattr(aura.asset_api, 'batch_update', _down)

    with pytest.raises(ConsecutiveWriteFailureError) as exc_info:
        _run(SyncPlan(to_upload=paths), aura, batch_size=1)

    err = exc_info.value
    assert err.count == MAX_CONSECUTIVE_WRITE_FAILURES
    assert [p.name for p, _ in err.result.upload_failures] == [
        '00.jpg', '01.jpg', '02.jpg', '03.jpg', '05.jpg']
    assert [p.name for p, _ in err.result.upload_skipped] == ['04.jpg']


def test_run_of_plain_401_still_trips_with_unsupported_items_present(tmp_path):
    # The breaker's raison d'etre (select-asset-401-unauthorized) is intact
    # even when the chunk also carries unsupported items.
    paths = [_write(tmp_path / f'{i:02d}.jpg', 'JPEG') for i in range(7)]
    paths += [_write(tmp_path / f'{i:02d}.jpg', 'WEBP') for i in range(7, 10)]
    overrides = _default_overrides()
    overrides[SELECT_ASSET_PATH] = httpx.Response(401, json={'error': 'unauthorized'})
    aura = offline_aura(overrides=overrides)

    with pytest.raises(ConsecutiveWriteFailureError) as exc_info:
        _run(SyncPlan(to_upload=paths), aura, relogin=aura.login)

    assert exc_info.value.count == MAX_CONSECUTIVE_WRITE_FAILURES
    assert 'Unsupported' not in exc_info.value.last_error


def test_s3_upload_failures_stay_systemic(tmp_path):
    # S3 is a network write: a run of S3 failures is a systemic signal and
    # keeps tripping the breaker (ExpiredToken gets its own class in the
    # sibling session s3-expiredtoken-long-run).
    paths = [_write(tmp_path / f'{i:02d}.jpg', 'JPEG') for i in range(6)]
    aura = offline_aura(overrides=_default_overrides())

    class _BrokenS3:
        def upload_file(self, data, extension):
            raise RuntimeError('S3 PutObject failed')

    with pytest.raises(ConsecutiveWriteFailureError) as exc_info:
        _run(SyncPlan(to_upload=paths), aura, s3=_BrokenS3())

    assert exc_info.value.count == MAX_CONSECUTIVE_WRITE_FAILURES
    assert exc_info.value.result.upload_skipped == []


# --- 3. the classification seam --------------------------------------------

def test_classify_write_failure_seam():
    from pushframe.sync import (
        FAILURE_PER_ITEM,
        FAILURE_SYSTEMIC,
        ItemContentError,
        classify_write_failure,
    )
    req = httpx.Request('POST', 'https://api.pushd.com/v5/assets/batch_update.json')
    http_401 = httpx.HTTPStatusError(
        '401', request=req, response=httpx.Response(401, request=req))

    assert classify_write_failure(ItemContentError('Unsupported image format: GIF')) == FAILURE_PER_ITEM
    # Only the typed, pre-network content error is per-item: a bare
    # ValueError / RuntimeError / HTTP error from anywhere else is systemic.
    assert classify_write_failure(ValueError('x')) == FAILURE_SYSTEMIC
    assert classify_write_failure(RuntimeError('x')) == FAILURE_SYSTEMIC
    assert classify_write_failure(http_401) == FAILURE_SYSTEMIC
    # ItemContentError stays a ValueError (pre-existing callers matching
    # ValueError on _prep_upload keep working).
    assert issubclass(ItemContentError, ValueError)


# --- 4. GIF -> first frame as JPEG (the venus album's actual content) ------

def test_gif_is_reduced_to_its_first_frame_as_jpeg(tmp_path):
    from pushframe.sync import _prep_upload
    path = _animated_gif(tmp_path / 'AF1QipANIM')
    with Image.open(path) as check:
        assert check.format == 'GIF' and check.n_frames == 2

    s3 = _FakeS3Client()
    _lid, partial = _prep_upload(path, s3)

    assert partial.data_uti == 'public.jpeg'
    assert (partial.width, partial.height) == (6, 4)
    uploaded, _ext = s3.upload_calls[0]
    assert uploaded != path.read_bytes()
    with Image.open(io.BytesIO(uploaded)) as im:
        assert im.format == 'JPEG'
        assert getattr(im, 'n_frames', 1) == 1
        r, g, b = im.convert('RGB').getpixel((0, 0))
        assert r > 150 and b < 80                 # the FIRST (red) frame


def test_venus_shape_five_gifs_in_a_chunk_all_upload(tmp_path):
    gifs = [_animated_gif(tmp_path / f'AF1Qip{i:03d}') for i in range(5)]
    jpeg = _write(tmp_path / 'AF1Qip999', 'JPEG')
    aura = offline_aura(overrides=_default_overrides())
    _install_ack_all_batch_update(aura)

    result = _run(SyncPlan(to_upload=gifs + [jpeg]), aura)

    assert result.upload_succeeded == 6
    assert result.upload_skipped == [] and result.upload_failures == []
    # Reduced containers expose the md5 of the bytes actually sent (gsync
    # persists THAT in the manifest -- the MPO contract).
    assert set(result.uploaded_md5_by_path) == {str(p) for p in gifs + [jpeg]}

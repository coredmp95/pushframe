"""Offline tests for `pushframe.sync._prep_upload`'s content-derived
`data_uti` (Phase 11 Plan 04, FMT-01/FMT-03, D-11/D-12).

`data_uti` is resolved from the DECODED image's real format
(`image.format`, read from the same `Image.open()` call `_prep_upload`
already performs for dimensions) -- never from the file name or its
extension. Covers every supported format (JPEG/PNG/HEIF), a mislabeled
file (bytes decide over name), an explicitly unmapped format (WebP,
D-12), and undecodable files (zero-byte, truncated) each failing closed
as a single per-file attribution rather than blocking the rest of a
chunk.
"""
from pathlib import Path

import pytest
from loguru import logger
from PIL import Image

from pushframe.sync import SyncPlan, _prep_upload, execute_plan
from tests.offline import offline_aura
from tests.test_execute_plan import (
    FRAME_ID,
    _default_overrides,
    _FakeS3Client,
    _FakeSQSClient,
    _install_ack_all_batch_update,
)


@pytest.fixture(autouse=True)
def _reset_loguru():
    # loguru's `logger` is a process-global singleton -- see
    # tests/test_execute_plan.py's identical rationale.
    logger.remove()
    yield
    logger.remove()


def _write_image(path: Path, fmt: str, color=(1, 2, 3)):
    Image.new('RGB', (4, 4), color).save(path, format=fmt)


# --- Behaviors 1-4: supported formats + mislabeled file (D-11) --------------

def test_jpeg_produces_public_jpeg(tmp_path):
    path = tmp_path / 'photo.jpg'
    _write_image(path, 'JPEG')
    s3 = _FakeS3Client()

    local_identifier, partial = _prep_upload(path, s3)

    assert partial.data_uti == 'public.jpeg'


def test_png_produces_public_png_and_real_dimensions(tmp_path):
    path = tmp_path / 'photo.png'
    Image.new('RGB', (7, 3), (10, 20, 30)).save(path, format='PNG')
    s3 = _FakeS3Client()

    local_identifier, partial = _prep_upload(path, s3)

    assert partial.data_uti == 'public.png'
    assert partial.width == 7
    assert partial.height == 3


def test_heic_produces_public_heic_and_untouched_bytes(tmp_path):
    path = tmp_path / 'photo.heic'
    _write_image(path, 'HEIF')
    s3 = _FakeS3Client()

    local_identifier, partial = _prep_upload(path, s3)

    assert partial.data_uti == 'public.heic'
    # Nothing was transcoded -- the exact bytes handed to S3 equal the
    # source file's bytes, byte-for-byte (D-09: the md5_hash diffing
    # contract depends on this).
    assert len(s3.upload_calls) == 1
    uploaded_bytes, uploaded_ext = s3.upload_calls[0]
    assert uploaded_bytes == path.read_bytes()
    assert uploaded_ext == '.heic'


def test_mislabeled_png_named_jpg_produces_public_png(tmp_path):
    # The bytes decide, not the name (D-11): a PNG saved to a `.jpg`-named
    # path must still be typed public.png.
    path = tmp_path / 'photo.jpg'
    Image.new('RGB', (4, 4), (5, 6, 7)).save(path, format='PNG')
    s3 = _FakeS3Client()

    local_identifier, partial = _prep_upload(path, s3)

    assert partial.data_uti == 'public.png'


# --- Behavior 5: unmapped decoded format fails closed (D-12) ---------------

def test_mpo_is_reduced_to_its_first_frame_as_jpeg(tmp_path):
    """MPO files (stereo/3D JPEG containers, live-proven 2026-10-01 on two
    Google Photos items) used to fail closed as unmapped; the frame is a 2D
    display, so the FIRST view is what should upload -- re-encoded as plain
    JPEG with the `public.jpeg` UTI (the md5 travels inside the fake, so the
    bytes-level check below is the honest one)."""
    import io

    # Build a real 2-frame MPO: PIL writes the MPF multi-picture container
    # when saving MPO with append_images.
    primary = Image.new('RGB', (6, 4), (10, 20, 30))
    second = Image.new('RGB', (6, 4), (200, 100, 50))
    path = tmp_path / 'stereo.jpg'
    primary.save(path, format='MPO', save_all=True,
                 append_images=[second])

    # Sanity: PIL really decoded it as MPO with 2 frames.
    with Image.open(path) as check:
        assert check.format == 'MPO'
        assert getattr(check, 'n_frames', 1) == 2

    s3 = _FakeS3Client()
    local_identifier, partial = _prep_upload(path, s3)

    assert partial.data_uti == 'public.jpeg'
    assert (partial.width, partial.height) == (6, 4)
    # The S3 payload is the RE-ENCODED first frame, not the container:
    uploaded_bytes, uploaded_ext = s3.upload_calls[0]
    assert uploaded_bytes != path.read_bytes()
    with Image.open(io.BytesIO(uploaded_bytes)) as uploaded:
        assert uploaded.format == 'JPEG'
        assert getattr(uploaded, 'n_frames', 1) == 1
        assert uploaded.size == (6, 4)


def test_webp_raises_valueerror_naming_decoded_format_and_uploads_nothing(tmp_path):
    path = tmp_path / 'photo.webp'
    _write_image(path, 'WEBP')
    s3 = _FakeS3Client()

    with pytest.raises(ValueError, match='WEBP'):
        _prep_upload(path, s3)

    assert s3.upload_calls == []


# --- Behavior 6: undecodable files fail closed per-file, not per-chunk ----

def test_zero_byte_file_raises_from_prep_upload(tmp_path):
    path = tmp_path / 'empty.jpg'
    path.write_bytes(b'')
    s3 = _FakeS3Client()

    with pytest.raises(Exception):
        _prep_upload(path, s3)

    assert s3.upload_calls == []


def test_truncated_jpeg_raises_from_prep_upload(tmp_path):
    good = tmp_path / 'good.jpg'
    _write_image(good, 'JPEG')
    path = tmp_path / 'truncated.jpg'
    # Take only the first few bytes of a real JPEG -- enough to look like
    # a JPEG by extension, not enough for Pillow to decode a header from.
    path.write_bytes(good.read_bytes()[:8])
    s3 = _FakeS3Client()

    with pytest.raises(Exception):
        _prep_upload(path, s3)

    assert s3.upload_calls == []


def test_mixed_chunk_bad_file_attributed_alone_good_file_still_uploads(tmp_path):
    # Drives the failure through the real caller loop (execute_plan), not
    # just _prep_upload directly -- proves the per-file try/except in the
    # upload chunk loop costs exactly one file, never the chunk.
    good_path = tmp_path / 'good.jpg'
    _write_image(good_path, 'JPEG')
    bad_path = tmp_path / 'empty.jpg'
    bad_path.write_bytes(b'')

    plan = SyncPlan(to_upload=[good_path, bad_path], to_delete=[])
    aura = offline_aura(overrides=_default_overrides())
    _install_ack_all_batch_update(aura)
    s3 = _FakeS3Client()
    sqs = _FakeSQSClient()

    result = execute_plan(
        plan, aura, FRAME_ID,
        s3_client=s3, sqs_client=sqs, sleep=lambda *_: None,
        throttle_seconds=0, chunk_delay_seconds=0,
    )

    assert result.upload_succeeded == 1
    assert len(result.upload_failures) == 1
    failed_path, _reason = result.upload_failures[0]
    assert failed_path == bad_path
    assert len(s3.upload_calls) == 1


# --- Behavior 7: decompression-bomb guard stays enabled --------------------

def test_max_image_pixels_guard_is_not_disabled():
    assert Image.MAX_IMAGE_PIXELS is not None

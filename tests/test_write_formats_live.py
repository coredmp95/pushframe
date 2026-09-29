import os
import random
import uuid

import pytest
from PIL import Image

from pushframe.aws.s3client import S3Client, get_md5
from pushframe.aws.sqsclient import SQSClient
from pushframe.models.asset import AssetPartialId
from pushframe.sync import SyncPlan, execute_plan


def _target_frame_id() -> str:
    """Frame id for this module's live write regression tests, read from an
    env var with a skip when unset -- mirrors conftest.py's credential-gated
    skip (D-02) so a checkout without this var configured stays green rather
    than failing on a frame the operator never authorized."""
    frame_id = os.getenv("AURA_TEST_FRAME")
    if not frame_id:
        pytest.skip("AURA_TEST_FRAME not set; skipping live write-format regression tests")
    return frame_id


def _round_trip_and_hide(aura, tmp_path, *, suffix: str, pil_format: str, expected_data_uti: str):
    """Shared body for the PNG/HEIC round-trip tests (D-05 disposable-test-
    asset methodology): upload one small, per-invocation-unique-colour image
    through the production `execute_plan` write path, assert it succeeded,
    re-fetch it by content hash and assert the server-recorded `data_uti`
    matches the decoded format (FMT-01/D-11), then hide it (reversible tier)
    so no live-test artifact is left visible on the frame.

    The pixel colour is randomised per call (not a fixed constant) so that
    re-running this test in a later CI/live run never re-encodes the exact
    same bytes as a still-present (hidden) asset from a prior run -- a fixed
    colour was tried first and failed exactly this way: the second run's
    `md5_hash` collided with the first run's already-uploaded asset, so the
    `len(matches) == 1` re-fetch assertion below found 2 instead of 1
    (confirmed live 2026-09-03, see 11-LIVE-FINDINGS.md).
    """
    frame_id = _target_frame_id()

    color = (random.randint(0, 255), random.randint(0, 255), random.randint(0, 255))
    image_path = tmp_path / f"gsd-11-05-live-{uuid.uuid4().hex[:8]}{suffix}"
    Image.new("RGB", (64, 64), color=color).save(image_path, format=pil_format)
    local_md5 = get_md5(image_path.read_bytes())

    plan = SyncPlan(to_upload=[image_path])
    result = execute_plan(
        plan, aura, frame_id, s3_client=S3Client(), sqs_client=SQSClient(),
    )

    assert result.upload_succeeded == 1, f"upload_failures={result.upload_failures}"
    assert result.upload_failures == []

    assets = aura.get_all_assets(frame_id)
    matches = [a for a in assets if a.md5_hash == local_md5]
    assert len(matches) == 1, (
        f"expected exactly one uploaded asset matching md5 {local_md5}, found {len(matches)}"
    )
    asset = matches[0]
    assert asset.data_uti == expected_data_uti, (
        f"expected data_uti={expected_data_uti!r}, got {asset.data_uti!r}"
    )

    # D-05: hide (reversible exclude_asset), never leave a live-test asset
    # visible on the frame -- this test's own cleanup, independent of any
    # other plan-05 sacrificial asset.
    aura.frame_api.exclude_asset(frame_id, AssetPartialId(id=asset.id))


@pytest.mark.live
def test_write_png_round_trips_and_data_uti_is_public_png(aura, tmp_path):
    """FMT-01/FMT-02: a real `.png` uploads end-to-end via `execute_plan`,
    round-trips through the server with `data_uti == 'public.png'`, and is
    left hidden (not deleted) afterward."""
    _round_trip_and_hide(
        aura, tmp_path, suffix=".png", pil_format="PNG", expected_data_uti="public.png",
    )


@pytest.mark.live
def test_write_heic_round_trips_and_data_uti_is_public_heic(aura, tmp_path):
    """FMT-03/D-10 (renders branch): a real `.heic` uploads end-to-end via
    `execute_plan`, round-trips through the server with
    `data_uti == 'public.heic'`, and is left hidden (not deleted) afterward.
    Live-verified 2026-09-03 on frame "Cadre de Fabrice": the operator
    confirmed both a solid-red PNG and a solid-blue HEIC render correctly,
    both in the Aura app's photo-library view (each image opened
    full-screen) and on the physical frame itself, appearing in the live
    slideshow rotation -- see 11-LIVE-FINDINGS.md. HEIC-as-is needs no code
    change; this test is the regression proof."""
    _round_trip_and_hide(
        aura, tmp_path, suffix=".heic", pil_format="HEIF", expected_data_uti="public.heic",
    )

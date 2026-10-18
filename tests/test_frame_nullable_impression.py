"""Regression for the venus `pushframe status` crash: the live API returns
`last_impression_at: null` for a frame that has never displayed an asset
(never-impressed frame), but `Frame` required the field as `str`, so
`FrameApi.get_frames()` died with a pydantic ValidationError instead of
listing the frames.

Unmarked (no @pytest.mark.live) — offline, zero network access.
"""
import json

import httpx

from pushframe.models.frame import Frame
from pushframe.cli import run_status
from tests.offline import FIXTURES_DIR, offline_aura


def _frames_payload_with_null_impression() -> str:
    """The exact drift observed on venus: a frame whose last_impression_at is null."""
    data = json.loads((FIXTURES_DIR / 'frames.json').read_text())
    assert data['frames'], 'fixture must contain a frame'
    data['frames'][0]['last_impression_at'] = None
    return json.dumps(data)


def test_frame_hydrates_with_null_last_impression_at():
    """Never-impressed frame: hydration must not raise and must keep None."""
    data = json.loads(_frames_payload_with_null_impression())
    frame = Frame(**data['frames'][0])
    assert frame.last_impression_at is None
    assert frame.name == 'Fake Frame'


def test_status_lists_never_impressed_frame_without_traceback(monkeypatch, capsys):
    """venus story: `status` against a frames.json where last_impression_at is
    null must list the frame — exit 0, name printed, never a traceback."""
    monkeypatch.setenv('AURA_EMAIL', 'you@example.invalid')
    monkeypatch.setenv('AURA_PASSWORD', 'super-secret-pw')

    override = httpx.Response(200, content=_frames_payload_with_null_impression())
    aura = offline_aura(overrides={'/v5/frames.json': override})

    rc = run_status(aura=aura)

    assert rc == 0
    out = capsys.readouterr().out
    assert 'Fake Frame' in out
    assert 'frame-fake-0001' in out

"""Offline tests proving the write/delete endpoints fail loud (WRITE-05) and
that Aura.get_sqs is parameterized by frame_id with no hardcoded queue id
(WRITE-04, D-11) (Phase 8 Plan 01, Tasks 2 + 3).

Unmarked (no @pytest.mark.live) — all exercised through
`tests/offline.py`'s `httpx.MockTransport`-backed harness, zero network
access and no credentials required.
"""
import httpx
import pytest

from pushframe.client import WriteEndpointError
from pushframe.models.asset import Asset, AssetPartial, AssetPartialId
from tests.offline import offline_aura

FRAME_ID = 'frame-fake-0001'
SELECT_ASSET_PATH = f'/v5/frames/{FRAME_ID}/select_asset.json'
REMOVE_ASSET_PATH = f'/v5/frames/{FRAME_ID}/remove_asset.json'
BATCH_UPDATE_PATH = '/v5/assets/batch_update.json'
DELETE_ASSET_PATH = '/v5/assets/asset-1.json'


def _asset_partial_id():
    return AssetPartialId(local_identifier='local-id-1')


# ---------------------------------------------------------------------------
# select_asset
# ---------------------------------------------------------------------------

def test_select_asset_raises_on_error_envelope():
    overrides = {
        SELECT_ASSET_PATH: httpx.Response(200, json={'error': 'not_found'}),
    }
    aura = offline_aura(overrides=overrides)

    with pytest.raises(RuntimeError):
        aura.frame_api.select_asset(FRAME_ID, _asset_partial_id())


def test_select_asset_raises_on_nonzero_number_failed():
    overrides = {
        SELECT_ASSET_PATH: httpx.Response(200, json={'number_failed': 1}),
    }
    aura = offline_aura(overrides=overrides)

    with pytest.raises(RuntimeError):
        aura.frame_api.select_asset(FRAME_ID, _asset_partial_id())


def test_select_asset_returns_number_failed_on_success():
    overrides = {
        SELECT_ASSET_PATH: httpx.Response(200, json={'number_failed': 0}),
    }
    aura = offline_aura(overrides=overrides)

    result = aura.frame_api.select_asset(FRAME_ID, _asset_partial_id())

    assert result == 0


def test_select_asset_accepts_a_list_and_sends_one_batched_call():
    overrides = {SELECT_ASSET_PATH: httpx.Response(200, json={'number_failed': 0})}
    aura = offline_aura(overrides=overrides)

    items = [
        AssetPartialId(local_identifier='local-id-1'),
        AssetPartialId(local_identifier='local-id-2'),
        AssetPartialId(local_identifier='local-id-3'),
    ]
    result = aura.frame_api.select_asset(FRAME_ID, items)

    assert result == 0
    # Exactly one call was made and it carried all 3 assets in one payload.
    select_calls = [r for r in aura._client.history if r.request.url.path == SELECT_ASSET_PATH]
    assert len(select_calls) == 1
    import json as _json
    sent = _json.loads(select_calls[0].request.content)
    assert sent == {'assets': [
        {'asset_local_identifier': 'local-id-1'},
        {'asset_local_identifier': 'local-id-2'},
        {'asset_local_identifier': 'local-id-3'},
    ]}


# ---------------------------------------------------------------------------
# remove_asset
# ---------------------------------------------------------------------------

def test_remove_asset_raises_on_error_envelope():
    overrides = {
        REMOVE_ASSET_PATH: httpx.Response(200, json={'error': 'not_found'}),
    }
    aura = offline_aura(overrides=overrides)

    with pytest.raises(RuntimeError):
        aura.frame_api.remove_asset(FRAME_ID, _asset_partial_id())


def test_remove_asset_raises_on_nonzero_number_failed():
    overrides = {
        REMOVE_ASSET_PATH: httpx.Response(200, json={'number_failed': 2}),
    }
    aura = offline_aura(overrides=overrides)

    with pytest.raises(RuntimeError):
        aura.frame_api.remove_asset(FRAME_ID, _asset_partial_id())


def test_remove_asset_returns_number_failed_on_success():
    overrides = {
        REMOVE_ASSET_PATH: httpx.Response(200, json={'number_failed': None}),
    }
    aura = offline_aura(overrides=overrides)

    result = aura.frame_api.remove_asset(FRAME_ID, _asset_partial_id())

    assert not result


def test_remove_asset_accepts_a_list_and_sends_one_batched_call():
    overrides = {REMOVE_ASSET_PATH: httpx.Response(200, json={'number_failed': 0})}
    aura = offline_aura(overrides=overrides)

    items = [AssetPartialId(id='asset-1'), AssetPartialId(id='asset-2')]
    result = aura.frame_api.remove_asset(FRAME_ID, items)

    assert result == 0
    remove_calls = [r for r in aura._client.history if r.request.url.path == REMOVE_ASSET_PATH]
    assert len(remove_calls) == 1
    import json as _json
    sent = _json.loads(remove_calls[0].request.content)
    assert sent == {'assets': [{'asset_id': 'asset-1'}, {'asset_id': 'asset-2'}]}


# ---------------------------------------------------------------------------
# batch_update
# ---------------------------------------------------------------------------

def test_batch_update_raises_on_error_envelope():
    overrides = {
        BATCH_UPDATE_PATH: httpx.Response(200, json={'error': 'invalid'}),
    }
    aura = offline_aura(overrides=overrides)
    partial = AssetPartial(local_identifier='local-id-1')

    with pytest.raises(WriteEndpointError):
        aura.asset_api.batch_update(partial)


def test_batch_update_succeeds_with_asset_partial():
    overrides = {
        BATCH_UPDATE_PATH: httpx.Response(200, json={
            'ids': ['local-id-1'],
            'successes': [{'id': 'asset-1', 'local_identifier': 'local-id-1'}],
        }),
    }
    aura = offline_aura(overrides=overrides)
    partial = AssetPartial(local_identifier='local-id-1', file_name='photo.jpg')

    result = aura.asset_api.batch_update(partial)

    assert result.ids == ['local-id-1']
    assert len(result.successes) == 1
    assert result.successes[0].id == 'asset-1'
    assert result.unacknowledged == []


def test_batch_update_accepts_a_list_and_does_not_raise_on_partial_successes():
    # A partial `successes` list (fewer than sent) is the NORMAL, expected
    # batch-mode signal that the caller must attribute per-item -- it must
    # NOT raise. This is the behavioral change from the old single-item
    # partial-failure raise (attribution is now the caller's job).
    overrides = {
        BATCH_UPDATE_PATH: httpx.Response(200, json={
            'ids': ['local-id-1', 'local-id-2', 'local-id-3'],
            'successes': [{'id': 'asset-1', 'local_identifier': 'local-id-1'}],
        }),
    }
    aura = offline_aura(overrides=overrides)
    partials = [
        AssetPartial(local_identifier='local-id-1', file_name='a.jpg'),
        AssetPartial(local_identifier='local-id-2', file_name='b.jpg'),
        AssetPartial(local_identifier='local-id-3', file_name='c.jpg'),
    ]

    result = aura.asset_api.batch_update(partials)

    assert result.ids == ['local-id-1', 'local-id-2', 'local-id-3']
    assert len(result.successes) == 1
    assert result.successes[0].local_identifier == 'local-id-1'
    assert result.unacknowledged == ['local-id-2', 'local-id-3']
    batch_calls = [r for r in aura._client.history if r.request.url.path == BATCH_UPDATE_PATH]
    assert len(batch_calls) == 1


# ---------------------------------------------------------------------------
# delete_asset
# ---------------------------------------------------------------------------

def test_delete_asset_raises_on_error_envelope():
    overrides = {
        DELETE_ASSET_PATH: httpx.Response(200, json={'error': 'forbidden'}),
    }
    aura = offline_aura(overrides=overrides)
    asset = Asset.model_construct(id='asset-1', local_identifier=None)

    with pytest.raises(WriteEndpointError):
        aura.asset_api.delete_asset(asset)


# ---------------------------------------------------------------------------
# get_sqs (WRITE-04, D-11)
# ---------------------------------------------------------------------------

class _FakeSQSClient:
    """Records the frame_id it was constructed/queried with, proving
    Aura.get_sqs no longer leaks a hardcoded queue id."""

    def __init__(self):
        self.requested_frame_id = None

    def get_queue_url(self, frame_id: str):
        self.requested_frame_id = frame_id
        return f'https://sqs.fake/{frame_id}'


def test_get_sqs_passes_frame_id_to_sqs_client(monkeypatch):
    aura = offline_aura()
    fake_client = _FakeSQSClient()
    monkeypatch.setattr('pushframe.aura.SQSClient', lambda: fake_client)

    queue_url = aura.get_sqs('frame-abc-123')

    assert fake_client.requested_frame_id == 'frame-abc-123'
    assert queue_url == 'https://sqs.fake/frame-abc-123'

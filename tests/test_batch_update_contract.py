"""Offline contract tests for `AssetApi.batch_update`'s `BatchUpdateResult`
(REL-06/REL-07, D-18/D-19, Phase 11 Plan 02, Task 1).

`assetApi.py` is otherwise entirely untested (`codebase/CONCERNS.md` line
175) -- these are its first tests. They prove the named `unacknowledged`
return element (the sent-but-not-acknowledged local_identifier set) and the
tolerant inbound / strict outbound parsing split.

Unmarked (no @pytest.mark.live) — all exercised through `tests/offline.py`'s
`httpx.MockTransport`-backed harness, zero network access and no
credentials required, following `tests/test_write_endpoints_failloud.py`'s
conventions.
"""
import httpx
import pytest
from pydantic import ValidationError

from pushframe.api.assetApi import BatchUpdateResult
from pushframe.models.asset import AssetPartial, AssetPartialId
from tests.offline import offline_aura

BATCH_UPDATE_PATH = '/v5/assets/batch_update.json'


def _partials(*local_ids):
    return [AssetPartial(local_identifier=lid, file_name=f'{lid}.jpg') for lid in local_ids]


def test_unacknowledged_is_empty_when_every_sent_id_is_acknowledged():
    """Test 1: three items sent, all three acknowledged -> unacknowledged == []."""
    overrides = {
        BATCH_UPDATE_PATH: httpx.Response(200, json={
            'ids': ['local-id-1', 'local-id-2', 'local-id-3'],
            'successes': [
                {'id': 'asset-1', 'local_identifier': 'local-id-1'},
                {'id': 'asset-2', 'local_identifier': 'local-id-2'},
                {'id': 'asset-3', 'local_identifier': 'local-id-3'},
            ],
        }),
    }
    aura = offline_aura(overrides=overrides)

    result = aura.asset_api.batch_update(_partials('local-id-1', 'local-id-2', 'local-id-3'))

    assert isinstance(result, BatchUpdateResult)
    assert result.unacknowledged == []
    assert len(result.successes) == 3


def test_unacknowledged_preserves_sent_order_for_a_partial_acknowledgement():
    """Test 2: three items sent, only the first acknowledged -> unacknowledged ==
    ['local-id-2', 'local-id-3'] in sent order, and batch_update does not raise."""
    overrides = {
        BATCH_UPDATE_PATH: httpx.Response(200, json={
            'ids': ['local-id-1', 'local-id-2', 'local-id-3'],
            'successes': [
                {'id': 'asset-1', 'local_identifier': 'local-id-1'},
            ],
        }),
    }
    aura = offline_aura(overrides=overrides)

    result = aura.asset_api.batch_update(_partials('local-id-1', 'local-id-2', 'local-id-3'))

    assert result.unacknowledged == ['local-id-2', 'local-id-3']
    assert len(result.successes) == 1


def test_absent_successes_key_yields_the_full_sent_set_unacknowledged_without_raising():
    """Test 3: response has no `successes` key at all -> every sent id is
    unacknowledged, in sent order, and no exception is raised."""
    overrides = {
        BATCH_UPDATE_PATH: httpx.Response(200, json={
            'ids': ['local-id-1', 'local-id-2', 'local-id-3'],
        }),
    }
    aura = offline_aura(overrides=overrides)

    result = aura.asset_api.batch_update(_partials('local-id-1', 'local-id-2', 'local-id-3'))

    assert result.unacknowledged == ['local-id-1', 'local-id-2', 'local-id-3']
    assert result.successes == []


def test_empty_assets_list_yields_three_empty_collections_and_one_request():
    """Test 4: an empty list of assets yields empty ids, empty successes and
    empty unacknowledged, and issues exactly one request."""
    overrides = {
        BATCH_UPDATE_PATH: httpx.Response(200, json={'ids': [], 'successes': []}),
    }
    aura = offline_aura(overrides=overrides)

    result = aura.asset_api.batch_update([])

    assert result == BatchUpdateResult([], [], [])
    batch_calls = [r for r in aura._client.history if r.request.url.path == BATCH_UPDATE_PATH]
    assert len(batch_calls) == 1


def test_malformed_successes_entry_is_skipped_without_raising():
    """Test 5: one entry in `successes` has neither `id` nor `local_identifier`
    (fails AssetPartialId's cross-field validator); the two valid entries are
    still returned and the malformed one counts as unacknowledged by omission.
    batch_update itself does not raise."""
    overrides = {
        BATCH_UPDATE_PATH: httpx.Response(200, json={
            'ids': ['local-id-1', 'local-id-2', 'local-id-3'],
            'successes': [
                {'id': 'asset-1', 'local_identifier': 'local-id-1'},
                {'user_id': 'u1'},  # malformed: neither id nor local_identifier
                {'id': 'asset-3', 'local_identifier': 'local-id-3'},
            ],
        }),
    }
    aura = offline_aura(overrides=overrides)

    result = aura.asset_api.batch_update(_partials('local-id-1', 'local-id-2', 'local-id-3'))

    assert len(result.successes) == 2
    assert {s.local_identifier for s in result.successes} == {'local-id-1', 'local-id-3'}
    assert result.unacknowledged == ['local-id-2']


def test_asset_partial_id_construction_with_neither_field_raises():
    """Test 6: constructing AssetPartialId() with neither id nor
    local_identifier raises pydantic.ValidationError on the ordinary
    construction path -- the outbound path stays strict while the inbound
    path (this module's other tests) just became tolerant."""
    with pytest.raises(ValidationError):
        AssetPartialId()

"""Offline tests for `AssetPartial` (pushframe.models.asset), the
all-Optional variant of `Asset` used to send new-upload metadata through
`AssetApi.batch_update` before every field of a real `Asset` is known
(Phase 8 Plan 01, Task 1).

Unmarked (no @pytest.mark.live) — pure model-construction tests, zero
network access and no credentials required.

REL-06 finding (Phase 11 Plan 02, Task 2): `AssetPartialId`'s
`model_validator(mode='after')` (`pushframe/models/asset.py`) already fires
on the ordinary construction path under the installed pydantic v2 -- both on
`AssetPartialId()` (no args) and on keyword-expanded construction
(`AssetPartialId(**entry)`, the shape `AssetApi.batch_update` uses to parse
an inbound `successes` entry). REL-06 is therefore closed by the proving
tests below plus `batch_update`'s inbound tolerance (Task 1's
try/except ValidationError around each entry), not by rewriting the
validator. `pushframe/models/asset.py` is unmodified by this plan.
"""
import pytest
from pydantic import ValidationError

from pushframe.models.asset import Asset, AssetPartial, AssetPartialId

# The exact allowlist AssetApi.batch_update uses for its `.dict(include=...)`
# call (pushframe/api/assetApi.py:19-38) -- AssetPartial must serialize to
# precisely this shape.
BATCH_UPDATE_ALLOWLIST = {
    'data_uti': True,
    'favorite': True,
    'file_name': True,
    'height': True,
    'local_identifier': True,
    'location': True,
    'md5_hash': True,
    'modified_at': True,
    'orientation': True,
    'selected': True,
    'taken_at': True,
    'upload_priority': True,
    'width': True,
}


def test_asset_partial_constructs_with_id_unset():
    """AssetPartial(local_identifier='x') constructs with no ValidationError,
    unlike Asset(id=None, ...) which raises because `id` is a required str."""
    partial = AssetPartial(local_identifier='x')

    assert partial.local_identifier == 'x'
    assert partial.id is None


def test_asset_partial_serializes_to_batch_update_payload_shape():
    """.dict(include=...) over batch_update's allowlist returns exactly
    those keys, regardless of which fields were actually set."""
    partial = AssetPartial(
        local_identifier='local-id-1',
        file_name='photo.jpg',
        md5_hash='deadbeef==',
        height=100,
        width=200,
        taken_at='2026-07-07T00:00:00Z',
        data_uti='public.jpeg',
        selected=True,
        upload_priority=1,
    )

    payload = partial.dict(include=BATCH_UPDATE_ALLOWLIST)

    assert set(payload.keys()) == set(BATCH_UPDATE_ALLOWLIST.keys())
    assert payload['local_identifier'] == 'local-id-1'
    assert payload['file_name'] == 'photo.jpg'
    assert payload['md5_hash'] == 'deadbeef=='
    assert payload['height'] == 100
    assert payload['width'] == 200
    assert payload['taken_at'] == '2026-07-07T00:00:00Z'
    assert payload['data_uti'] == 'public.jpeg'
    assert payload['selected'] is True
    assert payload['upload_priority'] == 1


def test_asset_partial_is_subclass_of_asset():
    """make_partial uses __base__=model, so isinstance(p, Asset) is True."""
    partial = AssetPartial(local_identifier='x')

    assert isinstance(partial, Asset)


def test_asset_partial_id_no_args_raises_validation_error():
    """Test 7: AssetPartialId() raises pydantic.ValidationError, naming both
    fields, when neither id nor local_identifier is provided."""
    with pytest.raises(ValidationError) as exc_info:
        AssetPartialId()

    message = str(exc_info.value)
    assert 'id' in message
    assert 'local_identifier' in message


def test_asset_partial_id_keyword_expanded_all_none_raises_validation_error():
    """Test 8: AssetPartialId(**{'id': None, 'local_identifier': None,
    'user_id': 'u'}) -- the inbound dict-expansion shape AssetApi.batch_update
    uses to parse a `successes` entry -- also raises, proving the validator
    fires on keyword-expansion construction, not only on the no-argument
    path."""
    with pytest.raises(ValidationError):
        AssetPartialId(**{'id': None, 'local_identifier': None, 'user_id': 'u'})


def test_asset_partial_id_single_field_constructions_succeed():
    """Test 9: AssetPartialId(id='a') and AssetPartialId(local_identifier='b')
    each construct successfully -- the validator rejects only the genuinely
    identity-less case."""
    by_id = AssetPartialId(id='a')
    by_local_id = AssetPartialId(local_identifier='b')

    assert by_id.id == 'a'
    assert by_local_id.local_identifier == 'b'


def test_asset_partial_id_to_request_format_unchanged_by_this_phase():
    """Test 10: to_request_format()'s outbound wire shape is unchanged --
    id-based construction sends {'asset_id': ...}, local_identifier-based
    construction sends {'asset_local_identifier': ...}."""
    assert AssetPartialId(id='a').to_request_format() == {'asset_id': 'a'}
    assert AssetPartialId(local_identifier='b').to_request_format() == {'asset_local_identifier': 'b'}

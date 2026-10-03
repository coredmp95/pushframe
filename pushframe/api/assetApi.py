import typing

from loguru import logger
from pydantic import ValidationError

from pushframe.api.baseApi import BaseApi
from pushframe.client import WriteEndpointError

# TODO: Untested
from pushframe.models.asset import Asset, AssetPartial, AssetPartialId


class BatchUpdateResult(typing.NamedTuple):
    """Result of `AssetApi.batch_update`.

    A `NamedTuple` so `unacknowledged` is reachable by name
    (`result.unacknowledged`) while the value stays usable positionally.

    `unacknowledged` is the sent-but-not-acknowledged local_identifier set --
    the honest name for the fact that a `batch_update` response can silently
    drop requested ids (REL-07, D-18). A non-empty `unacknowledged` is a
    per-item failure signal for the caller to attribute, NOT an error
    condition for `batch_update` itself to raise on -- see the docstring
    below.
    """
    ids: list[str]
    successes: list[AssetPartialId]
    unacknowledged: list[str]


class AssetApi(BaseApi):

    def batch_update(self, assets: Asset | AssetPartial | list[Asset | AssetPartial]) -> BatchUpdateResult:
        """
        Posts new metadata to the API for one or more assets. This does not appear to affect the
        frame; however subsequent calls to retrieve the asset(s) will have the modified metadata.

        Primarily used to update an asset after the image has been uploaded to S3.

        This is a native Pushd BATCH endpoint: the official app sends the whole collection of
        assets to update in a single `{"assets": [...]}` call rather than one call per asset. A
        single `Asset`/`AssetPartial` is accepted for backward compatibility (normalized to a
        one-element list).

        `successes` in the response (each carrying `id` + `local_identifier`) is the per-file
        source of truth for batch callers: match each sent item's `local_identifier` against
        `successes[].local_identifier` to attribute success/failure per item -- a partial
        `successes` list (fewer entries than sent) is the NORMAL, expected signal in batch mode
        that the caller must attribute per-item, not an error to raise on. Only the `error`
        envelope (a whole-call failure) raises here.

        The returned `BatchUpdateResult.unacknowledged` is that same per-item signal computed
        once, here, as the sent local_identifiers (in sent order) that never appear among the
        parsed `successes` entries -- returned to the caller rather than raised, since a partial
        `successes` list is this endpoint's normal batch behavior, not an error. Every caller,
        not just `execute_plan`, can read it -- `Aura.upload_image` now does too.

        Each entry in the inbound `successes` array is parsed strictly (`AssetPartialId`'s
        cross-field validator still applies), but a single malformed entry is skipped and logged
        rather than raised (D-19): the API is undocumented and its shape drifts, so one junk row
        in an otherwise-good response must not cost the whole chunk's per-file attribution.

        :param assets: A single `Asset`/`AssetPartial`, or a list of them, to update in one call.
        :return: A `BatchUpdateResult` of sent remote ids, parsed `AssetPartialId` successes (may
            be a partial subset of what was sent -- see above), and the unacknowledged
            local_identifier set.
        """
        items = assets if isinstance(assets, list) else [assets]

        json_response = self._client.put(f'/assets/batch_update.json', data={
            "assets": [
                item.model_dump(
                    include={
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
                        'width': True
                    })
                for item in items
            ]
        })
        if json_response.get('error'):
            raise WriteEndpointError(f"batch_update failed: {json_response.get('error')}")

        ids = json_response.get('ids') or []
        raw_successes = json_response.get('successes') or []

        successes: list[AssetPartialId] = []
        for entry in raw_successes:
            # D-19: strict outbound, tolerant inbound. The outbound
            # AssetPartialId validator (pushframe/models/asset.py) is left
            # untouched; here on the inbound side, one malformed row must
            # not crash the whole chunk -- the API is undocumented and its
            # shape drifts (Phase 10's smart_adds regression is the
            # precedent), so a single junk entry in a 50-item response is
            # skipped and logged rather than costing the whole chunk's
            # per-file attribution through execute_plan's generic
            # `except Exception` branch.
            try:
                successes.append(AssetPartialId(**entry))
            except ValidationError as e:
                keys = sorted(entry.keys()) if isinstance(entry, dict) else type(entry).__name__
                # T-11-06: pydantic v2's default ValidationError str/repr embeds
                # each error's raw input_value (confirmed: constructing
                # AssetPartialId(**{'user_id': '...'}) puts the whole dict --
                # including user_id -- in str(e)). include_input=False strips
                # that before it reaches the log sink; only keys + error
                # type/message are logged, never entry values.
                safe_errors = e.errors(include_url=False, include_input=False)
                logger.warning(f"batch_update: skipping malformed successes entry (keys={keys}): {safe_errors}")

        acknowledged = {s.local_identifier for s in successes if s.local_identifier}
        unacknowledged = [
            item.local_identifier for item in items
            if item.local_identifier and item.local_identifier not in acknowledged
        ]

        return BatchUpdateResult(ids, successes, unacknowledged)

    def get_asset_by_local_identifier(self, local_id: str):
        """
        Retrieves an asset given a local id.
        :param local_id: A local id string.
        :return: The retrieved asset, related child albums, and any smart adds related to the asset.
        """
        json_response = self._client.get(f'/assets/asset_for_local_identifier.json',
                                         query_params={'local_identifier': local_id})

        return Asset(**json_response.get('asset')), json_response.get('child_albums'), json_response.get('smart_adds')

    def update_taken_at_date(self, asset: Asset) -> Asset:
        """
        Updates an asset's taken_date and taken_at_granularity. This will modify the date displayed in the frame and
        from future responses.
        :param asset: Asset with new taken_at or taken_at_granularity
        :return: The asset with modified dates
        """
        # Asset.id is a required str (never None for a server-hydrated
        # Asset), so this always uses the id-based request shape.
        request = {
            'taken_at': asset.taken_at,
            'taken_at_granularity': asset.taken_at_granularity,
            'id': asset.id,
        }

        json_response = self._client.post(f'/assets/update_taken_at_date.json', data=request)
        return Asset(**json_response)

    def delete_asset(self, asset: Asset):
        """
        Deletes the asset. **Currently unknown if this is used, most deletions occur by removing
        the activity; maybe this deletes it from S3/Glacier** see :func:`FrameApi.remove_asset`

        :param asset: Asset for removal
        :return: TODO
        """
        # Asset.id is a required str (never None for a server-hydrated
        # Asset), so this always uses the id-based delete endpoint.
        json_response = self._client.delete(f'/assets/{asset.id}.json')

        if json_response.get('error'):
            raise WriteEndpointError(f"delete_asset failed: {json_response.get('error')}")

        return json_response

    def crop_asset(self, asset: Asset) -> Asset:
        """
        Crops an asset, modifying `rotation_cw`, `user_landscape_rect`, `user_portrait_rect` and related
        aspect ratio rects.
        :param asset: Asset containing new rotation/rect data.
        :return: The asset with modified crop fields.
        """
        json_response = self._client.post(f'/assets/crop.json', data=asset.model_dump(
            include={
                'id': True,
                'local_identifier': True,
                'user_id': True,
                'rotation_cw': True,
                'user_landscape_16_10_rect': True,
                'user_landscape_rect': True,
                'user_portrait_4_5_rect': True,
                'user_portrait_rect': True
            }))

        return Asset(**json_response.get('asset'))

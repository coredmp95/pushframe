import uuid

from pushframe.api.baseApi import BaseApi
from pushframe.models.activity import Activity
from pushframe.models.asset import Asset, AssetPartialId
from pushframe.models.frame import Frame, FramePartial

from pushframe.utils.dt import get_utc_now, format_dt_to_aura


def _apply_asset_settings(assets: list[Asset], asset_settings) -> None:
    """Overwrite each asset's `selected` with this frame's visibility, taken
    from the `asset_settings` array that rides alongside `assets` in the
    /frames/{id}/assets.json response.

    Visibility is per-frame state, so it lives in `asset_settings` (keyed by
    `asset_id`, carrying `selected` and its mirror `hidden`), NOT on the asset
    itself. Live-confirmed in Phase 10: after `exclude_asset` hid a photo, its
    `asset_settings.selected` flipped to `false` while the asset-level
    `selected` stayed `true`. Reading the asset-level field would classify
    every photo as visible forever and silently never hide anything, so the
    join happens once here at the API boundary rather than in each caller.

    An asset with no matching settings row keeps whatever `selected` the API
    sent (it has no per-frame override to apply). Mutates `assets` in place.
    """
    if not asset_settings:
        return

    visibility = {
        row['asset_id']: row['selected']
        for row in asset_settings
        if row.get('asset_id') is not None and row.get('selected') is not None
    }
    for asset in assets:
        if asset.id in visibility:
            asset.selected = visibility[asset.id]


class FrameApi(BaseApi):

    def get_frames(self) -> list[Frame]:
        """
        Gets all frames available for the active user.
        :return: List of all frames the active user owns or is collaborating on.
        """
        json_response = self._client.get('/frames.json')
        return [Frame(**frame_data) for frame_data in json_response.get('frames')]

    def get_frame(self, frame_id: str) -> tuple[Frame, int]:
        """
        Gets frame data for a given `frame_id`
        :param frame_id: Frame id to retrieve
        :return: The hydrated frame and the frame's total asset count.
        """
        json_response = self._client.get(f'/frames/{frame_id}.json')
        frame_data = json_response.get('frame')
        # The live API moved the asset count: it used to be the top-level
        # `total_asset_count` and is now `frame.num_assets` (Phase 2 live drift).
        # Prefer the legacy key, fall back to the new location so either API
        # shape yields a count.
        total_asset_count = json_response.get('total_asset_count')
        if total_asset_count is None and frame_data:
            total_asset_count = frame_data.get('num_assets')
        return Frame(**frame_data), total_asset_count

    def get_assets(self, frame_id: str, limit: int = 1000, cursor: str = None) -> tuple[list[Asset], str]:
        """
        Gets assets for a `frame_id`. The results are paginated with `limit` results per page. To obtain the next set
        of pages, pass in the cursor from the response.

        Returns BOTH visible and hidden assets: the request sends `filter=all`
        because the server otherwise defaults to `filter=selected` and silently
        drops every hidden asset from the page (live-confirmed Phase 10 --
        omitting the filter returned 154 assets where `filter=all` returned
        157). Hidden assets must stay in the listing so a hidden photo still
        counts as present for md5 dedup and is never re-uploaded (D-06).

        Each returned `Asset.selected` carries THIS FRAME's visibility, joined
        from the response's parallel `asset_settings` array (see
        `_apply_asset_settings`). The asset-level `selected` field the API
        sends is NOT per-frame visibility -- live probing showed it stays
        `true` even while the photo is hidden on the frame -- so it is
        overwritten here, at the boundary, and every downstream consumer can
        read `asset.selected` as the real signal (D-01/D-05).

        :param frame_id: Frame ID to retrieve assets
        :param limit: Maximum number of assets per page / callout.
        :param cursor: The cursor from the previous page.
        :return: List of all the assets (visible and hidden), and the next page's cursor
            (will be `None` if there are no more pages)
        """
        json_response = self._client.get(f'/frames/{frame_id}/assets.json',
                                         query_params={'limit': limit, 'cursor': cursor, 'filter': 'all'})
        if json_response.get('error'):
            # Surface API drift instead of silently swallowing it (D-06):
            # a drifted/failed asset page must not be processed as success.
            raise RuntimeError(
                f"get_assets failed for frame {frame_id}: "
                f"{json_response.get('message') or json_response.get('error')}"
            )
        assets = [Asset(**asset_data) for asset_data in json_response.get('assets')]
        _apply_asset_settings(assets, json_response.get('asset_settings'))
        return assets, json_response.get('next_page_cursor')

    def get_activities(self, frame_id: str, cursor: str = None):
        """
        Gets activities associated to a frame. This appears to be paginated, although
        :param frame_id: Frame id to retrieve associated activities
        :param cursor: Cursor of the previous page TODO: **UNUSED?**
        :return: A list of activities, the cursor for the next page
        """
        json_response = self._client.get(f'/frames/{frame_id}/activities.json', query_params={'cursor': cursor})
        return [Activity(**json_activity) for json_activity in
                json_response.get('activities')], json_response.get('next_page_cursor')

    def show_asset(self, frame_id: str, asset_id: str, goto_time: str) -> bool:
        """
        Forces the frame to display the asset.

        :param frame_id: Frame id to control
        :param asset_id: Asset id to display on the frame
        :param goto_time: TODO: Unknown, appears to be the current datetime -- does setting it to the future queue the
                            asset?
        :return: Boolean describing if the frame was able to process the request.
        """
        json_response = self._client.post(f'/frames/{frame_id}/goto.json', data={
            'asset_id': asset_id,
            'frame_id': frame_id,
            'goto_time': goto_time if goto_time else format_dt_to_aura(get_utc_now()),
            'swipe_direction': 0,
            'impression_id': uuid.uuid4(),
            'select_asset': True
        })

        return json_response.get('showing')

    def update_frame(self, frame_id: str, frame_partial: FramePartial):
        """
        Updates a frame by id. This cannot update the frame id.
            TODO: Should we assume that FramePartial has `id` set and use that instead of `frame_id`?

        :param frame_id: Frame to update
        :param frame_partial: `FramePartial` containing changes to the frame.
        :return: Returns the hydrated frame with changes.
        """
        json_response = self._client.put(f'/frames/{frame_id}.json',
                                         data={'frame': frame_partial.dict(exclude_unset=True)})
        return Frame(**json_response.get('frame'))

    def select_asset(self, frame_id: str, asset_partial_ids: AssetPartialId | list[AssetPartialId]) -> int:
        """
        Associates one or more assets to a frame. This is typically done immediately before the
        asset(s) are uploaded to S3.

        This is a native Pushd BATCH endpoint: the official app sends the whole collection of
        assets to associate in a single `{"assets": [...]}` call rather than one call per asset.
        A single `AssetPartialId` is accepted for backward compatibility (normalized to a
        one-element list) and legacy single-item callers are unaffected.

        :param frame_id: Frame id
        :param asset_partial_ids: A single `AssetPartialId`, or a list of them, to associate to
            the frame in one call.
        :return: The number of assets that failed to be associated to the frame. NOTE: this is a
            count only -- in batch mode (a list of more than one item) there is no per-item
            signal in this response, so a caller cannot learn WHICH item(s) failed from
            select_asset alone.
        """
        items = asset_partial_ids if isinstance(asset_partial_ids, list) else [asset_partial_ids]

        json_response = self._client.post(f'/frames/{frame_id}/select_asset.json',
                                          data={'assets': [item.to_request_format() for item in items]})
        if json_response.get('error'):
            raise RuntimeError(f"select_asset failed for frame {frame_id}: {json_response.get('error')}")

        number_failed = json_response.get('number_failed')
        if number_failed:
            raise RuntimeError(f"select_asset reported {number_failed} failure(s) for frame {frame_id}")

        return number_failed

    def exclude_asset(self, frame_id: str, asset_partial_ids: AssetPartialId | list[AssetPartialId]) -> int:
        """
        Hides one or more assets on the frame: they stop displaying in the slideshow but are
        NOT deleted -- they remain in `get_assets(filter='all')` and still show in the app.
        Live-confirmed in Phase 10 (the frame's asset total was unchanged across a hide, and
        the asset's `asset_settings.selected` flipped to false / `hidden` to true).

        `select_asset` is the exact inverse -- it un-hides. There is no `include_asset`
        endpoint and none is needed.

        This is a native Pushd BATCH endpoint (service method `excludeAssets`): the official
        app sends the whole collection in a single `{"assets": [...]}` call rather than one
        call per asset, live-confirmed in Phase 10 by hiding two assets in one request. A
        single `AssetPartialId` is accepted for backward compatibility (normalized to a
        one-element list).

        The URL deliberately has NO `.json` suffix -- unlike every sibling endpoint here.
        That matches what the decompiled app posts and is live-confirmed working; it is not
        a bug, so do not "fix" it.

        :param frame_id: Frame id
        :param asset_partial_ids: A single `AssetPartialId`, or a list of them, to hide on
            the frame in one call.
        :return: The number of assets that failed to be hidden. NOTE: this is a count only --
            in batch mode there is no per-item signal in this response, so a caller cannot
            learn WHICH item(s) failed from exclude_asset alone.
        """
        items = asset_partial_ids if isinstance(asset_partial_ids, list) else [asset_partial_ids]

        json_response = self._client.post(f'/frames/{frame_id}/exclude_asset',
                                          data={'assets': [item.to_request_format() for item in items]})
        if json_response.get('error'):
            raise RuntimeError(f"exclude_asset failed for frame {frame_id}: {json_response.get('error')}")

        number_failed = json_response.get('number_failed')
        if number_failed:
            raise RuntimeError(f"exclude_asset reported {number_failed} failure(s) for frame {frame_id}")

        return number_failed

    def remove_asset(self, frame_id: str, asset_partial_ids: AssetPartialId | list[AssetPartialId]) -> int:
        """
        Disassociates one or more assets from a frame. This does not seem to remove the asset(s)
        from S3/Glacier.

        This is a native Pushd BATCH endpoint: the official app sends the whole collection of
        assets to remove in a single `{"assets": [...]}` call rather than one call per asset. A
        single `AssetPartialId` is accepted for backward compatibility (normalized to a
        one-element list). Per-item delete attribution degrades to per-chunk in batch mode: a
        nonzero `number_failed` or a raised error fails the WHOLE batch's deletes, since this
        endpoint returns only a count, never which item(s) failed.

        :param frame_id: Frame id containing the asset(s).
        :param asset_partial_ids: A single `AssetPartialId`, or a list of them, to remove from
            the frame in one call.
        :return: The number of assets that failed to be removed from the frame. NOTE: this is a
            count only -- there is no per-item signal in this response.
        """
        items = asset_partial_ids if isinstance(asset_partial_ids, list) else [asset_partial_ids]

        json_response = self._client.post(f'/frames/{frame_id}/remove_asset.json',
                                          data={'assets': [item.to_request_format() for item in items]})
        if json_response.get('error'):
            raise RuntimeError(f"remove_asset failed for frame {frame_id}: {json_response.get('error')}")

        number_failed = json_response.get('number_failed')
        if number_failed:
            raise RuntimeError(f"remove_asset reported {number_failed} failure(s) for frame {frame_id}")

        return number_failed

    def reconfigure(self, frame_id: str):
        """
        TODO: Unknown
        :param frame_id:
        :return:
        """
        return self._client.post(f'/frames/{frame_id}/reconfigure.json', data=None)

    def add_playlist(self, frame_id: str, playlist_params: any):
        # TODO: Implement
        json_response = self._client.post(f'/frames/{frame_id}/add_playlist.json', data={})

        return json_response

    def remove_playlist(self, frame_id: str, playlist_params: any):
        # TODO: Implement
        json_response = self._client.post(f'/frames/{frame_id}/remove_playlist.json', data={})

        return json_response

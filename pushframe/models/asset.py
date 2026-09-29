from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, model_validator

from pushframe.models.meta import make_partial
from pushframe.models.user import User
from pushframe.utils.dt import parse_aura_dt


class AssetPadding(BaseModel):
    top: float
    right: float
    bottom: float
    left: float


class AssetSetting(BaseModel):
    added_by_id: str
    asset_id: str
    created_at: str
    frame_id: str
    hidden: bool
    id: str
    last_impression_at: str
    reason: str  # TODO: Have only seen "user"
    selected: bool
    updated_at: str
    updated_selected_at: str


class Asset(BaseModel):
    auto_landscape_16_10_rect: Optional[str] = None
    auto_portrait_4_5_rect: Optional[str] = None
    burst_id: Any
    burst_selection_types: Any
    colorized_file_name: Optional[str] = None
    # Optional with a None default, not required: whether the live
    # `/frames/{id}/assets.json` payload sends this field at all is
    # currently unknown (pydantic silently drops undeclared keys, so its
    # prior absence here proved nothing either way). Declaring it Optional
    # makes the value visible when the server does send it and None when it
    # does not -- additive, and cannot break any existing caller (Phase 11
    # Plan 03, D-15).
    created_at: Optional[str] = None
    created_at_on_client: Optional[str] = None
    # data_uti/file_name/good_resolution/height/width/upload_priority/taken_at/
    # uploaded_at are Optional because the live API returns assets that are still
    # mid-server-side-processing (a freshly-uploaded placeholder): source_id,
    # local_identifier, user and selected are populated, but the processed
    # content metadata (dimensions, filenames, dates, data_uti) is null and
    # good_resolution is omitted entirely until processing completes. See
    # .planning/debug/resolved/inspect-asset-null-fields.md.
    data_uti: Optional[str] = None
    duplicate_of_id: Optional[str] = None
    duration: Optional[float] = None
    duration_unclipped: Optional[float] = None
    exif_orientation: int
    favorite: Optional[bool] = None
    file_name: Optional[str] = None
    glaciered_at: str
    good_resolution: Optional[bool] = None
    handled_at: Optional[str] = None
    hdr: Optional[bool] = None
    height: Optional[int] = None
    horizontal_accuracy: Optional[float] = None
    id: str
    ios_media_subtypes: Optional[int] = None
    is_live: Optional[bool] = None
    is_subscription: bool
    landscape_16_10_url: Optional[str] = None
    landscape_16_10_url_padding: Optional[AssetPadding] = None
    landscape_rect: Optional[str] = None
    landscape_url: Optional[str] = None
    landscape_url_padding: Optional[AssetPadding] = None
    live_photo_off: Optional[bool] = None
    local_identifier: str
    location: Optional[list[float]] = None  # Lat/Long, seems to default to (-77.8943033, 34.1978216)
    location_name: Optional[str] = None
    md5_hash: Optional[str] = None
    minibar_landscape_url: Optional[str] = None
    minibar_portrait_url: Optional[str] = None
    minibar_url: Optional[str] = None
    modified_at: Optional[str] = None
    orientation: Optional[int] = None
    original_file_name: Optional[str] = None
    panorama: Optional[bool] = None
    portrait_4_5_url: Optional[str] = None
    portrait_4_5_url_padding: Optional[AssetPadding] = None
    portrait_rect: Optional[str] = None
    portrait_url: Optional[str] = None
    portrait_url_padding: Optional[AssetPadding] = None
    raw_file_name: Optional[str] = None
    represents_burst: Any
    rotation_cw: int
    selected: bool
    source_id: str
    taken_at: Optional[str] = None
    taken_at_granularity: Any
    taken_at_user_override_at: Optional[str] = None
    thumbnail_url: Optional[str] = None
    unglacierable: Optional[bool] = None
    upload_priority: Optional[int] = None
    uploaded_at: Optional[str] = None
    user: User
    user_id: str
    user_landscape_16_10_rect: Optional[str] = None
    user_landscape_rect: Optional[str] = None
    user_portrait_4_5_rect: Optional[str] = None
    user_portrait_rect: Optional[str] = None
    video_clip_excludes_audio: Optional[bool] = None
    video_clip_start: Any
    video_clipped_by_user_at: Optional[str] = None
    video_file_name: Optional[str] = None
    video_url: Optional[str] = None
    widget_url: Optional[str] = None
    width: Optional[int] = None

    @property
    def taken_at_dt(self):
        # taken_at is None for an unprocessed placeholder asset (see the field
        # comment above); parse_aura_dt(None) would raise, and the CLI reads
        # this property for every listed asset (cli.py inspect/sync), so return
        # None rather than crash on a not-yet-processed asset.
        if self.taken_at is None:
            return None
        return parse_aura_dt(self.taken_at)

    # `is_local_asset` (formerly `return self.id is None`) was removed:
    # `id` above is a required `str`, so any `Asset` built through normal
    # validated construction (the only path used by `FrameApi.get_assets`/
    # `AssetApi.get_asset_by_local_identifier`) can never have `id=None`.
    # The property was always `False` and its dependent branches in
    # `AssetApi` were unreachable dead code. `Asset` (as opposed to
    # `AssetPartial`) always represents a server-hydrated asset with a
    # real `id`.


class AssetPartialId(BaseModel):
    id: Optional[str] = None
    local_identifier: Optional[str] = None
    user_id: Optional[str] = None

    @model_validator(mode='after')
    def check_id_or_local_id(self) -> 'AssetPartialId':
        if not self.id and not self.local_identifier:
            raise ValueError('Either id or local_identifier is required')
        return self

    def to_request_format(self):
        # 'user_id': user_id # in the iphone version user_id is not passed in
        if self.id:
            return {'asset_id': self.id}
        else:
            return {'asset_local_identifier': self.local_identifier}


AssetPartial = make_partial(Asset, "AssetPartial")

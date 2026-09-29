from enum import Enum
from typing import Optional

import pydantic
from pydantic import BaseModel

from pushframe.models.meta import make_partial
from pushframe.models.user import User


class Feature(Enum):
    SKIP_VIDEO_PRELOAD = 'skip_video_preload'
    UDP_COMMANDS = 'udp_commands'
    MQTT_ENABLED = 'mqtt_enabled'
    # The Aura API is undocumented and adds feature flags over time; map any
    # value we don't recognise to UNKNOWN instead of raising on hydration, so a
    # newly-added flag can't break the whole read path (live drift, Phase 2).
    UNKNOWN = 'unknown'

    @classmethod
    def _missing_(cls, value):
        return cls.UNKNOWN


class Frame(BaseModel):
    id: str
    name: str
    user_id: str
    software_version: str
    build_version: str
    hw_android_version: str
    created_at: str
    updated_at: str
    handled_at: str
    deleted_at: Optional[str] = None
    updated_at_on_client: Optional[str] = None
    orientation: int
    auto_brightness: bool
    min_brightness: int
    max_brightness: int
    brightness: Optional[int] = None
    sense_motion: bool
    default_speed: Optional[str] = None
    slideshow_interval: int
    slideshow_auto: bool
    digits: int
    contributors: Optional[list[User]] = None
    contributor_tokens: list[dict]
    hw_serial: str
    matting_color: str
    trim_color: str
    is_handling: bool
    calibrations_last_modified_at: str
    gestures_on: bool
    portrait_pairing_off: Optional[bool] = None
    live_photos_on: bool
    auto_processed_playlist_ids: list[object]  # unknown
    time_zone: str
    wifi_network: str
    cold_boot_at: Optional[str] = None
    is_charity_water_frame: bool
    num_assets: int
    thanks_on: bool
    frame_queue_url: Optional[str] = None
    client_queue_url: str
    scheduled_display_sleep: bool
    scheduled_display_on_at: Optional[str] = None
    scheduled_display_off_at: Optional[str] = None
    forced_wifi_state: Optional[str] = None
    forced_wifi_recipient_email: Optional[str] = None
    is_analog_frame: bool
    control_type: str
    display_aspect_ratio: str
    has_claimable_gift: Optional[bool] = None
    gift_billing_hint: Optional[str] = None
    locale: str
    frame_type: Optional[int] = None
    description: Optional[str] = None
    representative_asset_id: Optional[str] = None
    sort_mode: Optional[str] = None
    email_address: str
    features: Optional[list[Feature]] = None
    letterbox_style: Optional[str] = None
    user: User
    playlists: list[dict]  # TODO
    delivered_frame_gift: Optional[dict] = None  # TODO
    last_feed_item: dict
    last_impression: Optional[dict] = None
    last_impression_at: str
    child_albums: list
    # The live API stopped returning `smart_adds` on /frames.json and
    # /frames/{id}.json (live drift, Phase 10). Optional-with-default so an
    # absent key can't break the whole read path -- same treatment as the
    # Phase 2 `total_asset_count` -> `num_assets` move.
    smart_adds: list = pydantic.Field(default_factory=list)
    recent_assets: list

    def is_portrait(self):
        return self.orientation == 2 or self.orientation == 3

    def get_frame_type(self):
        return self.frame_type if self.frame_type else "normal"


FramePartial = make_partial(Frame, "FramePartial")

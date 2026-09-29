from typing import Optional

from pydantic import BaseModel


class User(BaseModel):
    id: str
    created_at: str
    updated_at: str
    name: str
    email: str
    short_id: Optional[str] = None
    show_push_prompt: bool
    latest_app_version: Optional[str] = None
    attribution_id: Optional[str] = None
    attribution_string: Optional[str] = None
    test_account: Optional[bool] = None
    avatar_file_name: Optional[str] = None
    has_frame: Optional[bool] = None
    analytics_optout: bool = None
    admin_account: Optional[bool] = False
    auth_token: str = None

import json
from pathlib import Path

from pushframe.models.asset import Asset
from pushframe.models.frame import Frame
from pushframe.models.user import User

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _load(name: str) -> dict:
    return json.loads((FIXTURES_DIR / name).read_text())


def test_login_fixture_hydrates_user():
    data = _load("login.json")
    User(**data["result"]["current_user"])


def test_frames_fixture_hydrates_frame():
    data = _load("frames.json")
    for frame_data in data["frames"]:
        Frame(**frame_data)


def test_assets_page1_fixture_hydrates_asset():
    data = _load("assets_page1.json")
    for asset_data in data["assets"]:
        Asset(**asset_data)


def test_assets_page2_fixture_hydrates_asset():
    data = _load("assets_page2.json")
    for asset_data in data["assets"]:
        Asset(**asset_data)

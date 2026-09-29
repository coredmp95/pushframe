"""Regression tests for the `Asset` model tolerating a mid-server-side-
processing placeholder asset.

The live Aura/Pushd API can return an asset that has been queued but not yet
fully processed: source_id/local_identifier/user/selected are populated, but the
processed content metadata (data_uti, file_name, dimensions, dates,
upload_priority) is null and `good_resolution` is omitted from the payload
entirely. Before the fix those 8 fields were declared required, so building
`Asset(**data)` raised 8 pydantic ValidationErrors and crashed `pushframe
inspect` (see .planning/debug/resolved/inspect-asset-null-fields.md).

The fixture `asset_unprocessed.json` is the exact shape (redacted) that crashed.

Unmarked (no @pytest.mark.live) — runs in the default offline suite.
"""
import json
from pathlib import Path

from pushframe.models.asset import Asset

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _load_unprocessed() -> dict:
    data = json.loads((FIXTURES_DIR / "asset_unprocessed.json").read_text())
    data.pop("_comment", None)
    return data


def test_unprocessed_asset_hydrates_without_error():
    """The real placeholder shape that raised 8 ValidationErrors now hydrates."""
    data = _load_unprocessed()

    # Pre-condition guards: the fixture must actually reproduce the bug shape,
    # otherwise this test would pass vacuously.
    assert "good_resolution" not in data, "fixture must omit good_resolution"
    for null_field in (
        "data_uti", "file_name", "taken_at", "uploaded_at",
        "height", "width", "upload_priority",
    ):
        assert data[null_field] is None, f"fixture {null_field} must be null"

    asset = Asset(**data)

    # The 8 previously-required fields are now tolerated as None/absent.
    assert asset.data_uti is None
    assert asset.file_name is None
    assert asset.taken_at is None
    assert asset.uploaded_at is None
    assert asset.height is None
    assert asset.width is None
    assert asset.upload_priority is None
    assert asset.good_resolution is None

    # The genuinely-present fields still hydrate.
    assert asset.id == "asset-unprocessed-0001"
    assert asset.source_id == "source-unprocessed-0001"
    assert asset.selected is True
    assert asset.user.email == "fake-user@example.invalid"


def test_unprocessed_asset_taken_at_dt_is_none_not_crash():
    """`taken_at_dt` must return None (not raise) when taken_at is None — the
    CLI reads this property for every listed asset (cli.py inspect/sync)."""
    asset = Asset(**_load_unprocessed())
    assert asset.taken_at_dt is None


def test_newer_server_fields_are_ignored():
    """Newer fields the API added (is_classified/attachments/is_b2/is_video_b2)
    are not declared on the model; pydantic v2 ignores extras rather than
    raising — so they never re-break hydration."""
    data = _load_unprocessed()
    assert "is_classified" in data and "attachments" in data
    asset = Asset(**data)
    assert not hasattr(asset, "is_classified")

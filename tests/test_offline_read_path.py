"""Offline mirror of tests/test_read_path.py — exercises the same
Aura/*Api/Client stack assertions (login header-set, frame hydration,
pagination drain, both error-raise mechanisms) entirely through
`tests/offline.py`'s `httpx.MockTransport`-backed harness, with zero
network access and no credentials required.

Unmarked (no @pytest.mark.live) — this is the default pytest suite.
tests/test_read_path.py and tests/conftest.py are untouched.
"""
import httpx
import pytest

from pushframe.api.frameApi import FrameApi
from pushframe.client import Client
from pushframe.models.frame import Frame
from tests.offline import _load as _load_fixture, offline_aura


def test_offline_login_sets_auth_headers():
    """Mirrors test_read_01_login: a login through the mocked transport sets
    both auth headers on the shared httpx session."""
    aura = offline_aura()
    aura.login(email='fake@example.invalid', password='fake-pw')

    headers = aura._client.http2_client.headers
    assert headers.get("x-token-auth"), "x-token-auth header missing after login"
    assert headers.get("x-user-id"), "x-user-id header missing after login"


def test_offline_get_frames_hydrates_frame():
    """Mirrors test_read_02_list_frames: get_frames() returns a non-empty
    list[Frame] hydrated from the frames.json fixture."""
    aura = offline_aura()
    frames = aura.frame_api.get_frames()

    assert frames, "expected at least one frame from the fixture"
    for f in frames:
        assert isinstance(f, Frame)
        assert f.id, "frame is missing an id"


def test_offline_get_all_assets_drains_pagination():
    """Mirrors test_read_03_pagination: get_all_assets with a limit that
    forces the cursor branch drains both fixture pages (2 distinct assets)."""
    aura = offline_aura()
    frame = aura.frame_api.get_frames()[0]

    assets = aura.get_all_assets(frame.id, limit=1)

    assert len(assets) == 2, f"expected 2 stitched assets, got {len(assets)}"
    ids = {a.id for a in assets}
    assert len(ids) == 2, "expected two distinct asset ids across pages"


def test_offline_get_assets_raises_on_error_envelope():
    """Exercises the soft '200 + {error: ...} body' business-rule raise in
    FrameApi.get_assets (RESEARCH.md Pattern 3, mechanism 1)."""
    overrides = {
        "/v5/frames/frame-fake-0001/assets.json": httpx.Response(
            200, json={"error": "not_found", "message": "Resource not found"}
        )
    }
    aura = offline_aura(overrides=overrides)

    with pytest.raises(RuntimeError):
        aura.frame_api.get_assets("frame-fake-0001")


def test_offline_http_status_error_raises():
    """Exercises Client's unconditional raise_for_status() path (RESEARCH.md
    Pattern 3, mechanism 2) — distinct from the business-rule RuntimeError
    above."""
    transport = httpx.MockTransport(
        lambda request: httpx.Response(404, json={"error": "not_found"})
    )
    client = Client(transport=transport)

    with pytest.raises(httpx.HTTPStatusError):
        client.get("/missing.json")


def test_offline_get_assets_requests_filter_all():
    """get_assets must send `filter=all` (HIDE-02, D-06).

    The server defaults to `filter=selected` when the param is omitted and
    silently drops every hidden asset from the page, which would make a hidden
    photo look absent and get re-uploaded on the next sync.
    """
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/assets.json"):
            seen["filter"] = request.url.params.get("filter")
            return httpx.Response(200, json={"assets": [], "next_page_cursor": None})
        return httpx.Response(404, json={"error": "not_found"})

    client = Client(transport=httpx.MockTransport(handler))
    FrameApi(client).get_assets("frame-fake-0001")

    assert seen["filter"] == "all", f"expected filter=all, got {seen['filter']!r}"


def _assets_body(rows, asset_settings=None):
    """Build an assets.json response body from the fixture asset, cloned once
    per (id, selected) pair in `rows`, so the Asset model hydrates against a
    realistic full payload rather than a hand-rolled partial one."""
    template = _load_fixture("assets_page1.json")["assets"][0]
    assets = []
    for asset_id, selected in rows:
        clone = dict(template)
        clone["id"] = asset_id
        clone["selected"] = selected
        assets.append(clone)
    body = {"assets": assets, "next_page_cursor": None}
    if asset_settings is not None:
        body["asset_settings"] = asset_settings
    return body


def test_offline_get_assets_joins_per_frame_visibility_from_asset_settings():
    """`Asset.selected` must carry THIS FRAME's visibility, joined from the
    response's parallel `asset_settings` array (D-01/D-05).

    Live-confirmed in Phase 10: after `exclude_asset` hid a photo its
    `asset_settings.selected` flipped to false while the asset-level
    `selected` stayed true. Reading the asset-level field would classify every
    photo as visible forever and silently never hide anything, so the boundary
    overwrites it.
    """
    body = _assets_body(
        # The API still reports selected=true for all three...
        [("asset-hidden", True), ("asset-visible", True), ("asset-no-settings-row", True)],
        # ...but this frame's settings say one of them is hidden.
        asset_settings=[
            {"asset_id": "asset-hidden", "selected": False, "hidden": True},
            {"asset_id": "asset-visible", "selected": True, "hidden": False},
        ],
    )
    client = Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=body)))

    assets, _ = FrameApi(client).get_assets("frame-fake-0001")
    by_id = {a.id: a for a in assets}

    assert by_id["asset-hidden"].selected is False, \
        "hidden asset must read selected=False after the asset_settings join"
    assert by_id["asset-visible"].selected is True
    # No settings row => no per-frame override to apply; keep what the API sent.
    assert by_id["asset-no-settings-row"].selected is True


def test_offline_get_assets_survives_missing_asset_settings():
    """A response with no `asset_settings` array must not raise — the assets
    simply keep whatever `selected` the API sent."""
    body = _assets_body([("asset-1", True)])
    client = Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=body)))

    assets, _ = FrameApi(client).get_assets("frame-fake-0001")

    assert assets[0].selected is True


# --- TEST-01 (Phase 19): the two v1.1 lift-tests-off-network candidates ---


def test_offline_login_sets_exact_authenticated_values():
    """Candidate #2 (authenticated value): after login against the canned
    fixture, the session headers carry the fixture's EXACT values — not just
    any non-empty string (the presence-only assertion this candidate
    supersedes lives in test_offline_login_sets_auth_headers above)."""
    aura = offline_aura()
    aura.login(email='fake@example.invalid', password='fake-pw')

    headers = aura._client.http2_client.headers
    assert headers['x-token-auth'] == 'fake-auth-token-0001'
    assert headers['x-user-id'] == 'user-fake-0001'


def test_authenticated_headers_propagate_to_subsequent_requests():
    """Candidate #2 (propagation half): a follow-up request through the SAME
    logged-in client carries both authenticated headers on the wire — the
    behavior `add_default_headers` exists for and only the live suite
    exercised implicitly before."""
    seen: dict[str, str | None] = {}

    def capturing_frames_route(request: httpx.Request) -> httpx.Response:
        seen['x-token-auth'] = request.headers.get('x-token-auth')
        seen['x-user-id'] = request.headers.get('x-user-id')
        return httpx.Response(200, json=_load_fixture('frames.json'))

    aura = offline_aura(overrides={'/v5/frames.json': capturing_frames_route})
    aura.login(email='fake@example.invalid', password='fake-pw')
    aura.frame_api.get_frames()  # a request AFTER login, same client

    assert seen['x-token-auth'] == 'fake-auth-token-0001'
    assert seen['x-user-id'] == 'user-fake-0001'


def test_client_base_url_is_injectable():
    """Candidate #4 (injected config): Client accepts base_url and requests
    land on the injected host with its path prefix — offline via a capturing
    transport, zero network. The v1.1 deferral ('endpoint config deferred to
    candidate #4') closes here."""
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen['host'] = request.url.host
        seen['path'] = request.url.path
        return httpx.Response(200, json={"result": {}})

    client = Client(transport=httpx.MockTransport(handler), base_url='https://mock.invalid/v9')
    client.get('/probe.json')

    assert seen['host'] == 'mock.invalid'
    assert seen['path'] == '/v9/probe.json'

"""Reusable offline test harness driving the Aura client stack through
`httpx.MockTransport` instead of the live network (Phase 4 Plan 03).

This is a plain module of functions (not pytest fixtures) — offline tests
import `offline_aura`/`make_router` directly. `tests/conftest.py`'s existing
`aura` fixture (live, credential-gated) is a separate concern and is left
untouched.
"""
import json
from pathlib import Path

import httpx

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _load(name: str) -> dict:
    """Read and json-decode a fixture file by filename under tests/fixtures/."""
    return json.loads((FIXTURES_DIR / name).read_text())


def make_router(overrides: dict | None = None):
    """Build a `MockTransport` handler routing on the fully-resolved request
    path (including the `/v5` base_url prefix) plus, for the paginated
    assets endpoint, the `cursor` query param.

    `overrides` lets a caller substitute a canned `httpx.Response` for a
    given path key, checked before the default routing branches — this is
    how error-path tests force a specific endpoint to misbehave without
    editing this router's defaults. An override value may also be a
    callable `(httpx.Request) -> httpx.Response` -- this is how a test makes
    one endpoint answer DIFFERENTLY on successive calls (e.g. a mutable
    counter closure returning 401 on the first call and 200 after, as the
    401-retry tests need). Every existing override in the suite is a plain
    `httpx.Response`, so callable support is purely additive.
    """
    overrides = overrides or {}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path  # includes the '/v5' base_url prefix (Pitfall 2)

        if path in overrides:
            value = overrides[path]
            return value(request) if callable(value) else value

        if path == "/v5/login.json":
            return httpx.Response(200, json=_load("login.json"))
        if path == "/v5/frames.json":
            return httpx.Response(200, json=_load("frames.json"))
        if path.endswith("/assets.json"):
            # None on page 1 (key stripped entirely, not sent as ""), a
            # token string once the cursor branch kicks in (Pitfall 3).
            cursor = request.url.params.get("cursor")
            fixture = "assets_page2.json" if cursor else "assets_page1.json"
            return httpx.Response(200, json=_load(fixture))
        if path.startswith("/v5/frames/") and path.endswith(".json") \
                and "/assets" not in path and "/activities" not in path:
            # get_frame(frame_id) detail route (Phase 6) — must come after the
            # exact "/v5/frames.json" and "/assets.json" branches above so it
            # doesn't shadow either (Pitfall 1).
            return httpx.Response(200, json=_load("frame_detail.json"))

        return httpx.Response(404, json=_load("error_envelope.json"))

    return handler


def offline_aura(overrides: dict | None = None):
    """Build a fully-wired `Aura` instance backed entirely by canned
    fixtures — the exact `Aura(client=Client(transport=MockTransport(...)))`
    two-seam composition Plan 01 introduced. Imports are local to avoid a
    module-level import cycle concern, matching the PATTERNS.md sketch.
    """
    from pushframe.aura import Aura
    from pushframe.client import Client

    transport = httpx.MockTransport(make_router(overrides))
    return Aura(client=Client(transport=transport))

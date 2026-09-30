import os

import pytest
from dotenv import load_dotenv

# Load AURA_EMAIL / AURA_PASSWORD from a local .env so the live read-path tests
# can run without exporting shell vars. Shell-exported vars still win (override
# defaults to False), and a missing .env is a no-op — so a credential-less
# checkout still skips the live suite cleanly (D-02).
load_dotenv()


# ---------------------------------------------------------------------------
# Network guard (CI hardening, 2026-09-30). An OFFLINE test must never reach
# the real pushd API. Phase 24's token-aware relogin default let 9 offline
# tests silently depend on a local .env (the env-password branch) and fail in
# CI — the mock was bypassed because nothing blocked the DEFAULT transport.
# While no live test is actually going to run, patch httpx.Client so any
# client built WITHOUT an explicit transport (the real-network shape) gets a
# blocking one: every request fails loudly. offline_aura-style tests are
# unaffected (they pass transport= explicitly); `live` runs opt out.
# ---------------------------------------------------------------------------
_NETWORK_GUARD_ACTIVE = False
_CURRENT_ITEM = None


def pytest_runtest_setup(item):
    """Track the current item so the guard can allow live-marked tests."""
    global _CURRENT_ITEM
    _CURRENT_ITEM = item


def pytest_runtest_teardown(item):
    global _CURRENT_ITEM
    _CURRENT_ITEM = None


def _blocking_transport():
    import httpx

    def handler(request: httpx.Request) -> httpx.Response:
        raise RuntimeError(
            'NETWORK GUARD: an offline test reached the real network at '
            f'{request.url}. Offline tests must go through '
            'tests.offline.offline_aura (explicit transport) or an injected '
            'seam. If a product default now escapes the seam, fix the TEST '
            '(e.g. pin relogin=aura.login), not the product.'
        )

    return httpx.MockTransport(handler)


def pytest_collection_modifyitems(config, items):
    """Install the network guard for the whole session. It is ALWAYS active:
    every transport-less httpx.Client gets a blocking transport, EXCEPT when
    built DURING a live-marked test (the only legitimate real-network shape:
    the session `aura` fixture constructs the client inside the live test,
    so the marker is checked at construction time — not at collection, which
    would let a local .env masquerade as a live posture for offline tests)."""
    global _NETWORK_GUARD_ACTIVE
    import httpx

    original = httpx.Client.__init__

    def guarded(self, *args, **kwargs):
        live_running = _CURRENT_ITEM is not None and \
            _CURRENT_ITEM.get_closest_marker('live') is not None
        # httpx.Client's signature is keyword-only; `app` implies transport.
        if not live_running and 'transport' not in kwargs and 'app' not in kwargs:
            kwargs['transport'] = _blocking_transport()
        return original(self, *args, **kwargs)

    # Detection lives ON the patched __init__ (NOT an importable conftest
    # flag): pytest registers conftest under its own module name, so a test
    # importing tests.conftest would see a second, never-mutated copy.
    guarded._network_guard = True
    httpx.Client.__init__ = guarded
    _NETWORK_GUARD_ACTIVE = True


@pytest.fixture(scope="session")
def aura():
    """Authenticated Aura session shared by all live read-path tests.

    Reads AURA_EMAIL/AURA_PASSWORD from the environment and skips the whole
    live suite cleanly when either is unset (D-02), so a credential-less
    checkout stays green. When credentials are present it instantiates Aura()
    and performs the login (READ-01's act, D-03), returning the authenticated
    instance so all live tests reuse one session.
    """
    email = os.getenv("AURA_EMAIL")
    password = os.getenv("AURA_PASSWORD")
    if not email or not password:
        pytest.skip("AURA_EMAIL/AURA_PASSWORD not set; skipping live Aura API tests")

    from pushframe.aura import Aura

    instance = Aura()
    instance.login()
    return instance

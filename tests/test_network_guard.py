"""The network guard itself (2026-09-30 CI hardening).

Meta-test: when the guard is installed, a client built WITHOUT an explicit
transport — the real-network shape — must fail loudly instead of reaching
pushd. When it is not installed (a genuine live posture), there is nothing
to assert and the test skips.
"""
import httpx
import pytest


def _guard_installed() -> bool:
    """True when conftest's blocking transport is patched onto httpx.Client.

    Detection reads the attribute the hook sets on the patched __init__
    itself — importing the flag from conftest would read a second module
    copy (pytest registers conftest under its own name).
    """
    return getattr(httpx.Client.__init__, '_network_guard', False)


def test_guard_blocks_transportless_clients_in_offline_posture():
    if not _guard_installed():
        pytest.skip('guard not installed — a genuine live posture is selected')
    client = httpx.Client(base_url='https://api.pushd.com/v5')
    with pytest.raises(RuntimeError, match='NETWORK GUARD'):
        client.get('/frames.json')  # any request: the transport itself blocks


def test_guard_never_blocks_explicit_transports():
    """offline_aura's shape: transport= passes through untouched."""
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(200, json={'ok': True})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    r = client.get('https://api.pushd.com/v5/frames.json')
    assert r.status_code == 200
    assert calls == ['/v5/frames.json']

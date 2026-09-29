"""Offline tests for the Client's rate-limit / lockout classification
(select-asset-401-unauthorized preventive fix, part 3).

Every request method (get/post/delete/put) must convert an HTTP 429 or the
custom Pushd 475 into a `RateLimitError` -- carrying the status code, the
parsed Retry-After, and the server's body message -- BEFORE the generic
`raise_for_status()` runs. Ordinary 4xx responses (e.g. 404) must still
raise `httpx.HTTPStatusError`, not `RateLimitError`.

Driven entirely through `httpx.MockTransport`: zero network, no credentials.
"""
import httpx
import pytest
from loguru import logger

from pushframe.client import Client, RateLimitError, _parse_retry_after


@pytest.fixture(autouse=True)
def _reset_loguru():
    # _raise_if_rate_limited emits a logger.warning; keep the process-global
    # loguru sink from firing against a torn-down sink from another test.
    logger.remove()
    yield
    logger.remove()


def _client(status, *, headers=None, body=None):
    """Build a Client whose every request returns the given canned response."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, headers=headers or {}, json=body if body is not None else {})

    return Client(transport=httpx.MockTransport(handler))


# ---------------------------------------------------------------------------
# _parse_retry_after (pure helper)
# ---------------------------------------------------------------------------

def test_parse_retry_after_numeric_returns_int():
    assert _parse_retry_after('30') == 30


def test_parse_retry_after_http_date_returns_raw_string():
    assert _parse_retry_after('Wed, 21 Oct 2026 07:28:00 GMT') == 'Wed, 21 Oct 2026 07:28:00 GMT'


def test_parse_retry_after_absent_or_blank_returns_none():
    assert _parse_retry_after(None) is None
    assert _parse_retry_after('   ') is None


# ---------------------------------------------------------------------------
# 429 / 475 -> RateLimitError
# ---------------------------------------------------------------------------

def test_post_429_raises_rate_limit_error_with_numeric_retry_after():
    client = _client(429, headers={'Retry-After': '30'}, body={'message': 'slow down'})

    with pytest.raises(RateLimitError) as exc_info:
        client.post('/frames/f/select_asset.json', data={'assets': []})

    err = exc_info.value
    assert err.status_code == 429
    assert err.retry_after == 30
    assert err.server_message == 'slow down'
    assert 'back off' in str(err).lower()
    assert '30s' in str(err)


def test_post_475_raises_rate_limit_error_with_server_message():
    # The exact escalation observed live in the debug session: valid creds,
    # custom 475, "The email or password was incorrect." with no Retry-After.
    client = _client(475, body={'error': True, 'message': 'The email or password was incorrect.'})

    with pytest.raises(RateLimitError) as exc_info:
        client.post('/login.json', data={'user': {}})

    err = exc_info.value
    assert err.status_code == 475
    assert err.retry_after is None
    assert err.server_message == 'The email or password was incorrect.'


def test_rate_limit_on_get_read_path_also_classified():
    # A throttle that first shows up on a read must be classified too.
    client = _client(429, headers={'Retry-After': '5'})

    with pytest.raises(RateLimitError) as exc_info:
        client.get('/frames.json')

    assert exc_info.value.status_code == 429


def test_delete_and_put_also_classify_rate_limit():
    for method in ('delete', 'put'):
        client = _client(475)
        with pytest.raises(RateLimitError):
            getattr(client, method)('/frames/f.json')


def test_retry_after_http_date_preserved_on_error():
    client = _client(429, headers={'Retry-After': 'Wed, 21 Oct 2026 07:28:00 GMT'})

    with pytest.raises(RateLimitError) as exc_info:
        client.get('/frames.json')

    assert exc_info.value.retry_after == 'Wed, 21 Oct 2026 07:28:00 GMT'


def test_rate_limit_with_non_json_body_still_raises_cleanly():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, text='<html>Too Many Requests</html>')

    client = Client(transport=httpx.MockTransport(handler))

    with pytest.raises(RateLimitError) as exc_info:
        client.get('/frames.json')

    # Non-JSON body must not leak a JSONDecodeError; server_message is just None.
    assert exc_info.value.server_message is None


# ---------------------------------------------------------------------------
# Ordinary 4xx must NOT be reclassified
# ---------------------------------------------------------------------------

def test_ordinary_404_still_raises_http_status_error_not_rate_limit():
    client = _client(404, body={'error': 'not_found'})

    with pytest.raises(httpx.HTTPStatusError):
        client.get('/frames.json')


def test_ordinary_401_still_raises_http_status_error_not_rate_limit():
    # A genuine 401 (not one of the two throttle codes) stays an
    # HTTPStatusError -- we deliberately classify only 429/475 as back-off
    # signals to avoid masking real auth failures.
    client = _client(401, body={'error': 'unauthorized'})

    with pytest.raises(httpx.HTTPStatusError):
        client.post('/frames/f/select_asset.json', data={})


def test_401_exception_message_carries_redacted_server_body():
    """Phase 23.5 (venus): a 401's message must carry the server body (the
    trip-vs-token discriminator), redacted through the same filter as the
    request logs."""
    client = _client(401, body={'error': True,
                                'auth_token': 'sk-journey-should-not-leak'})

    with pytest.raises(httpx.HTTPStatusError) as exc_info:
        client.post('/frames/f/select_asset.json', data={'assets': []})

    msg = str(exc_info.value)
    assert 'server body:' in msg
    assert 'sk-journey-should-not-leak' not in msg      # redacted
    assert '***REDACTED***' in msg


def test_401_with_non_json_body_names_the_raw_text():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text='<html>blocked</html>')

    client = Client(transport=httpx.MockTransport(handler))

    with pytest.raises(httpx.HTTPStatusError) as exc_info:
        client.post('/frames/f/select_asset.json')

    assert 'blocked' in str(exc_info.value)


def test_401_without_body_keeps_the_plain_message():
    client = _client(401, body={})

    with pytest.raises(httpx.HTTPStatusError) as exc_info:
        client.post('/frames/f/select_asset.json')

    # `{}` serializes to '{}' — still present, harmless.
    assert 'server body:' in str(exc_info.value)


def test_non_401_4xx_keep_their_plain_message():
    client = _client(403, body={'error': 'forbidden'})

    with pytest.raises(httpx.HTTPStatusError) as exc_info:
        client.get('/frames.json')

    assert 'server body:' not in str(exc_info.value)

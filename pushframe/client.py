import copy
import json
from collections import deque
from typing import Optional, Deque

import httpx
from httpx import Response, Timeout
from loguru import logger

# Phase 23: the API endpoint is now resolved at CONSTRUCTION time through
# settings (env → config file → default), so PUSHFRAME_API_BASE_URL — long
# declared in DEFAULTS but never consumed — finally takes effect (the
# config-wizard container journey points it at a fake API). Module aliases
# kept for any legacy reader; defaults are byte-identical to 5.0.x.
from pushframe.utils import settings as _settings

AURA_API_BASE_URL = _settings.AURA_API_BASE_URL
AURA_API_VERSION = _settings.AURA_API_VERSION
USER_AGENT = 'Aura/4.7.790 (Android 30; Client)'

# Keys whose values are secrets and must never reach the on-disk logs (D-07).
_REDACT_KEYS = {'password', 'auth_token', 'x-token-auth'}
_REDACTED = '***REDACTED***'

# Status codes that mean "the server is throttling / has locked out this
# account" rather than a per-request client error. 429 is the standard
# Too Many Requests; 475 is Pushd's non-standard code observed during the
# select-asset-401-unauthorized debug session — returned with valid
# credentials once the account's write burst tripped the anti-abuse layer,
# and it escalates to reject login too. Both are treated as a single
# back-off-and-stop signal (see RateLimitError) so a tripped batch aborts
# with one clear message instead of N misleading per-item 401s.
_RATE_LIMIT_STATUS_CODES = {429, 475}


class AuraError(Exception):
    """Package-wide exception root for pushframe's own control-flow types
    (MOD-03/D-20). This is a MARKER base, not a codebase-wide taxonomy --
    most of this codebase still raises bare ``RuntimeError``/``ValueError``
    deliberately (MOD-03 scopes typed-exception conversion to the write
    path only, "where it pays", not a full rewrite).

    Catching ``AuraError`` broadly is deliberately discouraged and, inside
    `pushframe/sync.py`, actively prohibited: `execute_plan`'s write-loop
    except-ladder depends on `RateLimitError` and
    `ConsecutiveWriteFailureError` each being matched by their OWN specific
    branch before any broader handler could see them. An `except AuraError`
    introduced there would silently swallow both and break that ordering.
    """
    pass


class AuthenticationError(AuraError):
    """Raised by `execute_plan`'s write-chunk 401 retry (REL-01/REL-04,
    D-01/D-02) when a write returns HTTP 401, a re-login is attempted to
    discriminate a genuine authentication failure from an anti-abuse trip,
    and that re-login itself raises.

    This is a HARD STOP, never a transient/retryable condition: two write
    401s in a row would be ambiguous on their own, but a re-login that
    *itself* fails means the credentials are bad or revoked. The write is
    never retried and the failure is never re-labelled transient.

    Carries `underlying` -- the exception the re-login raised (or `None`)
    -- for callers/logs. The message names the failure class and
    `type(underlying).__name__` ONLY; it must never interpolate a response
    body, an auth token, or the password (T-11-02).
    """

    def __init__(self, underlying: Exception | None = None):
        self.underlying = underlying
        underlying_name = type(underlying).__name__ if underlying is not None else 'unknown error'
        super().__init__(
            f'Re-login failed after a write returned HTTP 401 -- treating this as a '
            f'genuine authentication failure (bad or revoked credentials), not a '
            f'transient/retryable anti-abuse trip. Underlying: {underlying_name}.'
        )


class WriteEndpointError(AuraError):
    """The write-path form of an Aura error-envelope response (MOD-03/D-20):
    the call reached the server, the server answered HTTP 200, and the body
    carried an `error` key -- distinct from a network/HTTP-status failure.

    Converted from exactly two bare `RuntimeError`s in
    `pushframe/api/assetApi.py` (`batch_update`, `delete_asset`). Every
    other write-endpoint error envelope (`select_asset`/`exclude_asset`/
    `remove_asset` in `pushframe/api/frameApi.py`) and every read-path
    raise is deliberately left as `RuntimeError` this phase -- D-20 scopes
    conversion narrowly so a phase about the write path's *trustworthiness*
    does not also become the widest-blast-radius rewrite of its *type
    surface*.
    """
    pass


class RateLimitError(AuraError):
    """Raised when the Aura/Pushd API signals rate-limiting or an account
    lockout (HTTP 429 or the custom 475).

    Carries the offending ``status_code`` and, when the server provided a
    ``Retry-After`` header, ``retry_after`` (an int number of seconds when
    the header was numeric, otherwise the raw header string — e.g. an
    HTTP-date). Distinct from ``httpx.HTTPStatusError`` so callers can
    abort a whole batch and surface a single "back off" message rather than
    treating it as one of many per-item failures.
    """

    def __init__(self, status_code: int, retry_after=None, server_message: str | None = None):
        self.status_code = status_code
        self.retry_after = retry_after
        self.server_message = server_message

        detail = (
            f' Retry after {retry_after}s.'
            if isinstance(retry_after, int)
            else (f' Retry-After: {retry_after}.' if retry_after else '')
        )
        server = f' Server said: {server_message}.' if server_message else ''
        super().__init__(
            f'Aura API is rate-limiting or has locked out this account '
            f'(HTTP {status_code}). Stop and back off before retrying; '
            f'continued calls may extend the lockout.{detail}{server}'
        )


def _parse_retry_after(raw: str | None):
    """Parse a ``Retry-After`` header value.

    Returns an ``int`` when the header is a plain number of seconds, the
    stripped raw string when it is an HTTP-date (or otherwise non-numeric),
    or ``None`` when the header is absent. Stdlib only — no date parsing is
    attempted; a non-numeric value is surfaced verbatim for the human.
    """
    if raw is None:
        return None
    raw = raw.strip()
    if not raw:
        return None
    return int(raw) if raw.isdigit() else raw


def _redact(value):
    """Return a deep copy of a dict/list with secret-bearing keys masked.

    Recurses into nested dicts (e.g. the ``user`` sub-dict of the login payload)
    and lists so a secret can never leak from a deeper level. Non-container
    values are returned unchanged. Stdlib only.
    """
    if isinstance(value, dict):
        return {
            k: (_REDACTED if k in _REDACT_KEYS else _redact(v))
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return copy.copy(value)


# Use something similar to:
# https://github.com/sudoguy/tiktokpy/blob/master/tiktokpy/client/__init__.py
# https://github.com/mkb79/Audible/tree/master/src/audible
# https://github.com/ssut/py-googletrans/blob/master/googletrans/client.py


# TODO: This should be reworked to be async, particularly for mass uploads/clones.

class Client:

    def __init__(self, history_len: int = 30, transport: httpx.BaseTransport | None = None,
                 base_url: str | None = None):
        # TEST-01 candidate #4 (Phase 19): base_url is injectable for offline
        # tests; None keeps the historical composition byte-identical.
        # settings.AURA_API_BASE_URL is the FULL API root — its default is
        # 'https://api.pushd.com/v5' (version suffix baked in, as declared in
        # the settings table since the AWS closeout). PUSHFRAME_API_BASE_URL
        # therefore overrides the whole root, version included.
        resolved_base_url = base_url or str(_settings.AURA_API_BASE_URL)
        self.http2_client = httpx.Client(http2=True, base_url=resolved_base_url, headers={
            'accept-language': 'en-US',
            'cache-control': 'no-cache',
            'user-agent': USER_AGENT,
            'content-type': 'application/json; charset=utf-8',
        }, timeout=Timeout(timeout=20.0), transport=transport)

        self.history: Deque[Response] = deque(maxlen=history_len)

    def get(self, url, query_params: Optional[dict] = None, headers: Optional[dict] = None):
        query_params = {k: v for k, v in query_params.items() if v is not None} if query_params else None
        logger.info(f'GET request to {url}', query_params=query_params, headers=headers)
        response = self.http2_client.get(url=url, params=query_params, headers=headers)

        self.history.append(response)
        self._raise_if_rate_limited(response)
        self._raise_for_status_with_body(response)
        logger.debug(f'Response ({response.status_code}), body: {_redact(response.json())}')

        self._set_cookies(response)

        return response.json()

    def post(self, url, data: dict = None, query_params: Optional[dict] = None, headers: Optional[dict] = None):
        logger.info(f'POST request to {url}', data=_redact(data), query_params=query_params, headers=headers)
        response = self.http2_client.post(url=url, json=data, headers=headers, params=query_params)

        self.history.append(response)
        self._raise_if_rate_limited(response)
        self._raise_for_status_with_body(response)
        logger.debug(f'Response ({response.status_code}), body: {_redact(response.json())}')

        self._set_cookies(response)

        return response.json()

    def delete(self, url, query_params: Optional[dict] = None, headers: Optional[dict] = None):
        logger.info(f'DELETE request to {url}', query_params=query_params, headers=headers)
        response = self.http2_client.delete(url=url, headers=headers, params=query_params)

        self.history.append(response)
        self._raise_if_rate_limited(response)
        self._raise_for_status_with_body(response)
        logger.debug(f'Response ({response.status_code}), body: {_redact(response.json())}')

        self._set_cookies(response)

        return response.json()

    def put(self, url, data: dict = None, query_params: Optional[dict] = None, headers: Optional[dict] = None):
        logger.info(f'PUT request to {url}', data=_redact(data), query_params=query_params, headers=headers)
        response = self.http2_client.put(url=url, json=data, headers=headers, params=query_params)

        self.history.append(response)
        self._raise_if_rate_limited(response)
        self._raise_for_status_with_body(response)
        logger.debug(f'Response ({response.status_code}), body: {_redact(response.json())}')

        self._set_cookies(response)

        return response.json()

    def _raise_for_status_with_body(self, response: httpx.Response) -> None:
        """`raise_for_status()`, except an HTTP 401's exception message carries
        the server's (redacted, truncated) response body.

        Phase 23.5 venus regression: writes 401'd while login stayed green —
        the bare "Client error '401 Unauthorized'" told the operator nothing
        about WHY. The Pushd body is the discriminator (an anti-abuse trip
        and a session/token rejection carry different payloads), and the
        exception message is what every failure path (per-item reasons,
        verify-probe errors, the consecutive-failures abort) already
        propagates — enrich it once here and every surface diagnoses.
        """
        try:
            response.raise_for_status()
            return
        except httpx.HTTPStatusError as e:
            if response.status_code != 401:
                raise
            body_text = self._safe_body_text(response)
            if not body_text:
                raise
            logger.warning(
                f"HTTP 401 from {response.request.url} — server body: {body_text}")
            raise httpx.HTTPStatusError(
                f"401 Unauthorized for {e.request.url} — server body: {body_text}",
                request=e.request, response=e.response) from e

    @staticmethod
    def _safe_body_text(response: httpx.Response, limit: int = 300) -> str:
        """Best-effort readable body: JSON (redacted through the same filter
        as the request logs) or raw text, always truncated to `limit`."""
        try:
            body = _redact(response.json())
            text = json.dumps(body, default=str)
        except Exception:
            text = (response.text or '').strip()
        return text if len(text) <= limit else text[:limit - 3] + '...'

    def _raise_if_rate_limited(self, response: httpx.Response) -> None:
        """Convert a rate-limit / lockout response (HTTP 429 or 475) into a
        `RateLimitError` before the generic `raise_for_status()` runs.

        Runs on every request method (read and write) so a throttle that
        first appears on a GET is classified just as clearly as one on a
        write. Reads the server's ``message`` body field (best-effort) and
        the ``Retry-After`` header so the raised error can tell the caller
        how long to wait.
        """
        if response.status_code not in _RATE_LIMIT_STATUS_CODES:
            return

        retry_after = _parse_retry_after(response.headers.get('retry-after'))
        server_message = None
        try:
            body = response.json()
            if isinstance(body, dict):
                server_message = body.get('message')
        except Exception:
            # A rate-limit response with a non-JSON body must still raise a
            # clean RateLimitError, never a JSON-decode error.
            server_message = None

        logger.warning(
            f'Rate-limited/locked-out response (HTTP {response.status_code}) '
            f'from {response.request.url}; aborting.'
        )
        raise RateLimitError(response.status_code, retry_after, server_message)

    def add_default_headers(self, headers: dict) -> None:
        self.http2_client.headers.update(headers)

    def _set_cookies(self, response: httpx.Response) -> None:
        if len(response.cookies):
            logger.debug(f'Response Cookies: {response.cookies}')

        for cookie_name, cookie_data in response.cookies.items():
            self.http2_client.cookies.set(cookie_name, cookie_data)

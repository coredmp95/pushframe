"""Google session client: full-cookie-jar httpx over photos.google.com.

Migrated from probes/browser_bootstrap.py's live-proven session builder
(phase 16). Two live-proven requirements drive the shape:

1. photos.google.com treats a FLATTENED name→value cookie dict as an
   anonymous visitor (redirect to the marketing page), while the complete
   jar — domain+path preserved from the harvest — yields the logged-in app
   page with the SNlM0e at-token. The jar is therefore built from the
   vault's FULL records, never from `cookies_for_httpx()`.
2. The User-Agent must match the harvesting browser's (Chrome); a
   mismatched UA gets redirected to `about/`.

The `transport=` seam mirrors `pushframe.client.Client`'s DI: offline tests
inject an httpx.MockTransport and never touch the network (TEST-02).

NOTE: the vault denylist refuses `pushframe.cli` — the CLI reaches the
session only through this package's `GoogleSession.from_vault`, which is the
boundary's intended enforcement point.
"""
from __future__ import annotations

import hashlib
import re
import time

import httpx

from pushframe.google import vault

PHOTOS_HOME = "https://photos.google.com/"

# UA of the harvesting browser (live-proven: required, else redirect to about/).
HARVEST_UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/153.0.0.0 Safari/537.36")

_AUTH_COOKIE_NAMES = ("SAPISID", "__Secure-1PAPISID", "__Secure-3PAPISID")

# Account email on the logged-in home page: the "oPEP7c" key with the
# account address as value (whitespace-tolerant on the JSON separator).
_EMAIL_RE = re.compile(r'"oPEP7c"\s*[,:]\s*"([^"@]+@[^"]+)"')

# The SNlM0e at-token (XSRF boilerplate batchexecute needs).
_SNLMOE_RE = re.compile(r'"SNlM0e"\s*:\s*"([^"]+)"')


class GoogleSessionError(RuntimeError):
    """Session-level failure: vault unusable or a page fetch failing loud."""


class GoogleSession:
    """An httpx session over photos.google.com built from the full cookie jar.

    Exposes `transport=` for offline tests (httpx.MockTransport), the
    session-usable check (`is_linked`), the SNlM0e at-token extraction, and
    the account email (for `status` — email only, never cookie values, D-02).
    """

    def __init__(self, cookie_records: list[dict], *, transport: httpx.BaseTransport | None = None,
                 user_agent: str = HARVEST_UA, timeout: float = 30.0) -> None:
        self.cookie_records = cookie_records
        self._sapisid: str | None = next(
            (c["value"] for c in cookie_records if c.get("name") in _AUTH_COOKIE_NAMES), None)
        # FULL jar: domain+path preserved (live-proven requirement — a
        # flattened dict reads as anonymous).
        jar = httpx.Cookies()
        for c in cookie_records:
            if "name" in c and "value" in c:
                jar.set(c["name"], c["value"],
                        domain=c.get("domain", ".google.com"), path=c.get("path", "/"))
        self.http = httpx.Client(
            cookies=jar,
            timeout=timeout,
            follow_redirects=True,
            transport=transport,
            headers={"User-Agent": user_agent,
                     "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8"},
        )
        self._home_text: str | None = None

    @classmethod
    def from_vault(cls, *, path: str | None = None,
                   **kwargs) -> "GoogleSession":
        """Build a session from the cookie vault (denylist check fires inside
        vault.load()). Falls back to the legacy probe vault path (soft migration).
        """
        return cls(vault.load(path=path), **kwargs)

    @property
    def sapisid(self) -> str | None:
        """SAPISID value from the harvest, for SAPISIDHASH Authorization."""
        return self._sapisid

    def home_text(self) -> str:
        """The logged-in photos home page text (fetched once, then cached)."""
        if self._home_text is None:
            resp = self.http.get(PHOTOS_HOME)
            if resp.status_code != 200:
                raise GoogleSessionError(
                    f"photos.google.com home returned HTTP {resp.status_code} "
                    f"with session cookies attached — failing loud"
                )
            self._home_text = resp.text
        return self._home_text

    def is_linked(self) -> bool:
        """Session-usable check (no exception on a dead session).

        Linked iff the photos home answers 200 with the SNlM0e at-token. A
        dead/expired session gets redirected to the marketing page (no
        at-token) — the live-proven anonymous signal.
        """
        try:
            m = _SNLMOE_RE.search(self.home_text())
        except GoogleSessionError:
            return False
        return m is not None

    def at_token(self) -> str | None:
        """The SNlM0e at-token (XSRF boilerplate for batchexecute calls)."""
        m = _SNLMOE_RE.search(self.home_text())
        return m.group(1) if m else None

    def account_email(self) -> str | None:
        """The linked account's email address (for `status`; D-02: email only,
        never cookie values). None when the page shape carries no email row."""
        m = _EMAIL_RE.search(self.home_text())
        return m.group(1) if m else None

    def authorization_header(self, origin: str = "https://photos.google.com") -> str | None:
        """SAPISIDHASH Authorization value, or None when no SAPISID cookie —
        some batchexecute calls expect it (xob0t/Google-Photos-Toolkit shape)."""
        if not self._sapisid:
            return None
        now_ms = int(time.time() * 1000)
        digest = hashlib.sha1(f"{now_ms} {self._sapisid} {origin}".encode()).hexdigest()
        return f"SAPISIDHASH {now_ms}_{digest}"

    def close(self) -> None:
        self.http.close()

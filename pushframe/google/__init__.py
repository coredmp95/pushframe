"""Google Photos integration package (phase 17 — LGS-02..05, TEST-02).

The phase-16-proven mechanics, migrated from the `probes/` instruments into
production shape:

- `GoogleSession` (`client`) — full-cookie-jar httpx client over
  photos.google.com, built from the 0600 cookie vault (`vault`, denylist
  boundary carried over verbatim), with the harvesting browser's UA;
- parsers (`parsers`) — share-page ds:1 and snAcKc RPC payloads (shared §1b
  item shape), batchexecute envelope splitting, fail-loud everywhere;
- enumeration (`enumerate`) — the snAcKc continuation loop (300/page, AH_
  token swap, clean exhaustion) and the disk-weight measurer (1-byte Range
  GETs → Content-Range totals);
- redaction (`redaction`) — every printable URL goes through the helpers.

Everything Google-facing is offline-tested through injected transports
(TEST-02): zero live network in the test suite.
"""
from pushframe.google.client import GoogleSession, GoogleSessionError
from pushframe.google.parsers import (
    BatchexecuteEntry,
    ProbeParseError,
    SnackcPage,
    parse_af_initdata,
    parse_batchexecute,
    parse_snackc_payload,
)
from pushframe.google.redaction import redact_link, redact_tokens
from pushframe.google.vault import CookieVaultError

__all__ = [
    "GoogleSession",
    "GoogleSessionError",
    "CookieVaultError",
    "ProbeParseError",
    "SnackcPage",
    "BatchexecuteEntry",
    "parse_af_initdata",
    "parse_snackc_payload",
    "parse_batchexecute",
    "redact_link",
    "redact_tokens",
]

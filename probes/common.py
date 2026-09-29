"""Shared utilities for the Phase 16 access-mechanism probes.

Everything a probe prints that could carry a capability URL goes through
`redact_link()` first (D-05: committed docs and terminal scrollback carry the
truncated shape only — `AF1Qip…<last4>`). `fetch()` is a fail-loud raw HTTP
wrapper: a non-200 is an exception, never a silent pass, matching the repo's
convention of honest failures over optimistic defaults.

Phase 17 (plan 17-01 T3): the redaction helpers MOVED to
pushframe/google/redaction.py — this module re-exports them so the probes'
import surface stays identical.
"""
from __future__ import annotations

import sys

import httpx

# Single source of truth for redaction lives in the package now.
from pushframe.google.redaction import redact_link, redact_tokens  # noqa: E402,F401


def fetch(url: str, *, timeout: float = 30.0) -> httpx.Response:
    """GET a URL fail-loudly: raise on non-200, never return a silent pass.

    Plain HTTP/2-capable httpx, no cookies, no browser. 30s timeout; no
    retry loop (T-16-04: single-album low-frequency fetches must not look
    like bulk scraping).
    """
    resp = httpx.get(url, timeout=timeout, follow_redirects=True)
    if resp.status_code != 200:
        raise RuntimeError(
            f"probe fetch failed: HTTP {resp.status_code} for {redact_link(url)} "
            f"({len(resp.content)} bytes) — failing loud per probe convention"
        )
    return resp


def fail_loud(message: str) -> None:
    """Print a failure and exit non-zero — probes never exit 0 on a bad run."""
    print(f"PROBE FAILED: {message}", file=sys.stderr)
    raise SystemExit(1)

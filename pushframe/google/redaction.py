"""Redaction helpers for every Google-facing print/log surface (T-17-03).

Migrated from probes/common.py. Capability URLs and AH_ cursors are
secret-like: they grant album/read access to anyone holding them. Every
downstream print site uses these helpers — the full shape is never echoed
to stdout, never written to any committed file.
"""
from __future__ import annotations

import re

# A full share-link/album token: AF1Qip followed by the ~48-char id portion.
_FULL_TOKEN_RE = re.compile(r"AF1Qip[A-Za-z0-9_-]{40,}")


def redact_link(url: str | None) -> str:
    """Return the truncated capability-URL shape for any URL string.

    `https://photos.google.com/share/AF1QipXXXXXXXX...YYYY` becomes
    `photos.google.com/share/AF1Qip…YYYY` — enough to correlate two links
    as different, never enough to resolve either. Idempotent: a string with
    no full token passes through with the scheme stripped.
    """
    if not url:
        return "(none)"
    text = _FULL_TOKEN_RE.sub(lambda m: f"AF1Qip…{m.group(0)[-4:]}", url)
    return text.replace("https://", "")


def redact_tokens(text: str) -> str:
    """Redact every full capability token embedded in arbitrary text (e.g. a
    raw error body we are about to print or record)."""
    return _FULL_TOKEN_RE.sub(lambda m: f"AF1Qip…{m.group(0)[-4:]}", text)

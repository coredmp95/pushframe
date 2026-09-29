"""Shared-album-link probe (Phase 16, plan 16-01 — LGS-01/LGS-06).

Fetches a Google Photos shared-album page with plain HTTP — no cookies, no
login, no JS — parses the inline `AF_initDataCallback({key: 'ds:1', ...})`
payload into the album's item list, and optionally downloads originals via
the `{baseUrl}=d` convention, hashing them with the frame's own base64-MD5
convention (`pushframe.aws.s3client.get_md5`) for the 16-03 fidelity
comparison.

Phase 17 (plan 17-01 T3): the parsers MOVED into the production package
`pushframe/google/parsers.py` — this module imports them (single source of
truth, D-03) and keeps its CLI surface unchanged.

Usage:
    uv run python probes/shared_link_probe.py <share_url> [--download-n N] [--out DIR]

Privacy: every URL printed goes through redaction (`redact_link`) — the
full capability URL is never echoed to stdout and never written to any
committed file. Full links live only in untracked `probes/*.link` files or
in the shell invocation itself (D-05 privacy tiers).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import httpx

# Import path shim so `python probes/shared_link_probe.py` works from the
# repo root without installing the probes package.
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from probes.common import fetch, redact_link, redact_tokens  # noqa: E402,F401

# The proven parsers now live in the package; the probe re-exports them so
# existing probe-side callers (fidelity_check.py, browser_bootstrap.py) and
# the probe tests keep working unchanged.
from pushframe.google.parsers import (  # noqa: E402,F401
    ProbeParseError,
    _walk_items,
    extract_ds1_data,
    parse_af_initdata,
    parse_snackc_payload,
)

SUSPECTED_CEILING = 500


def resolve_share_url(url: str) -> tuple[str, httpx.Response]:
    """Follow app.goo.gl 302s to the final photos.google.com/share/... URL."""
    resp = fetch(url)  # follow_redirects=True in common.fetch
    return str(resp.url), resp


def download_original(base_url: str, dest: Path) -> tuple[int, str]:
    """Download the `{baseUrl}=d` original, return (byte_length, base64_md5).

    Hash comes from the repo's own convention — pushframe.aws.s3client.get_md5 —
    the exact function the frame's md5_hash provenance rests on (Phase 7).
    Bytes are hashed raw; no re-encoding, no Pillow round-trip.
    """
    from pushframe.aws.s3client import get_md5

    url = f"{base_url}=d"
    resp = httpx.get(url, timeout=60.0, follow_redirects=True)
    if resp.status_code != 200:
        raise RuntimeError(
            f"=d download failed: HTTP {resp.status_code} for a media baseUrl "
            f"(sha-prefix {base_url[-6:]}) — failing loud"
        )
    data = resp.content
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    return len(data), get_md5(data)


def measure_sizes(base_urls: list[str]) -> list[int]:
    """Exact byte sizes via 1-octet Range GETs on `{baseUrl}=d`.

    Google answers `Content-Range: bytes 0-0/TOTAL` — the album's total disk
    weight is measurable without downloading any photo (live-proven 2026-09-28:
    album C, 24 items → 86.6 MiB, every item answered with its exact size).
    Migrated to the package as pushframe.google.enumerate.measure_disk_weight;
    this probe keeps a thin local copy to stay a self-contained diagnostic
    over plain httpx (no vault/session needed for the anonymous flow).
    """
    sizes: list[int] = []
    http = httpx.Client(timeout=30.0)
    for base in base_urls:
        r = http.get(f"{base}=d", headers={"Range": "bytes=0-0"})
        cr = r.headers.get("content-range", "")
        size = int(cr.rsplit("/", 1)[-1]) if "/" in cr else None
        if size is None:
            cl = r.headers.get("content-length")
            size = int(cl) if cl else 0
        sizes.append(size)
    return sizes


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("share_url", help="photos.google.com/share/... or app.goo.gl link")
    parser.add_argument("--download-n", type=int, default=0,
                        help="download the first N originals via =d (default 0: listing only)")
    parser.add_argument("--sizes", action="store_true", dest="measure_sizes",
                        help="measure every item's exact byte size via 1-byte Range GETs "
                             "(album disk weight without downloading photos)")
    parser.add_argument("--out", type=Path, default=Path("probes/.probe-downloads"),
                        help="download directory (gitignored)")
    args = parser.parse_args(argv)

    final_url, resp = resolve_share_url(args.share_url)
    print(f"final URL: {redact_link(final_url)}")
    print(f"HTTP status: {resp.status_code}, page bytes: {len(resp.content)}")

    items = parse_af_initdata(resp.text)
    print(f"item count: {len(items)}")
    for i, item in enumerate(items, 1):
        w = item["width"] if item["width"] is not None else "?"
        h = item["height"] if item["height"] is not None else "?"
        print(f"  {i:3d}. {item['id'][:14]}…  {w}x{h}  ts={item['ts_ms']}")

    if len(items) >= SUSPECTED_CEILING:
        verdict = (f"AT/ABOVE the suspected ~{SUSPECTED_CEILING} ceiling — ceiling "
                   f"MEASURED at {len(items)} items returned by one fetch")
    else:
        verdict = (f"below the suspected ~{SUSPECTED_CEILING} ceiling "
                   f"(lower bound so far: {len(items)})")
    print(f"ceiling verdict: {verdict}")

    if getattr(args, "measure_sizes", False):
        sizes = measure_sizes([i["base_url"] for i in items])
        total = sum(sizes)
        print(f"disk weight: {total:,} bytes = {total / 1024 / 1024:.1f} MiB "
              f"({len(sizes)} items, min {min(sizes):,}, max {max(sizes):,}, "
              f"avg {total // max(len(sizes), 1):,})")

    if args.download_n > 0:
        for item in items[: args.download_n]:
            dest = args.out / f"{item['id']}.bin"
            length, digest = download_original(item["base_url"], dest)
            print(f"downloaded: {dest.name}  bytes={length}  base64_md5={digest}")

    # Safety net: nothing printed above may contain a full capability token.
    # (redact_link is applied at the print sites; this guards regressions.)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

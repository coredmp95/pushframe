"""Byte-fidelity check (plan 16-03 T1 — LGS-06).

Downloads `=d` originals from the probed shared albums, hashes each with the
frame's own convention (`pushframe.aws.s3client.get_md5`), and checks
membership against the md5_hash set of the frame's standing assets
(read-only `get_assets` with filter=all — no frame writes).

A hit means: the Google-side bytes are byte-identical to what the frame
stores and reports — the diff engine's load-bearing assumption, proven live.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv  # noqa: E402

from probes.common import fetch, redact_link  # noqa: E402
from probes.shared_link_probe import parse_af_initdata  # noqa: E402


def main() -> int:
    load_dotenv(".env")
    links = [Path(p).read_text().strip() for p in sys.argv[1:]]

    from pushframe.aura import Aura
    from pushframe.aws.s3client import get_md5

    a = Aura()
    a.login()
    frames, _count = a.frame_api.get_frames()
    frame = frames[0]
    print(f"frame: {frame.name} (id {frame.id[:8]}…, num_assets {frame.num_assets})")
    assets = a.get_all_assets(frame.id)
    frame_hashes = {asset.md5_hash for asset in assets if asset.md5_hash}
    print(f"standing assets with md5_hash: {len(frame_hashes)}")

    import httpx

    results = []
    for link in links:
        html = fetch(link).text
        items = parse_af_initdata(html)
        print(f"\nalbum {redact_link(link)}: {len(items)} items; sampling all "
              f"(album small) or first 12 (large)")
        sample = items if len(items) <= 30 else items[:12]
        for item in sample:
            try:
                resp = httpx.get(f"{item['base_url']}=d", timeout=60.0)
                if resp.status_code != 200:
                    results.append((item["id"], None, None, f"HTTP {resp.status_code}"))
                    continue
                digest = get_md5(resp.content)
                match = digest in frame_hashes
                results.append((item["id"], len(resp.content), digest,
                                "MATCH" if match else "no-match"))
            except Exception as exc:  # noqa: BLE001 — record, don't die
                results.append((item["id"], None, None, f"error: {exc}"))
            time.sleep(0.4)  # politeness throttle

    print("\n=== Fidelity results ===")
    matches = 0
    for item_id, nbytes, digest, verdict in results:
        if verdict == "MATCH":
            matches += 1
        print(f"{item_id[:16]}…  {str(nbytes):>9}  {digest}  {verdict}")
    print(f"\nSUMMARY: {matches}/{len(results)} =d originals byte-identical to a "
          f"standing frame asset's md5_hash")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

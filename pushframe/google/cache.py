"""Pruned-disk download cache for Google album items (phase 18 plan 18-01).

The cache is a STAGING area only — `cache_dir/<google_media_id>` files hold
the exact `=d` bytes (never re-encoded; LGS-06: the bytes decide) while they
wait for their confirmed frame upload, and are pruned afterwards (CSE-04).
Nothing in the pipeline ever diffs by walking it — demand is rebuilt from the
album listing plus the manifest (CSE-03, plan 18-02 builds that side).

SAFE-04 discipline: a download that fails (HTTP error, transport error, or a
Content-Length/expected-size mismatch) is recorded in CacheOutcome.failed and
its bytes are NEVER written, NEVER hashed — a failed or partial download can
never become uploadable junk bytes.

Concurrency is confined to this module (D-06 / CSE-01): a bounded worker pool
over the GoogleSession's httpx client. It imports nothing from the sync/apply
side of the project and never touches an Aura client, so concurrent code
structurally cannot reach the frame-write seam.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from pushframe.aws.s3client import get_md5


class CacheError(RuntimeError):
    """Cache staging failed in a way that must abort the sync run."""


@dataclass
class CacheOutcome:
    """The result of one download_to_cache run over an album listing.

    staged entries carry {"google_media_id", "path", "md5_hash",
    "size_bytes"} — verified, hash-annotated files ready for upload planning.
    `failed` carries (google_media_id, error) pairs; those ids are excluded
    from any plan (SAFE-04) and reported, never silently dropped.
    """

    staged: list[dict] = field(default_factory=list)
    failed: list[tuple[str, str]] = field(default_factory=list)
    skipped_manifest: int = 0
    videos_skipped: int | None = None

    @property
    def staged_by_id(self) -> dict[str, dict]:
        return {s["google_media_id"]: s for s in self.staged}


def _download_one(session, item: dict, cache_dir: Path,
                  expected_size: int | None) -> dict:
    """Fetch ONE item's `=d` bytes, verify length, write, hash.

    Raises on any failure — the caller records it in CacheOutcome.failed and
    guarantees no file was left behind for the failed id.
    """
    google_media_id = item["id"]
    resp = session.http.get(f"{item['base_url']}=d")
    if resp.status_code != 200:
        raise RuntimeError(f"=d download returned HTTP {resp.status_code}")
    body = resp.content
    declared = resp.headers.get("content-length")
    # A stream transport may not populate Content-Length even for complete
    # bodies; when the header is absent, the expected_size (phase 16's
    # Range-GET measurement) is the length check.
    if declared is not None and int(declared) != len(body):
        raise RuntimeError(
            f"truncated download: Content-Length {declared} != received {len(body)}"
        )
    if declared is None and expected_size is not None and expected_size != len(body):
        raise RuntimeError(
            f"truncated download: expected {expected_size} != received {len(body)}"
        )
    path = cache_dir / google_media_id
    path.write_bytes(body)
    return {
        "google_media_id": google_media_id,
        "path": str(path),
        "md5_hash": get_md5(body),
        "size_bytes": len(body),
    }


def download_to_cache(session, listing, cache_dir: Path, *,
                      expected_sizes: dict[str, int] | None = None,
                      manifest=None, workers: int = 4,
                      progress=None) -> CacheOutcome:
    """Download every listing item that the manifest does not already cover.

    Manifest members are skipped entirely (D-07): steady state = zero
    downloads, zero cache residency — their md5 lives in the manifest and the
    frame already holds the bytes. Concurrency is a bounded pool (default 4,
    injectable down to 1); results are merged after shutdown, so no shared
    mutable state is touched during flight. `progress(google_media_id, ok)`
    is invoked per RESOLVED item (success or failure) after the merge, so a
    CLI can drive a progress bar (CSE-08) without thread races.
    """
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    expected_sizes = expected_sizes or {}
    outcome = CacheOutcome()

    todo: list[dict] = []
    for item in listing.items:
        if manifest is not None and manifest.entry_for(item["id"]) is not None:
            outcome.skipped_manifest += 1
        else:
            todo.append(item)

    if todo:
        with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            futures = {
                pool.submit(_download_one, session, item, cache_dir,
                            expected_sizes.get(item["id"])): item["id"]
                for item in todo
            }
            for fut, google_media_id in futures.items():
                try:
                    outcome.staged.append(fut.result())
                except Exception as exc:  # per-item isolation: one bad download
                    outcome.failed.append((google_media_id, str(exc)))
        # A crash or transport error mid-write could strand a partial file;
        # a failed id must leave NOTHING staged-looking behind (SAFE-04).
        staged_ids = {s["google_media_id"] for s in outcome.staged}
        for google_media_id, _err in outcome.failed:
            orphan = cache_dir / google_media_id
            if google_media_id not in staged_ids and orphan.exists():
                orphan.unlink()

    if progress is not None:
        for s in outcome.staged:
            progress(s["google_media_id"], True)
        for google_media_id, _err in outcome.failed:
            progress(google_media_id, False)

    return outcome


def prune_cache(cache_dir: Path, manifest_ids: set[str]) -> int:
    """Delete staged files whose google_media_id is manifest-backed (CSE-04).

    Targeted deletions BY NAME — never a directory walk (CSE-03 discipline
    applies to the whole module, pruning included). Files of un-manifested
    ids (failed downloads awaiting retry) are untouched. An already-missing
    file counts as pruned: pruning is idempotent and never raises.
    """
    cache_dir = Path(cache_dir)
    if not manifest_ids:
        return 0
    pruned = 0
    for google_media_id in manifest_ids:
        f = cache_dir / google_media_id
        if f.exists():
            f.unlink()
        pruned += 1
    return pruned


def videos_skipped(listing, metadata_item_count: int | None) -> int | None:
    """Named video count via the live-proven metadata delta (CSE-07 / D-10):
    album metadata item count minus the photo count the walker enumerates.
    Returns None when the metadata count is unknown (no delta to report)."""
    if metadata_item_count is None:
        return None
    return metadata_item_count - len(listing.items)

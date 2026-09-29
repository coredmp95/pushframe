"""Persistent google_media_id → md5_hash manifest (phase 18 plan 18-01).

This file is the sole memory that survives cache pruning: a manifest entry
MEANS "this photo lives on the frame" (its md5_hash was confirmed uploaded).
The mirror plan is rebuilt from the album listing plus THIS file — never from
a directory walk of the (pruned) cache (CSE-02/CSE-03).

Privacy posture: ids, hashes and sizes only — no cookie material, no
capability URLs. The cookie vault's structural denylist is therefore not
implicated by this module, but the vault's FILE discipline is mirrored:
0600 permissions and an atomic temp+rename write so a crash mid-save can
never leave a truncated manifest behind.

Entry shape is closed (fail loud on anything else):

    {"md5_hash": str, "size_bytes": int,
     "album_share_token": str, "first_synced": iso8601}
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_MANIFEST_PATH = Path("~/.config/pushframe/google-manifest.json")

# The ONLY keys a manifest entry may carry. Anything else fails loud on load —
# this file must never become a bag of unvetted per-id state (and can never
# accumulate secrets without tripping this guard).
_ENTRY_KEYS = frozenset(
    {"md5_hash", "size_bytes", "album_share_token", "first_synced"}
)


class ManifestError(RuntimeError):
    """Manifest absent-malformed, or an entry violating the closed shape."""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _validate_entry(google_media_id: str, entry: object) -> dict:
    if not isinstance(entry, dict):
        raise ManifestError(
            f"manifest entry for {google_media_id!r} is not an object — "
            f"refusing to guess"
        )
    keys = set(entry)
    if keys != set(_ENTRY_KEYS):
        unexpected = keys - _ENTRY_KEYS
        missing = _ENTRY_KEYS - keys
        raise ManifestError(
            f"manifest entry for {google_media_id!r} violates the closed "
            f"shape — unexpected keys: {sorted(unexpected)}, "
            f"missing: {sorted(missing)}"
        )
    if not isinstance(entry["md5_hash"], str) or not entry["md5_hash"]:
        raise ManifestError(f"entry {google_media_id!r}: md5_hash must be a non-empty string")
    if not isinstance(entry["size_bytes"], int) or isinstance(entry["size_bytes"], bool):
        raise ManifestError(f"entry {google_media_id!r}: size_bytes must be an int")
    return entry


@dataclass
class GoogleManifest:
    """The in-memory manifest map with load/save and entry access."""

    entries: dict[str, dict] = field(default_factory=dict)

    @classmethod
    def load(cls, path: str | Path | None = None) -> "GoogleManifest":
        """Read the manifest; an ABSENT file is a legitimate empty manifest
        (first run), while a present-but-malformed one fails loud."""
        p = Path(path).expanduser() if path else DEFAULT_MANIFEST_PATH.expanduser()
        if not p.exists():
            return cls()
        data = json.loads(p.read_text())
        if not isinstance(data, dict):
            raise ManifestError(f"manifest at {p} is not a JSON object — refusing to guess")
        return cls(entries={mid: _validate_entry(mid, e) for mid, e in data.items()})

    def entry_for(self, google_media_id: str) -> dict | None:
        return self.entries.get(google_media_id)

    def add(self, google_media_id: str, *, md5_hash: str, size_bytes: int,
            album_share_token: str, first_synced: str | None = None) -> None:
        self.entries[google_media_id] = {
            "md5_hash": md5_hash,
            "size_bytes": size_bytes,
            "album_share_token": album_share_token,
            "first_synced": first_synced or _now_iso(),
        }

    def save(self, path: str | Path | None = None) -> Path:
        """Persist atomically (temp file + os.replace) with 0600 permissions —
        a crash mid-save can never leave a truncated manifest behind."""
        p = Path(path).expanduser() if path else DEFAULT_MANIFEST_PATH.expanduser()
        p.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(self.entries, indent=2, sort_keys=True).encode("utf-8")
        tmp = p.with_name(p.name + ".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(fd, payload)
        finally:
            os.close(fd)
        os.replace(tmp, p)
        os.chmod(p, 0o600)  # in case the file pre-existed with looser mode
        return p

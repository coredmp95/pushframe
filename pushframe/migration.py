"""One-time user-config migration `~/.config/auraframes/` → `~/.config/pushframe/`
(phase 20, IDN-03).

The pushframe rename moved the binary, the module AND the config home. This
module moves the operator's existing state exactly once, on first run:

- Google cookie vault (0600) incl. the legacy probes/ location the vault's
  soft migration reads,
- Google manifest (0600),
- persisted write-budget token bucket,
- Chrome profile directory used by google-link's cookie harvest,
- legacy CLI logs.

Discipline (fail-safe, never destructive):
- Migration runs only when the NEW directory does not exist yet — a fresh
  install that already created ~/.config/pushframe/ is never touched, so the
  operation is idempotent by construction.
- The OLD directory is left in place untouched (belt-and-braces against a
  crash mid-copy: nothing is ever deleted).
- Files keep their modes (the 0600 vaults stay 0600 — shutil.copy2, never a
  move).
- `migration_notice()` returns the one-line notice the CLI prints; a silent
  no-op on fresh machines (nothing printed, nothing copied).
"""
from __future__ import annotations

import shutil
from pathlib import Path

OLD_CONFIG_DIR = Path("~/.config/auraframes").expanduser()
NEW_CONFIG_DIR = Path("~/.config/pushframe").expanduser()

# Everything an existing operator may have under the old home. Missing paths
# are skipped silently; unknown extra paths are copied too (shutil.copytree of
# the whole directory minus the excluded noise) so nothing is lost.
_EXCLUDED_NAMES = {"__pycache__"}

_MIGRATED_SUMMARY = {
    "google-cookies.json": "Google session vault",
    "google-manifest.json": "Google sync manifest",
    "probes": "probe vaults + Chrome profile",
    "chrome-profile": "google-link browser profile",
}


def should_migrate() -> bool:
    """True only when old state exists AND the new home was never created."""
    return OLD_CONFIG_DIR.is_dir() and not NEW_CONFIG_DIR.exists()


def migrate_config_dir() -> Path | None:
    """Copy the old config home to the new one. Returns the new dir or None.

    Idempotent: a second call always returns None (the new dir exists).
    Never deletes or modifies the old directory.
    """
    if not should_migrate():
        return None

    NEW_CONFIG_DIR.mkdir(parents=True, exist_ok=True)

    for entry in OLD_CONFIG_DIR.iterdir():
        if entry.name in _EXCLUDED_NAMES:
            continue
        target = NEW_CONFIG_DIR / entry.name
        if target.exists():  # never overwrite anything
            continue
        if entry.is_dir():
            shutil.copytree(entry, target, copy_function=shutil.copy2, dirs_exist_ok=True)
        else:
            shutil.copy2(entry, target)

    return NEW_CONFIG_DIR


def migration_notice() -> str | None:
    """Run the migration when due; return the one-line CLI notice, or None."""
    if not should_migrate():
        return None
    migrated = migrate_config_dir()
    if migrated is None:  # raced with another process — nothing to announce
        return None
    return (
        f"Config migrated: {OLD_CONFIG_DIR} -> {migrated} "
        f"(old directory left in place; nothing was deleted)"
    )

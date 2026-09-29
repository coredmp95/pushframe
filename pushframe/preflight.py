"""Phase 24 (PRF-01/02): named preflights — machine prerequisites checked
up front, each failure one screen with the remedy, never a traceback.

The google-link pattern (phase 23.5) generalized: google-sync requires the
Google cookie vault; sync/push require the source directory to EXIST (D-03:
hard error) while empty stays the friendly "nothing to do" (not an error —
looping scripts over sometimes-empty dirs must not break).
"""
from pathlib import Path


class PreflightError(Exception):
    """A named, one-screen prerequisite failure. `remedy` names the next
    action; str() renders both."""


def require_google_vault(vault_path: Path | None = None) -> Path:
    """google-sync's vault check (PRF-01): present + non-empty JSON object."""
    import json
    from pushframe.google.vault import DEFAULT_VAULT_PATH as _default
    path = vault_path or _default
    if not path.exists():
        raise PreflightError(
            f'no Google session vault at {path} — google-sync needs a linked '
            f'Google session. Remedy: run `pushframe google-link` (on a '
            f'headless server over ssh, use `ssh -X` so the Chrome login '
            f'window can open; the 0600 vault survives logouts).')
    try:
        data = json.loads(path.read_text())
        if not isinstance(data, dict) or not data:
            raise ValueError('empty or malformed vault')
    except (ValueError, OSError) as e:
        raise PreflightError(
            f'the Google vault at {path} is unreadable ({e}) — re-link: '
            f'`pushframe google-link`') from e
    return path


def require_source_dir(raw: str) -> Path:
    """sync/push source-dir check (D-03): nonexistent is a HARD named error
    BEFORE any scanning; existing-but-empty is NOT an error here (the
    commands' own "nothing to do" path handles it)."""
    path = Path(raw).expanduser()
    if not path.exists():
        raise PreflightError(
            f'source directory does not exist: {path} — nothing was scanned. '
            f'Check the path (typos and trailing-machine-name are the usual '
            f'culprits); pass the directory that contains your photos.')
    if not path.is_dir():
        raise PreflightError(
            f'source path is not a directory: {path} — pass a directory of '
            f'photos, not a file.')
    return path

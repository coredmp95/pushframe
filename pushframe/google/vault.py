"""Cookie vault for harvested Google sessions (migrated from
probes/cookie_vault.py, phase 17 plan 17-01 — D-03/D-06).

Harvested Google session cookies are wide-scope secrets. This module is the
ONLY reader of the vault, and the boundary is structural, not conventional:
`load()` inspects the caller's module name and refuses any sync/apply path
outright (D-06's denylist), the file lives outside the repo (refusing
repo-inside paths at save time), and permissions are 0600 via os.open.

Soft migration (17-CONTEXT discretion): the default path is now the
production location `~/.config/pushframe/google-cookies.json`; when it is
absent, `load()` falls back to the legacy probe vault
`~/.config/pushframe/probes/google-cookies.json` so an operator's existing
session keeps working. The boundary travels with the module: the denylist,
the 0600 mode and the repo-inside refusal are carried over verbatim.

NOTE: the denylist includes `pushframe.cli` — the CLI must reach the vault
only through `pushframe.google` (GoogleSession.from_vault), never by
importing this module directly. That routing is the boundary's enforcement
point, not a formality.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

DEFAULT_VAULT_PATH = Path("~/.config/pushframe/google-cookies.json")
LEGACY_VAULT_PATH = Path("~/.config/pushframe/probes/google-cookies.json")

# D-06: sync/apply code paths can never hold session cookies. Enforced at
# load() via frame inspection — an import from these namespaces raises
# before any file is read.
_DENYLIST_PREFIXES = ("pushframe.sync", "pushframe.reconcile", "pushframe.cli")

_REPO_ROOT = Path(__file__).resolve().parents[2]


class CookieVaultError(RuntimeError):
    """Vault absent, mis-located, or reached from a denied code path."""


def _deny_check(caller_module_name: str) -> None:
    for prefix in _DENYLIST_PREFIXES:
        if caller_module_name.startswith(prefix):
            raise CookieVaultError(
                f"cookie vault refused: module '{caller_module_name}' matches the "
                f"sync/apply denylist prefix '{prefix}' — sync paths can never read "
                f"harvested session cookies (D-06)"
            )


def _caller_module_name() -> str:
    frame = sys._getframe(2)  # load() <- caller
    return frame.f_globals.get("__name__", "") if frame else ""


def _resolve(path: str | Path | None) -> Path:
    return Path(path).expanduser().resolve() if path else DEFAULT_VAULT_PATH.expanduser().resolve()


def _vault_candidates(path: str | Path | None) -> list[Path]:
    """Vault paths to try, in order: explicit > production > legacy probe vault.

    The soft migration (17-CONTEXT): an operator whose session still lives in
    the legacy probe vault keeps working until the next `google-link` writes
    the production vault.
    """
    if path is not None:
        return [_resolve(path)]
    prod = DEFAULT_VAULT_PATH.expanduser().resolve()
    if prod.exists():
        return [prod]
    legacy = LEGACY_VAULT_PATH.expanduser().resolve()
    if legacy.exists():
        return [legacy]
    return [prod]


def save(cookies: list[dict], *, path: str | Path | None = None) -> Path:
    """Persist cookie records atomically with 0600 permissions.

    Refuses to write when the resolved path is inside the git repo — the vault
    lives outside it by construction (T-16-05). Defaults to the production
    vault path; `google-link` re-links always write there.
    """
    vault = _resolve(path)
    if vault.is_relative_to(_REPO_ROOT):
        raise CookieVaultError(
            f"cookie vault refused: resolved path {vault} is inside the git repo — "
            f"harvested cookies are never tracked (D-06)"
        )
    vault.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(cookies, indent=2).encode("utf-8")
    fd = os.open(vault, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, payload)
    finally:
        os.close(fd)
    os.chmod(vault, 0o600)  # in case the file pre-existed with looser mode
    return vault


def load(*, path: str | Path | None = None) -> list[dict]:
    """Read the vault, refusing sync/apply-named callers structurally."""
    _deny_check(_caller_module_name())
    vault = _resolve(_vault_candidates(path)[0])
    if not vault.exists():
        raise CookieVaultError(
            f"no session — run the bootstrap first (vault not found at {vault})"
        )
    records = json.loads(vault.read_text())
    if not isinstance(records, list) or not records:
        raise CookieVaultError(f"vault at {vault} is empty or malformed")
    return records


def cookies_for_httpx(*, path: str | Path | None = None) -> dict[str, str]:
    """Return an httpx.Cookies-shaped name→value dict from the vault.

    FLATTENED — diagnostics only. The session client must build its jar from
    the full `load()` records instead: photos.google.com treats a flattened
    dict as an anonymous visitor (live-proven phase 16).
    """
    return {c["name"]: c["value"] for c in load(path=path) if "name" in c and "value" in c}

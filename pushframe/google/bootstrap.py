"""Interactive Google bootstrap: dedicated-profile browser harvest.

Migrated from probes/browser_bootstrap.py's `_bootstrap` (phase 16,
live-proven). The CLI's `google-link` drives it through a `bootstrap_fn`
seam so offline tests inject a fake and never launch a browser (TEST-02).

Posture carried over (T-16-06): the DAILY-DRIVER profile is structurally
unreachable — the dedicated profile directory must be named by
`AURA_PROBE_CHROME_PROFILE` before any browser exists; unset/empty fails
loud with the exact remedy. Playwright is imported lazily INSIDE
`run_bootstrap` only — importing this module never requires playwright
(BROWSER-AUTOMATION §4.2 isolation rule).

Output discipline (T-16-07): identity signals only — cookie NAMES and
counts, never values; the vault path is printed, never its contents.
"""
from __future__ import annotations

import os
import shutil
import time
from pathlib import Path

from pushframe.google import vault
from pushframe.google.client import PHOTOS_HOME

PROFILE_ENV_VAR = "PUSHFRAME_PROBE_CHROME_PROFILE"
# IDN-04: the AURA_* spelling stays readable for one release of grace.
_LEGACY_PROFILE_ENV_VAR = "AURA_PROBE_CHROME_PROFILE"

_AUTO_WAIT_SECONDS = 30 * 60  # the operator may log in much later

# Identity-signal cookie names (names only, never values).
_AUTH_MARKER_NAMES = ("SID", "SAPISID", "__Secure-1PSID", "__Secure-3PSID",
                      "LSID", "__Secure-1PAPISID", "__Secure-3PAPISID")

# Login-completion markers for auto-detect.
_AUTH_COMPLETION_MARKERS = {"SAPISID", "__Secure-1PAPISID", "__Secure-3PAPISID"}


class BootstrapError(RuntimeError):
    """Bootstrap prerequisites missing or the login never completed."""


DEFAULT_PROFILE_DIR = Path.home() / ".config" / "pushframe" / "chrome-profile"


def _require_profile() -> Path:
    """Resolve the dedicated profile directory (v5.1 simplification).

    Precedence: PUSHFRAME_PROBE_CHROME_PROFILE > AURA_PROBE_CHROME_PROFILE
    (legacy) > the built-in default ~/.config/pushframe/chrome-profile —
    created on demand. The env var is now an OVERRIDE, not a prerequisite:
    a fresh machine gets google-link with zero configuration.

    A near-miss env name (e.g. USHFRAME_PROBE_CHROME_PROFILE — a truncated
    prefix, seen in the wild on venus) is called out explicitly, because a
    silent typo looks identical to "unset".
    """
    profile = os.environ.get(PROFILE_ENV_VAR, "").strip()
    if not profile:
        profile = os.environ.get(_LEGACY_PROFILE_ENV_VAR, "").strip()
    if not profile:
        # Near-miss detection: subsequence match (USHFRAME_… = PUSHFRAME_…
        # minus its first letter — a real-world typo, seen on venus) catches
        # truncated/scrambled names that a plain substring check misses.
        target = PROFILE_ENV_VAR.lower()
        for name, value in os.environ.items():
            low = name.lower()
            if name == PROFILE_ENV_VAR or not low.endswith(target[4:]):
                continue
            it = iter(target)
            if all(ch in it for ch in low):  # low is a subsequence of target
                raise BootstrapError(
                    f"env var '{name}' looks like a misspelled "
                    f"{PROFILE_ENV_VAR} (value: {value}) — fix the name in "
                    f"your shell profile, or unset it to use the default "
                    f"profile at {DEFAULT_PROFILE_DIR}"
                )
        DEFAULT_PROFILE_DIR.mkdir(parents=True, exist_ok=True)
        return DEFAULT_PROFILE_DIR
    path = Path(profile).expanduser().resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path


def run_bootstrap(*, auto: bool = False) -> dict:
    """Open the dedicated-profile browser, harvest cookies, save the vault.

    auto=True polls the context for completed Google auth (SAPISID-family
    cookies) instead of waiting for an Enter in the terminal — for runs
    launched from a non-interactive shell. Returns an identity summary dict:
    {vault_path, cookie_count, auth_markers} — never cookie values.
    """
    profile_dir = _require_profile()

    # Preflight (v5.1): fail with the REMEDY, not a traceback. Order matters —
    # the python package first, then a browser able to drive.
    missing = []
    try:
        import playwright  # noqa: F401
    except ImportError:
        missing.append(
            "the 'playwright' python package (this .deb/wheel ships without it;"
            " install with: pip install --user playwright  — or "
            "pip install 'pushframe[google-browser]')"
        )
    has_chrome = shutil.which("google-chrome") is not None
    has_chromium = shutil.which("chromium") is not None or shutil.which("chromium-browser") is not None
    if not has_chrome and not has_chromium:
        missing.append(
            "a browser for Playwright to drive — install Google Chrome "
            "(recommended; channel=chrome avoids bot flags), or run "
            "'playwright install chromium' after installing the package"
        )
    if missing:
        remedy = "\n  - ".join(missing)
        raise BootstrapError(
            "google-link prerequisites missing on this machine:\n"
            f"  - {remedy}\n"
            "Then re-run: pushframe google-link"
        )

    from playwright.sync_api import sync_playwright  # lazy, in-function import ONLY

    print(f"dedicated profile: {profile_dir}")
    print("opening browser — log into Google in the window.")
    # Anti-bot-detection posture (BROWSER-AUTOMATION §2): channel="chrome"
    # uses the REAL installed Google Chrome when present (Google's risk
    # engine already treats it as an ordinary browser); otherwise bundled
    # Chromium with automation switches masked so navigator.webdriver stays
    # false.
    launch_kwargs = dict(user_data_dir=str(profile_dir), headless=False,
                         args=["--disable-blink-features=AutomationControlled"])
    if has_chrome:
        launch_kwargs["channel"] = "chrome"
        print("browser: system Google Chrome (channel=chrome)")
    else:
        launch_kwargs["ignore_default_args"] = ["--enable-automation"]
        print("browser: bundled Chromium (automation switches masked)")
    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(**launch_kwargs)
        page = context.pages[0] if context.pages else context.new_page()
        page.goto(PHOTOS_HOME, wait_until="domcontentloaded")
        if auto:
            print(f"waiting for login to complete (auto-detect, "
                  f"{_AUTO_WAIT_SECONDS // 60} min timeout)…", flush=True)
            deadline = time.monotonic() + _AUTO_WAIT_SECONDS
            auth_names: set[str] = set()
            while time.monotonic() < deadline:
                cookies = context.cookies()
                auth_names = {c["name"] for c in cookies
                              if c["name"] in _AUTH_COMPLETION_MARKERS}
                if auth_names:
                    break
                time.sleep(2)
            if not auth_names:
                context.close()
                raise BootstrapError(
                    "no auth cookies detected within the wait window — was the "
                    "login completed in the opened window?"
                )
            print(f"login detected via {sorted(auth_names)} — harvesting", flush=True)
            cookies = context.cookies()
            context.close()
        else:
            input("…press Enter here AFTER logging in on the opened window> ")
            cookies = context.cookies()
            context.close()

    if not any(c["name"] in ("SID", "SAPISID", "__Secure-1PSID") for c in cookies):
        raise BootstrapError(
            "no Google session cookies found after login — was the login "
            "completed in the opened window?"
        )

    vault_path = vault.save(cookies)
    markers = sorted({c["name"] for c in cookies if c["name"] in _AUTH_MARKER_NAMES})
    return {"vault_path": vault_path, "cookie_count": len(cookies),
            "auth_markers": markers}


def default_bootstrap(*, auto: bool = True) -> dict:
    """The CLI's default `bootstrap_fn`: interactive harvest with auto-detect
    (the non-interactive-friendly posture the phase-16 probe settled on)."""
    return run_bootstrap(auto=auto)

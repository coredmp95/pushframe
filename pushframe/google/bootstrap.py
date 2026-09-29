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


def _require_profile() -> Path:
    """Resolve the dedicated profile directory; fail loud when unset (T-16-06)."""
    profile = os.environ.get(PROFILE_ENV_VAR, "").strip()
    if not profile:
        profile = os.environ.get(_LEGACY_PROFILE_ENV_VAR, "").strip()
    if not profile:
        raise BootstrapError(
            f"{PROFILE_ENV_VAR} is unset — the daily-driver profile is "
            f"structurally unreachable; point the env var at a dedicated "
            f"profile directory (e.g. ~/.config/pushframe/chrome-profile) "
            f"and re-run (T-16-06)"
        )
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
    from playwright.sync_api import sync_playwright  # lazy, in-function import ONLY

    print(f"dedicated profile: {profile_dir}")
    print("opening browser — log into Google in the window.")
    # Anti-bot-detection posture (BROWSER-AUTOMATION §2): channel="chrome"
    # uses the REAL installed Google Chrome when present (Google's risk
    # engine already treats it as an ordinary browser); otherwise bundled
    # Chromium with automation switches masked so navigator.webdriver stays
    # false.
    has_chrome = shutil.which("google-chrome") is not None
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

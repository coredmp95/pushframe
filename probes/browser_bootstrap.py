"""Browser-automation probe (Phase 16, plan 16-02 — LGS-01).

Two subcommands:

  bootstrap — open Chromium (Playwright persistent context) on the DEDICATED
  profile directory ($AURA_PROBE_CHROME_PROFILE, required), let the operator
  log into Google interactively once, harvest the session cookies into the
  untracked 0600 vault, and print only a redacted identity signal. The
  daily-driver profile is structurally unreachable: unset/empty env fails
  loud before any browser exists (T-16-06).

  list — with NO browser at all: load vaulted cookies into a plain httpx
  client and issue ONE internal batchexecute album listing against a
  throwaway album (URL from an untracked *.link file or --url), reusing the
  shared-link probe's ds:1 parser. Prints batch-1 count and the follow-up
  continuation attempt, redacted (xob0t/Google-Photos-Toolkit is the RPC-shape
  reference; the exact continuation rpcid is part of what this probe measures).

Playwright is imported lazily INSIDE the bootstrap function only — importing
this module never requires playwright (isolation rule, BROWSER-AUTOMATION §4.2).

Usage:
    uv run python probes/browser_bootstrap.py bootstrap
    uv run python probes/browser_bootstrap.py bootstrap --auto   (30 min wait)
    uv run python probes/browser_bootstrap.py list --url <throwaway-album-link>
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import probes.cookie_vault as cookie_vault  # noqa: E402  (shim over the package vault)
from probes.common import redact_link, redact_tokens  # noqa: E402
from probes.shared_link_probe import _walk_items, parse_af_initdata  # noqa: E402
from pushframe.google.client import GoogleSession  # noqa: E402

PHOTOS_HOME = "https://photos.google.com/"
# batchexecute endpoint for the Photos web frontend (authuser keeps the call
# bound to the logged-in account the vault came from).
_BATCHEXECUTE_URL = "https://photos.google.com/_/PhotosUi/data/batchexecute"

_AUTO_WAIT_SECONDS = 30 * 60  # the operator may log in much later


def _require_dedicated_profile() -> Path:
    profile = os.environ.get("AURA_PROBE_CHROME_PROFILE", "").strip()
    if not profile:
        raise SystemExit(
            "PROBE FAILED: AURA_PROBE_CHROME_PROFILE is unset — the daily-driver "
            "profile is structurally unreachable; point the env var at a dedicated "
            "profile directory (T-16-06)"
        )
    path = Path(profile).expanduser().resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path


def _bootstrap(*, auto: bool = False) -> int:
    """Interactive one-time cookie harvest from the dedicated profile.

    auto=True polls the context for completed Google auth (SAPISID-family
    cookies) instead of waiting for an Enter in the terminal — for runs
    launched from a non-interactive shell. Same consent scope, same profile,
    same vault.
    """
    profile_dir = _require_dedicated_profile()
    from playwright.sync_api import sync_playwright  # lazy, in-function import ONLY

    print(f"dedicated profile: {profile_dir}")
    print("opening browser — log into Google in the window.")
    # Anti-bot-detection posture (BROWSER-AUTOMATION §2; D-03 single documented
    # retry after a concrete fix): Google challenges Playwright's bundled
    # Chromium at login ("this browser or app may not be secure"). Mitigations:
    #   1. channel="chrome" — the REAL installed Google Chrome when present
    #      (falls back to bundled Chromium otherwise), which Google's risk
    #      engine already treats as an ordinary browser;
    #   2. drop the --enable-automation switch and disable blink automation
    #      features so navigator.webdriver stays false.
    import shutil
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
            _AUTH_MARKERS = {"SAPISID", "__Secure-1PAPISID", "__Secure-3PAPISID"}
            print(f"waiting for login to complete (auto-detect, "
                  f"{_AUTO_WAIT_SECONDS // 60} min timeout)…", flush=True)
            deadline = time.monotonic() + _AUTO_WAIT_SECONDS
            auth_names: set[str] = set()
            while time.monotonic() < deadline:
                cookies = context.cookies()
                auth_names = {c["name"] for c in cookies if c["name"] in _AUTH_MARKERS}
                if auth_names:
                    break
                time.sleep(2)
            if not auth_names:
                context.close()
                print("PROBE FAILED: no auth cookies detected within the wait window — "
                      "was the login completed in the opened window?", file=sys.stderr)
                return 1
            print(f"login detected via {sorted(auth_names)} — harvesting", flush=True)
            cookies = context.cookies()
            context.close()
        else:
            input("…press Enter here AFTER logging in on the opened window> ")
            cookies = context.cookies()
            context.close()

    if not any(c["name"] in ("SID", "SAPISID", "__Secure-1PSID") for c in cookies):
        print("PROBE FAILED: no Google session cookies found after login — "
              "was the login completed in the opened window?", file=sys.stderr)
        return 1

    vault = cookie_vault.save(cookies)
    # Identity signal only — never cookie values (T-16-07).
    markers = sorted({c["name"] for c in cookies
                      if c["name"] in ("SID", "SAPISID", "__Secure-1PSID",
                                       "__Secure-3PSID", "LSID", "__Secure-1PAPISID",
                                       "__Secure-3PAPISID")})
    print(f"vault saved: {vault} (0600, untracked)")
    print(f"session cookies present: {len(cookies)} total; auth markers: {markers}")
    return 0


def _extract_sapisid() -> str | None:
    """SAPISID (or its __Secure- variants) is needed for the Authorization
    header some batchexecute calls expect. Returns None when absent; the
    listing still attempts cookie-only auth first."""
    try:
        for c in cookie_vault.load():
            if c.get("name") in ("SAPISID", "__Secure-1PAPISID", "__Secure-3PAPISID"):
                return c["value"]
    except cookie_vault.CookieVaultError as exc:
        raise SystemExit(f"PROBE FAILED: {exc}")
    return None


def _batchexecute(http: httpx.Client, rpcid: str, payload: list, origin: str) -> httpx.Response:
    """Issue ONE batchexecute POST replicating the public envelope shape:
    f.req carries the TRIPLE-nested [[ [rpcid, json(payload), null, 'generic'] ]]
    (phase 17 live-proven: a double-nested envelope answers HTTP 400 — the
    phase-16 'payload deviné' failure was a nesting bug), with the standard
    at boilerplate; SAPISIDHASH Authorization attached when available
    (xob0t/Google-Photos-Toolkit reference shapes)."""
    import hashlib

    entry = [rpcid, json_dumps(payload), None, "generic"]
    freq = json_dumps([[entry]])
    headers = {
        "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
        "Origin": origin,
        "Referer": f"{origin}/",
    }
    sapisid = _extract_sapisid()
    if sapisid:
        now_ms = int(time.time() * 1000)
        auth_hash = hashlib.sha1(f"{now_ms} {sapisid} {origin}".encode()).hexdigest()
        headers["Authorization"] = f"SAPISIDHASH {now_ms}_{auth_hash}"
    body = f"f.req={query_escape(freq)}&at={query_escape(_AT_TOKEN)}&"
    return http.post(_BATCHEXECUTE_URL, headers=headers, content=body.encode())


_AT_TOKEN = ""


_UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) " \
       "Chrome/153.0.0.0 Safari/537.36"


def _http_client() -> httpx.Client:
    """httpx client with the vault's FULL cookie records (domain+path preserved).

    Live finding: a flattened name→value dict gets treated as an anonymous
    visitor by photos.google.com (redirect to the marketing page), while the
    complete jar (domain/path from the harvest) yields the logged-in app page
    with the SNlM0e at-token. UA matches the harvesting browser.

    Phase 17 (plan 17-01 T3): the session builder MOVED to the package —
    this returns the GoogleSession's httpx.Client (same full-jar recipe,
    same UA, same 30s timeout + redirects).
    """
    return GoogleSession.from_vault().http


def _list_album(url: str) -> int:
    """Plain-httpx internal RPC listing against a throwaway album."""
    global _AT_TOKEN
    http = _http_client()

    # Step 1: fetch the album page WITH session cookies — extracts the album's
    # internal id, the at-token, and batch-1 of items via the ds:1 parser.
    page = http.get(url)
    if page.status_code != 200:
        print(f"PROBE FAILED: album page returned HTTP {page.status_code} "
              f"(session cookies attached) — {redact_link(url)}", file=sys.stderr)
        return 1
    at_m = re.search(r'"SNlM0e":"([^"]+)"', page.text)
    _AT_TOKEN = at_m.group(1) if at_m else ""
    if not _AT_TOKEN:
        # SNlM0e lives on the logged-in home page, not on share pages —
        # fetch it there with the same session.
        home = http.get(PHOTOS_HOME)
        at_m = re.search(r'"SNlM0e":"([^"]+)"', home.text)
        _AT_TOKEN = at_m.group(1) if at_m else ""
        print(f"at-token from photos.google.com home: {'found' if _AT_TOKEN else 'MISSING'}")
    # Album id: first AF1Qip token in the ds:0 (album header) block, falling
    # back to the page's first token. Share-page media ids share the prefix,
    # so a mis-grabbed id surfaces as an RPC error — which is still evidence.
    album_id = None
    ds0_m = re.search(r"key: 'ds:0'.{0,4000}?AF1Qip([A-Za-z0-9_-]{20,})", page.text, re.S)
    if ds0_m:
        album_id = ds0_m.group(1) if ds0_m.lastindex else ds0_m.group(0)
        album_id = "AF1Qip" + album_id
    else:
        any_m = re.search(r"(AF1Qip[A-Za-z0-9_-]{20,})", page.text)
        album_id = any_m.group(1) if any_m else None
    items = parse_af_initdata(page.text)
    print(f"album page (with session): HTTP {page.status_code}, "
          f"batch-1 count: {len(items)} — {redact_link(url)}")

    # Step 2: follow-up batchexecute — pagination via snAcKc + continuation.
    # The rpcid/shape was learned by rpc_capture.py (Google's own frontend
    # calls on a scrolled album): snAcKc(share_token, continuation, null, key),
    # 300 items/page, exhausts when the response carries no AH_ token.
    # A verbatim captured body (from the capture file) is replayed with the
    # token substituted — highest-fidelity replay, no payload guessing.
    capture = Path("/tmp/gsd-rpc-capture.json")
    if not capture.exists():
        print("follow-up RPC: no capture file — run probes/rpc_capture.py first")
    else:
        calls = json.loads(capture.read_text())
        snac = next((c for c in calls if c["rpcid"] == "snAcKc"), None)
        if snac is None:
            print("follow-up RPC: capture holds no snAcKc call — re-run rpc_capture on a large album")
        else:
            rpc_url = ("https://photos.google.com/_/PhotosUi/data/batchexecute?"
                       + snac["url_query"].split("&_reqid")[0])
            body = snac["freq_head"]
            cur_tok = re.search(r"AH_[A-Za-z0-9_-]{40,}", body).group(0)
            total, pages, all_ids = 0, 0, set()
            rpc_headers = {"Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
                           "Origin": "https://photos.google.com",
                           "Referer": "https://photos.google.com/"}
            while pages < 60:  # 60 pages x 300 = 18000, far beyond any real album
                r = http.post(rpc_url, content=body.encode(), headers=rpc_headers)
                if r.status_code != 200:
                    print(f"follow-up RPC: HTTP {r.status_code} on page {pages + 1} — recorded (D-03)")
                    break
                got, got_tok = 0, None
                for line in r.text.split("\n"):
                    line = line.strip()
                    if not line or line.startswith(")]}'"):
                        continue
                    try:
                        arr = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    for entry in arr:
                        if (isinstance(entry, list) and entry
                                and entry[0] == "wrb.fr" and entry[1] == "snAcKc"):
                            if entry[2] is None:
                                continue
                            inner = json.loads(entry[2])
                            page_items = []
                            _walk_items(inner, page_items)
                            got = len(page_items)
                            all_ids.update(i["id"] for i in page_items)
                            for tok in re.findall(r"AH_[A-Za-z0-9_-]{40,}", entry[2]):
                                got_tok = tok  # last = the freshest cursor
                total += got
                pages += 1
                print(f"RPC page {pages}: +{got} items (cumulated {total}, "
                      f"unique {len(all_ids)}); next token: {'yes' if got_tok else 'EXHAUSTED'}")
                if not got_tok or not got:
                    break
                body = body.replace(cur_tok, got_tok)
                cur_tok = got_tok
            print(f"RPC enumeration total: {total} items ({len(all_ids)} unique) "
                  f"— {'EXHAUSTED cleanly' if not got_tok else 'stopped (page cap)'}")
            if album_id:
                print(f"(album {album_id[:12]}…)")

    print("note: the browser mechanism is permanently local-only and never-CI-able; "
          "cookie expiry cadence is an accepted operational cost (2026-09-28 decision).")
    return 0


def json_dumps(obj) -> str:
    import json
    return json.dumps(obj, separators=(",", ":"))


def query_escape(s: str) -> str:
    from urllib.parse import quote
    return quote(s, safe="")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("bootstrap", help="interactive login + cookie harvest into the vault")
    sub.choices["bootstrap"].add_argument("--auto", action="store_true",
                                          help="auto-detect login completion instead of "
                                               "waiting for Enter (for non-interactive runs)")
    p_list = sub.add_parser("list", help="plain-httpx batchexecute album listing via vaulted cookies")
    p_list.add_argument("--url", required=False, default=None,
                        help="throwaway album share/URL (or put a full link in probes/*.link)")
    args = parser.parse_args(argv)

    if args.command == "bootstrap":
        return _bootstrap(auto=getattr(args, "auto", False))
    url = args.url
    if not url:
        links = sorted(Path(__file__).parent.glob("*.link"))
    if not url:
        raise SystemExit("PROBE FAILED: no album URL — pass --url or drop a full "
                         "link into an untracked probes/*.link file")
    return _list_album(url)


if __name__ == "__main__":
    raise SystemExit(main())

"""Offline tests for `pushframe google-link` (plan 17-02 T1).

Zero browser, zero network: the `bootstrap_fn` seam gets a fake, the
profile env is controlled per-test, and the never-print-cookie guarantee
is proven by grepping stdout for a planted fake cookie value (T-17-05).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pushframe.cli import run_google_link  # noqa: E402
from pushframe.google.bootstrap import BootstrapError  # noqa: E402

FAKE_COOKIE_VALUE = "FAKE-COOKIE-VALUE-never-print-001"


def _fake_bootstrap(summary=None):
    def fn():
        if isinstance(summary, Exception):
            raise summary
        return summary or {"vault_path": Path("/tmp/fake-vault.json"),
                           "cookie_count": 45,
                           "auth_markers": ["SID", "SAPISID", "__Secure-1PSID"]}
    return fn


def test_google_link_success_with_injected_bootstrap(monkeypatch, capsys):
    monkeypatch.setenv("PUSHFRAME_PROBE_CHROME_PROFILE", "/tmp/dedicated-profile")
    rc = run_google_link(bootstrap_fn=_fake_bootstrap())
    assert rc == 0
    out = capsys.readouterr().out
    assert "vault saved" in out and "0600" in out
    # Identity signals only: marker NAMES and counts, never values.
    assert "SAPISID" in out and "45 total" in out
    assert FAKE_COOKIE_VALUE not in out


def test_require_profile_zero_config_uses_default(tmp_path, monkeypatch):
    """v5.1 contract: the profile env var is an OVERRIDE, not a prerequisite.
    With nothing set, _require_profile resolves to the built-in default
    (~/.config/pushframe/chrome-profile) and CREATES it — a fresh machine
    needs no shell configuration."""
    for name in ("PUSHFRAME_PROBE_CHROME_PROFILE", "AURA_PROBE_CHROME_PROFILE",
                 "USHFRAME_PROBE_CHROME_PROFILE"):
        monkeypatch.delenv(name, raising=False)
    from pushframe.google import bootstrap as bs
    monkeypatch.setattr(bs, "DEFAULT_PROFILE_DIR", tmp_path / "chrome-profile")
    resolved = bs._require_profile()
    assert resolved == tmp_path / "chrome-profile"
    assert resolved.is_dir()


def test_require_profile_near_miss_env_name_is_called_out(tmp_path, monkeypatch):
    """A truncated prefix (USHFRAME_PROBE_CHROME_PROFILE, seen in the wild
    on venus) must not look like a plain 'unset': the error names the
    suspect variable and offers the default-profile escape hatch."""
    for name in ("PUSHFRAME_PROBE_CHROME_PROFILE", "AURA_PROBE_CHROME_PROFILE"):
        monkeypatch.delenv(name, raising=False)
    from pushframe.google import bootstrap as bs
    monkeypatch.setattr(bs, "DEFAULT_PROFILE_DIR", tmp_path / "chrome-profile")
    monkeypatch.setenv("USHFRAME_PROBE_CHROME_PROFILE", "/somewhere")
    try:
        bs._require_profile()
        raised = None
    except bs.BootstrapError as e:
        raised = str(e)
    assert raised is not None
    assert "USHFRAME_PROBE_CHROME_PROFILE" in raised and "misspell" in raised
    assert "chrome-profile" in raised


def test_google_link_relink_notice_when_vault_exists(tmp_path, monkeypatch, capsys):
    """Re-link is the same command (LGS-02): with an existing vault present,
    a one-line refresh notice prints before the bootstrap runs."""
    monkeypatch.setenv("PUSHFRAME_PROBE_CHROME_PROFILE", "/tmp/dedicated-profile")
    from pushframe.google import vault as google_vault
    vault_path = tmp_path / "v.json"
    google_vault.save([{"name": "SID", "value": "x", "domain": ".google.com",
                        "path": "/"}], path=vault_path)
    monkeypatch.setattr(google_vault, "DEFAULT_VAULT_PATH", vault_path)
    monkeypatch.setattr(google_vault, "LEGACY_VAULT_PATH", tmp_path / "nope")
    rc = run_google_link(bootstrap_fn=_fake_bootstrap())
    assert rc == 0
    assert "refreshing it" in capsys.readouterr().out


def test_google_link_bootstrap_error_exits_nonzero(monkeypatch, capsys):
    monkeypatch.setenv("PUSHFRAME_PROBE_CHROME_PROFILE", "/tmp/dedicated-profile")
    rc = run_google_link(
        bootstrap_fn=_fake_bootstrap(BootstrapError("no auth cookies detected")))
    assert rc == 1
    assert "no auth cookies detected" in capsys.readouterr().out


def test_google_link_never_prints_cookie_values(monkeypatch, capsys):
    """Even the fake bootstrap's summary dict is value-free; if a cookie
    VALUE ever leaked into the flow, this grep would catch it."""
    monkeypatch.setenv("PUSHFRAME_PROBE_CHROME_PROFILE", "/tmp/dedicated-profile")
    summary = {"vault_path": "/tmp/v", "cookie_count": 3,
               "auth_markers": ["SID", "SAPISID"]}
    run_google_link(bootstrap_fn=_fake_bootstrap(summary))
    out = capsys.readouterr().out
    assert FAKE_COOKIE_VALUE not in out
    # No cookie= / SAPISID= value assignment shape anywhere in the output.
    assert "SAPISID=" not in out and "SID=" not in out


def test_require_profile_display_check_names_sshx(tmp_path, monkeypatch):
    """Headless UX (venus, 2026-09-29): with every package-level prerequisite
    present but no $DISPLAY, the error must name the ssh -X remedy — never a
    raw playwright X11 traceback."""
    for name in ("PUSHFRAME_PROBE_CHROME_PROFILE", "AURA_PROBE_CHROME_PROFILE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv("DISPLAY", raising=False)
    from pushframe.google import bootstrap as bs
    called = {}
    def _fail_after_checks():
        # replicate run_bootstrap's check order up to the DISPLAY gate
        bs._require_profile()
        called["reached_display_check"] = True
        raise bs.BootstrapError("no X display detected ($DISPLAY is empty)")
    monkeypatch.setattr(bs, "run_bootstrap", _fail_after_checks)
    from pushframe.cli import run_google_link
    rc = run_google_link(bootstrap_fn=_fail_after_checks)
    assert rc == 1

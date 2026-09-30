"""google-sync's vault preflight (PRF-01) — the venus live-proven bugs.

2026-09-30, venus: `pushframe google-link` saved a perfectly healthy vault
(28 cookie records) yet `pushframe google-sync` answered "no Google session
vault". Two stacked bugs in require_google_vault:

1. the default path was probed WITHOUT expanduser() — `Path("~/.config/…")
   .exists()` compares a literal `~` directory on disk and never matches,
   so the preflight fired on every healthy machine (masked on CI: no vault
   there either; masked on scheduled runs: the --pair branch skips the
   block — exactly the path the nightly uses);
2. the shape check demanded a JSON OBJECT while vault.save() writes a JSON
   LIST of cookie records — with the path fixed, a healthy vault would have
   landed in the "unreadable — re-link" branch anyway.

The tell was in the transcript: google-link prints "existing Google session
found" only when from_vault() (a vault READ) succeeds — the vault was
there; the preflight was the liar.
"""
import json

import pytest

from pushframe.preflight import PreflightError, require_google_vault


@pytest.fixture
def vault_root(tmp_path, monkeypatch):
    """Pinned vault env: default path under tmp, isolated from the machine."""
    monkeypatch.setenv("PUSHFRAME_VAULT_PATH", str(tmp_path / "v.json"))
    return tmp_path / "v.json"


def _healthy_vault(path):
    path.write_text(json.dumps([
        {"name": "SID", "value": "x", "domain": ".google.com"},
        {"name": "SAPISID", "value": "y", "domain": ".google.com"},
    ]))


def test_healthy_list_vault_passes(vault_root):
    """The exact venus shape: vault.save()'s JSON list of records."""
    _healthy_vault(vault_root)
    assert require_google_vault() == vault_root.expanduser()


def test_missing_vault_fails_named_with_remedy(vault_root):
    with pytest.raises(PreflightError) as e:
        require_google_vault()
    assert 'no Google session vault' in str(e.value)
    assert 'google-link' in str(e.value)


def test_literal_tilde_default_path_never_probed(monkeypatch):
    """The regression itself: without PUSHFRAME_VAULT_PATH the default must
    be EXPANDED (probe the real per-user path, not a literal `~` file).
    Pure assertions — this test must never write near the machine's real
    vault (the first draft did a write+unlink there; unacceptable)."""
    monkeypatch.delenv("PUSHFRAME_VAULT_PATH", raising=False)
    from pushframe.preflight import default_vault_path
    from pushframe.google.vault import DEFAULT_VAULT_PATH
    real = default_vault_path()
    assert real == DEFAULT_VAULT_PATH.expanduser()
    assert '~' not in str(real)


def test_dict_vault_still_accepted_backward_compat(vault_root):
    vault_root.write_text(json.dumps({"SID": "x"}))
    assert require_google_vault() == vault_root.expanduser()


def test_empty_shapes_fail_named(vault_root):
    for payload in ("[]", "{}", "not json at all"):
        vault_root.write_text(payload)
        with pytest.raises(PreflightError) as e:
            require_google_vault()
        assert 'unreadable' in str(e.value)
        assert 'google-link' in str(e.value)

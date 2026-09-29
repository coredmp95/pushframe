"""Phase 23 (CFG-02): dynamic settings resolution.

Precedence is locked: **config file → env var → built-in default**. The
config file lives at ~/.config/pushframe/config.json (0600, managed by
pushframe.config_store). Importers keep reading `settings.X`
attribute-style — resolution happens at ACCESS time (PEP 562), so
`config set` changes what the next command sees without touching any
importer.
"""
import json

import pytest

from pushframe.utils import settings


@pytest.fixture
def config_file(tmp_path, monkeypatch):
    """A config.json in a fake home + CONFIG_PATH pointed at it."""
    path = tmp_path / "config.json"
    monkeypatch.setattr(settings, "CONFIG_PATH", path)
    yield path
    # ensure env leakage can't cross tests
    for name in ("PUSHFRAME_LOCALE", "AURA_LOCALE"):
        monkeypatch.delenv(name, raising=False)


def _write(path, payload):
    path.write_text(json.dumps(payload))


def test_default_when_no_file_no_env(config_file, monkeypatch):
    monkeypatch.delenv("PUSHFRAME_LOCALE", raising=False)
    monkeypatch.delenv("AURA_LOCALE", raising=False)
    assert settings.LOCALE == "en-US"


def test_config_file_beats_default(config_file, monkeypatch):
    monkeypatch.delenv("PUSHFRAME_LOCALE", raising=False)
    monkeypatch.delenv("AURA_LOCALE", raising=False)
    _write(config_file, {"version": 1, "settings": {"LOCALE": "fr-FR"}})
    assert settings.LOCALE == "fr-FR"


def test_env_beats_config_file(config_file, monkeypatch):
    _write(config_file, {"version": 1, "settings": {"LOCALE": "fr-FR"}})
    monkeypatch.setenv("PUSHFRAME_LOCALE", "de-DE")
    assert settings.LOCALE == "de-DE"


def test_legacy_aura_env_still_read(config_file, monkeypatch):
    _write(config_file, {"version": 1, "settings": {"LOCALE": "fr-FR"}})
    monkeypatch.delenv("PUSHFRAME_LOCALE", raising=False)
    monkeypatch.setenv("AURA_LOCALE", "es-ES")
    assert settings.LOCALE == "es-ES"


def test_numeric_and_bool_settings_resolve(config_file, monkeypatch):
    monkeypatch.delenv("PUSHFRAME_WRITE_BUDGET_CAPACITY", raising=False)
    monkeypatch.delenv("AURA_WRITE_BUDGET_CAPACITY", raising=False)
    monkeypatch.delenv("PUSHFRAME_WRITE_BUDGET_WAIT", raising=False)
    monkeypatch.delenv("AURA_WRITE_BUDGET_WAIT", raising=False)
    _write(config_file, {"version": 1, "settings": {
        "AURA_WRITE_BUDGET_CAPACITY": 60,
        "AURA_WRITE_BUDGET_WAIT": False,
    }})
    assert settings.AURA_WRITE_BUDGET_CAPACITY == 60.0
    assert settings.AURA_WRITE_BUDGET_WAIT is False


def test_unknown_name_raises_with_known_keys(config_file):
    with pytest.raises(AttributeError) as exc:
        settings.NOT_A_REAL_KEY
    assert "NOT_A_REAL_KEY" in str(exc.value)
    assert "LOCALE" in str(exc.value)  # known keys listed


def test_shadowed_keys_reports_env_overrides(config_file, monkeypatch):
    _write(config_file, {"version": 1, "settings": {
        "LOCALE": "fr-FR", "AURA_WRITE_BUDGET_CAPACITY": 60}})
    monkeypatch.setenv("PUSHFRAME_LOCALE", "de-DE")
    shadowed = settings.shadowed_keys({"LOCALE": "fr-FR",
                                       "AURA_WRITE_BUDGET_CAPACITY": 60})
    assert shadowed == ["LOCALE"]


def test_corrupt_config_file_fails_loud(config_file):
    config_file.write_text("{not json")
    with pytest.raises(ValueError) as exc:
        settings.LOCALE
    assert str(config_file) in str(exc.value)

"""Offline tests for the Phase 09 (proactive-write-rate-limiter-geo-guard)
additions to `pushframe.utils.settings` -- the write-budget/geo-guard env
config plus the new `_bool_env` helper (ANTI-05).

`settings.py` reads all env vars at MODULE IMPORT time (matching the
pre-existing `LOCALE`/`AURA_APP_IDENTIFIER`/`DEVICE_IDENTIFIER` convention),
so every test here uses `monkeypatch.setenv` + `importlib.reload` to force a
fresh read rather than relying on values captured at collection time.
"""
import importlib
from pathlib import Path

import pytest

import pushframe.utils.settings as settings


@pytest.fixture(autouse=True)
def _reload_settings_after_test():
    # Ensure a test's monkeypatched env vars never leak into a later test's
    # module-level settings via a stale reload -- monkeypatch itself restores
    # the env, but settings.py's already-imported values won't refresh
    # without one final reload back to the "no override" state.
    yield
    importlib.reload(settings)


def _clear_all_budget_geo_env(monkeypatch):
    for name in (
        'AURA_WRITE_BUDGET_CAPACITY',
        'AURA_WRITE_BUDGET_REFILL_PER_MIN',
        'AURA_WRITE_BUDGET_WAIT',
        'AURA_WRITE_BUDGET_MAX_WAIT',
        'AURA_COUNTRY',
        'AURA_GEO_FAIL_OPEN',
        'AURA_STATE_DIR',
    ):
        monkeypatch.delenv(name, raising=False)


def test_defaults_when_no_env_vars_set(monkeypatch):
    _clear_all_budget_geo_env(monkeypatch)
    importlib.reload(settings)

    assert settings.AURA_WRITE_BUDGET_CAPACITY == 30.0
    assert settings.AURA_WRITE_BUDGET_REFILL_PER_MIN == 0.75
    assert settings.AURA_WRITE_BUDGET_WAIT is True
    assert settings.AURA_WRITE_BUDGET_MAX_WAIT == 3600.0
    assert settings.AURA_COUNTRY is None
    assert settings.AURA_GEO_FAIL_OPEN is True
    assert settings.AURA_STATE_DIR == Path('~/.config/pushframe').expanduser()


def test_numeric_env_vars_are_parsed_as_floats(monkeypatch):
    _clear_all_budget_geo_env(monkeypatch)
    monkeypatch.setenv('AURA_WRITE_BUDGET_CAPACITY', '50')
    monkeypatch.setenv('AURA_WRITE_BUDGET_REFILL_PER_MIN', '1.5')
    monkeypatch.setenv('AURA_WRITE_BUDGET_MAX_WAIT', '120')
    importlib.reload(settings)

    assert settings.AURA_WRITE_BUDGET_CAPACITY == 50.0
    assert settings.AURA_WRITE_BUDGET_REFILL_PER_MIN == 1.5
    assert settings.AURA_WRITE_BUDGET_MAX_WAIT == 120.0


def test_aura_country_set_is_passed_through_verbatim(monkeypatch):
    _clear_all_budget_geo_env(monkeypatch)
    monkeypatch.setenv('AURA_COUNTRY', 'FR')
    importlib.reload(settings)

    assert settings.AURA_COUNTRY == 'FR'


def test_aura_state_dir_expands_tilde(monkeypatch):
    _clear_all_budget_geo_env(monkeypatch)
    monkeypatch.setenv('AURA_STATE_DIR', '~/custom-aura-state')
    importlib.reload(settings)

    assert settings.AURA_STATE_DIR == Path.home() / 'custom-aura-state'
    assert '~' not in str(settings.AURA_STATE_DIR)


@pytest.mark.parametrize('raw,expected', [
    ('1', True),
    ('true', True),
    ('True', True),
    ('TRUE', True),
    ('yes', True),
    ('on', True),
    ('0', False),
    ('false', False),
    ('False', False),
    ('no', False),
    ('off', False),
    ('garbage', False),
])
def test_bool_env_parses_common_string_forms(monkeypatch, raw, expected):
    monkeypatch.setenv('AURA_TEST_BOOL_FLAG', raw)
    assert settings._bool_env('AURA_TEST_BOOL_FLAG', default=not expected) is expected


def test_bool_env_missing_var_returns_default(monkeypatch):
    monkeypatch.delenv('AURA_TEST_BOOL_FLAG_UNSET', raising=False)
    assert settings._bool_env('AURA_TEST_BOOL_FLAG_UNSET', default=True) is True
    assert settings._bool_env('AURA_TEST_BOOL_FLAG_UNSET', default=False) is False


def test_write_budget_wait_and_geo_fail_open_parse_via_bool_env(monkeypatch):
    _clear_all_budget_geo_env(monkeypatch)
    monkeypatch.setenv('AURA_WRITE_BUDGET_WAIT', 'false')
    monkeypatch.setenv('AURA_GEO_FAIL_OPEN', 'no')
    importlib.reload(settings)

    assert settings.AURA_WRITE_BUDGET_WAIT is False
    assert settings.AURA_GEO_FAIL_OPEN is False


# --- MOD-02 (Phase 19): AWS bucket + pool IDs are settings, not literals ---

_AWS_ENV_NAMES = ('AURA_AWS_S3_BUCKET', 'AURA_AWS_UPLOAD_POOL_ID', 'AURA_AWS_SQS_POOL_ID')

# The exact literals that shipped hardcoded for years -- unset env must yield
# byte-identical behavior (ROADMAP phase-19 criterion 2).
_SHIPPED_DEFAULTS = {
    'AWS_S3_BUCKET': 'images.senseapp.co',
    'AWS_UPLOAD_IDENTITY_POOL_ID': 'us-east-1:b92826c0-8274-43db-abff-136977c13598',
    'AWS_SQS_IDENTITY_POOL_ID': 'us-east-1:98ccd0ff-69fe-4e9a-ad34-671b4381ab12',
}


def _clear_aws_env(monkeypatch):
    for name in _AWS_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)


def test_aws_settings_default_to_the_shipped_literals(monkeypatch):
    _clear_aws_env(monkeypatch)
    importlib.reload(settings)

    assert settings.AWS_S3_BUCKET == _SHIPPED_DEFAULTS['AWS_S3_BUCKET']
    assert settings.AWS_UPLOAD_IDENTITY_POOL_ID == _SHIPPED_DEFAULTS['AWS_UPLOAD_IDENTITY_POOL_ID']
    assert settings.AWS_SQS_IDENTITY_POOL_ID == _SHIPPED_DEFAULTS['AWS_SQS_IDENTITY_POOL_ID']


def test_aws_settings_are_env_overridable(monkeypatch):
    _clear_aws_env(monkeypatch)
    monkeypatch.setenv('AURA_AWS_S3_BUCKET', 'bucket.other.invalid')
    monkeypatch.setenv('AURA_AWS_UPLOAD_POOL_ID', 'us-east-1:00000000-0000-0000-0000-000000000001')
    monkeypatch.setenv('AURA_AWS_SQS_POOL_ID', 'us-east-1:00000000-0000-0000-0000-000000000002')
    importlib.reload(settings)

    assert settings.AWS_S3_BUCKET == 'bucket.other.invalid'
    assert settings.AWS_UPLOAD_IDENTITY_POOL_ID.endswith('000000000001')
    assert settings.AWS_SQS_IDENTITY_POOL_ID.endswith('000000000002')


def test_aws_modules_wire_settings_not_literals():
    # The AWS modules must read through settings (MOD-02's whole point): their
    # module attributes must track settings values by identity-of-config, and
    # the module source must contain no hardcoded literal anymore.
    from pushframe.aws import s3client, sqsclient

    assert s3client.BUCKET_KEY == settings.AWS_S3_BUCKET
    assert s3client.UPLOAD_IDENTITY_POOL_ID == settings.AWS_UPLOAD_IDENTITY_POOL_ID
    assert sqsclient.SQS_IDENTITY_POOL_ID == settings.AWS_SQS_IDENTITY_POOL_ID

    for path in (s3client.__file__, sqsclient.__file__):
        src = Path(path).read_text()
        assert 'images.senseapp.co' not in src
        assert "'us-east-1:" not in src

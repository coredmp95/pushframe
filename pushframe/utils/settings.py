import os
from pathlib import Path


def _env(*names: str) -> str | None:
    """First set variable among `names` (IDN-04, phase 20): PUSHFRAME_* is the
    documented primary spelling, the legacy AURA_* spellings remain readable
    as a one-release fallback. Explicitly-set values win in the order given."""
    for name in names:
        value = os.getenv(name)
        if value is not None:
            return value
    return None


LOCALE = _env('PUSHFRAME_LOCALE', 'AURA_LOCALE') or 'en-US'
AURA_APP_IDENTIFIER = _env('PUSHFRAME_APP_IDENTIFIER', 'AURA_APP_IDENTIFIER') or 'com.pushd.client'
# TODO: Load device identifier through config
DEVICE_IDENTIFIER = _env('PUSHFRAME_DEVICE_IDENTIFIER', 'AURA_DEVICE_IDENTIFIER') or '0000000000000000'
IMAGE_PROXY_BASE_URL = 'https://imgproxy.pushd.com'


def _bool_env(name: str, default: bool) -> bool:
    """Defensive boolean env-var parser (Phase 09, ANTI-05) -- Python's
    built-in `bool("false")` is `True`, so every non-empty string env var
    needs this membership test instead. Missing var -> `default`; present
    -> case-insensitive membership in `('1', 'true', 'yes', 'on')`."""
    return _bool_env2(os.getenv(name), default)


def _bool_env2(raw: str | None, default: bool) -> bool:
    """Boolean parse of an already-resolved raw value (IDN-04): None ->
    default; present -> case-insensitive membership in ('1','true','yes','on')."""
    if raw is None:
        return default
    return raw.strip().lower() in ('1', 'true', 'yes', 'on')


# Proactive write-rate-budget + geo pre-flight guard config (Phase 09,
# ANTI-05) -- the first non-string env vars in this codebase, hence the
# `_bool_env` helper above and bare `float(...)` for the numeric ones.
AURA_WRITE_BUDGET_CAPACITY = float(_env('PUSHFRAME_WRITE_BUDGET_CAPACITY', 'AURA_WRITE_BUDGET_CAPACITY') or '30')
AURA_WRITE_BUDGET_REFILL_PER_MIN = float(_env('PUSHFRAME_WRITE_BUDGET_REFILL_PER_MIN', 'AURA_WRITE_BUDGET_REFILL_PER_MIN') or '0.75')
AURA_WRITE_BUDGET_WAIT = _bool_env2(_env('PUSHFRAME_WRITE_BUDGET_WAIT', 'AURA_WRITE_BUDGET_WAIT'), True)
AURA_WRITE_BUDGET_MAX_WAIT = float(_env('PUSHFRAME_WRITE_BUDGET_MAX_WAIT', 'AURA_WRITE_BUDGET_MAX_WAIT') or '3600')
# None -> geo pre-flight check skipped entirely, per check_geo's falsy contract.
AURA_COUNTRY = _env('PUSHFRAME_COUNTRY', 'AURA_COUNTRY')
AURA_GEO_FAIL_OPEN = _bool_env2(_env('PUSHFRAME_GEO_FAIL_OPEN', 'AURA_GEO_FAIL_OPEN'), True)
AURA_STATE_DIR = Path(_env('PUSHFRAME_STATE_DIR', 'AURA_STATE_DIR') or '~/.config/pushframe').expanduser()

# AWS endpoints/identifiers (Phase 19, MOD-02) -- moved out of the hardcoded
# module constants in pushframe/aws/s3client.py and sqsclient.py (closing
# their `TODO: read them in through config?`). Defaults ARE the literals that
# shipped for years, so an unset environment behaves byte-identically.
AWS_S3_BUCKET = _env('PUSHFRAME_AWS_S3_BUCKET', 'AURA_AWS_S3_BUCKET') or 'images.senseapp.co'
AWS_UPLOAD_IDENTITY_POOL_ID = _env(
    'PUSHFRAME_AWS_UPLOAD_POOL_ID', 'AURA_AWS_UPLOAD_POOL_ID') or 'us-east-1:b92826c0-8274-43db-abff-136977c13598'
AWS_SQS_IDENTITY_POOL_ID = _env(
    'PUSHFRAME_AWS_SQS_POOL_ID', 'AURA_AWS_SQS_POOL_ID') or 'us-east-1:98ccd0ff-69fe-4e9a-ad34-671b4381ab12'

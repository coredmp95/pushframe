"""pushframe settings — resolved at ACCESS time (phase 23, CFG-02).

Precedence (locked, 23-CONTEXT D-04):
    1. environment variable  (PUSHFRAME_* primary, AURA_* legacy — IDN-04)
    2. config file           (~/.config/pushframe/config.json, 0600)
    3. built-in default      (the DEFAULTS table below)

Every importer keeps reading `settings.X` attribute-style: resolution
happens in the module-level `__getattr__` (PEP 562), so `pushframe config
set` changes what the next command sees without touching any importer.
The historical import-time constants are gone — anything that used to
`from settings import X` must switch to `import settings` (the aws/*
clients did, phase 23).
"""
import os
from pathlib import Path

CONFIG_PATH = Path(os.getenv('PUSHFRAME_CONFIG_FILE',
                             '~/.config/pushframe/config.json')).expanduser()

# The single table: settings name -> (env spellings, default, caster).
# Defaults ARE the literals that shipped for years (MOD-02 posture), so an
# empty config + empty environment behaves byte-identically to 5.0.x —
# sole deliberate exception: USER_AGENT's default was bumped to a current
# Play build in 5.1.1 because the years-stale identity feeds the
# anti-abuse trip (venus lesson; see docs/CLI.md "Client identity").
DEFAULTS: dict[str, dict] = {
    'LOCALE':                          {'env': ('PUSHFRAME_LOCALE', 'AURA_LOCALE'), 'default': 'en-US'},
    'AURA_APP_IDENTIFIER':             {'env': ('PUSHFRAME_APP_IDENTIFIER', 'AURA_APP_IDENTIFIER'), 'default': 'com.pushd.client'},
    'DEVICE_IDENTIFIER':               {'env': ('PUSHFRAME_DEVICE_IDENTIFIER', 'AURA_DEVICE_IDENTIFIER'), 'default': '0000000000000000'},
    'USER_AGENT':                      {'env': ('PUSHFRAME_USER_AGENT', 'AURA_USER_AGENT'), 'default': 'Aura/4.7.4271 (Android 36; Client)'},
    'IMAGE_PROXY_BASE_URL':            {'env': (), 'default': 'https://imgproxy.pushd.com'},
    'AURA_API_BASE_URL':               {'env': ('PUSHFRAME_API_BASE_URL',), 'default': 'https://api.pushd.com/v5'},
    'AURA_API_VERSION':                {'env': ('PUSHFRAME_API_VERSION',), 'default': 'v5'},
    'AURA_WRITE_BUDGET_CAPACITY':      {'env': ('PUSHFRAME_WRITE_BUDGET_CAPACITY', 'AURA_WRITE_BUDGET_CAPACITY'), 'default': '30', 'cast': float},
    'AURA_WRITE_BUDGET_REFILL_PER_MIN': {'env': ('PUSHFRAME_WRITE_BUDGET_REFILL_PER_MIN', 'AURA_WRITE_BUDGET_REFILL_PER_MIN'), 'default': '0.75', 'cast': float},
    'AURA_WRITE_BUDGET_WAIT':          {'env': ('PUSHFRAME_WRITE_BUDGET_WAIT', 'AURA_WRITE_BUDGET_WAIT'), 'default': True, 'cast': 'bool'},
    'AURA_WRITE_BUDGET_MAX_WAIT':      {'env': ('PUSHFRAME_WRITE_BUDGET_MAX_WAIT', 'AURA_WRITE_BUDGET_MAX_WAIT'), 'default': '3600', 'cast': float},
    'AURA_COUNTRY':                    {'env': ('PUSHFRAME_COUNTRY', 'AURA_COUNTRY'), 'default': None},
    'AURA_GEO_FAIL_OPEN':              {'env': ('PUSHFRAME_GEO_FAIL_OPEN', 'AURA_GEO_FAIL_OPEN'), 'default': True, 'cast': 'bool'},
    'AURA_STATE_DIR':                  {'env': ('PUSHFRAME_STATE_DIR', 'AURA_STATE_DIR'), 'default': '~/.config/pushframe', 'cast': Path},
    'AWS_S3_BUCKET':                   {'env': ('PUSHFRAME_AWS_S3_BUCKET', 'AURA_AWS_S3_BUCKET'), 'default': 'images.senseapp.co'},
    'AWS_UPLOAD_IDENTITY_POOL_ID':     {'env': ('PUSHFRAME_AWS_UPLOAD_POOL_ID', 'AURA_AWS_UPLOAD_POOL_ID'), 'default': 'us-east-1:b92826c0-8274-43db-abff-136977c13598'},
    'AWS_SQS_IDENTITY_POOL_ID':        {'env': ('PUSHFRAME_AWS_SQS_POOL_ID', 'AURA_AWS_SQS_POOL_ID'), 'default': 'us-east-1:98ccd0ff-69fe-4e9a-ad34-671b4381ab12'},
}


def _env(*names: str) -> str | None:
    """First set variable among `names` (IDN-04, phase 20). Kept for any
    legacy internal caller; resolution now goes through resolve()."""
    for name in names:
        value = os.getenv(name)
        if value is not None:
            return value
    return None


def _bool_env(name: str, *, default: bool) -> bool:
    """Defensive boolean env-var parser (Phase 09, ANTI-05) — historical
    signature kept: Python's `bool("false")` is True, so membership test.
    Missing var -> `default`; present -> case-insensitive membership in
    ('1', 'true', 'yes', 'on')."""
    return _bool_env2(os.getenv(name), default)


def _bool_env2(raw: str | None, default: bool) -> bool:
    """Boolean parse of an already-resolved raw value (IDN-04): None ->
    default; present -> case-insensitive membership in ('1','true','yes','on')."""
    if raw is None:
        return default
    return raw.strip().lower() in ('1', 'true', 'yes', 'on')


def _cast(name: str, raw):
    spec = DEFAULTS[name]
    cast = spec.get('cast')
    if cast is None:
        return raw
    if cast == 'bool':
        return raw if isinstance(raw, bool) else _bool_env2(str(raw), spec['default'])
    if cast is Path:
        return Path(str(raw)).expanduser()
    return cast(raw)


def resolve(name: str):
    """config file → env var → built-in default (D-04), cast to type."""
    spec = DEFAULTS[name]
    # 1. env (primary then legacy spelling)
    for env_name in spec['env']:
        value = os.getenv(env_name)
        if value is not None:
            return _cast(name, value)
    # 2. config file (the store validates schema; missing file = no config)
    from pushframe import config_store
    file_value = config_store.setting(name)
    if file_value is not None:
        return _cast(name, file_value)
    # 3. default
    return _cast(name, spec['default'])


def known_keys() -> list[str]:
    """Every settings name that may appear in the config file (CFG-04)."""
    return sorted(DEFAULTS)


def shadowed_keys(values: dict) -> list[str]:
    """Which of `values` (proposed file writes) would an env var override
    right now — the wizard's D-04 warning source (venus lesson)."""
    out = []
    for name, _value in values.items():
        spec = DEFAULTS.get(name)
        if spec and any(os.getenv(e) is not None for e in spec['env']):
            out.append(name)
    return sorted(out)


def __getattr__(name: str):
    """PEP 562: resolution at access time (D-01). Importers keep
    `settings.X`; unknown names fail listing the known keys (CFG-04)."""
    if name in DEFAULTS:
        return resolve(name)
    raise AttributeError(
        f"{name!r} is not a pushframe setting. Known settings: {', '.join(known_keys())}"
    )

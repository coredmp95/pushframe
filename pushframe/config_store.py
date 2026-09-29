"""The pushframe config file store (phase 23, CFG-01..04).

~/.config/pushframe/config.json — mode 0600, atomic writes (tmp+rename,
the google-manifest pattern), schema-versioned with a strict key
whitelist: unknown top-level keys fail loud on load (corruption /
foreign-writer detection, mirroring the manifest contract). The `pairs`
key is RESERVED for phase 25 (MTF-01) and validated as an object even
while empty.
"""
import json
import os
import tempfile
from pathlib import Path

from pushframe.utils import settings

SCHEMA_VERSION = 1


def _path() -> Path:
    """CONFIG_PATH read dynamically: tests (and `config set`) point
    settings.CONFIG_PATH elsewhere and the store must follow — a
    from-import would freeze the path at first import."""
    return Path(settings.CONFIG_PATH)
KEY_WHITELIST = {'version', 'email', 'auth_token', 'user_id', 'default_frame',
                 'debug', 'settings', 'pairs'}


class ConfigError(ValueError):
    """Raised for a corrupt or schema-invalid config file."""


def load(path: Path | None = None) -> dict:
    """Load the config file; absent file -> a fresh empty template."""
    path = Path(path) if path is not None else _path()
    if not path.exists():
        return {'version': SCHEMA_VERSION, 'settings': {}, 'pairs': {}}
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError as e:
        raise ConfigError(f'config file {path} is not valid JSON: {e}') from e
    if not isinstance(data, dict):
        raise ConfigError(f'config file {path} must contain an object')
    unknown = set(data) - KEY_WHITELIST
    if unknown:
        raise ConfigError(
            f'config file {path} has unknown key(s): {sorted(unknown)}. '
            f'Allowed: {sorted(KEY_WHITELIST)}')
    if data.get('version') != SCHEMA_VERSION:
        raise ConfigError(
            f'config file {path} has schema version {data.get("version")!r}, '
            f'expected {SCHEMA_VERSION}')
    data.setdefault('settings', {})
    data.setdefault('pairs', {})
    if not isinstance(data['settings'], dict):
        raise ConfigError(f'config file {path}: "settings" must be an object')
    if not isinstance(data['pairs'], dict):
        raise ConfigError(f'config file {path}: "pairs" must be an object')
    return data


def save(data: dict, path: Path | None = None) -> Path:
    """Atomic 0600 write (tmp + rename, root-owned by umask coverage)."""
    path = Path(path) if path is not None else _path()
    unknown = set(data) - KEY_WHITELIST
    if unknown:
        raise ConfigError(f'unknown config key(s): {sorted(unknown)}')
    data['version'] = SCHEMA_VERSION
    data.setdefault('settings', {})
    data.setdefault('pairs', {})
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix='.config-', suffix='.tmp')
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, 'w') as fh:
            json.dump(data, fh, indent=2, sort_keys=True)
            fh.write('\n')
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise
    os.chmod(path, 0o600)
    return path


def update(**kwargs) -> Path:
    """Merge keys into the existing config and save. A None VALUE removes
    the key (phase 24 logout semantics: deleting the token is an update
    whose result is the key's ABSENCE, not a stored null)."""
    data = load()
    for key, value in kwargs.items():
        if value is None:
            data.pop(key, None)
        else:
            data[key] = value
    return save(data)


def setting(name: str):
    """The file value for a settings name, or None (settings.resolve's
    step 2). Values live under the "settings" object; email/auth_token/
    default_frame/debug are top-level convenience keys."""
    data = load()
    if name in ('email', 'auth_token', 'default_frame', 'debug'):
        return data.get(name)
    return data.get('settings', {}).get(name)

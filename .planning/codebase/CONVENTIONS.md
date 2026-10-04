# Coding Conventions

**Analysis Date:** 2026-06-29

## Naming Patterns

**Files:**
- Modules use `camelCase` with a capital first letter for the class they contain: `frameApi.py`, `accountApi.py`, `assetApi.py`, `baseApi.py`
- Exception: utility modules use `snake_case`: `settings.py`, `io.py`, `dt.py`
- AWS client files follow the pattern: `awsclient.py`, `s3client.py`, `sqsclient.py`
- Model files use `snake_case` for the concept they model: `asset.py`, `activity.py`, `frame.py`, `user.py`, `person.py`, `meta.py`

**Classes:**
- PascalCase throughout: `FrameApi`, `AccountApi`, `AssetApi`, `BaseApi`, `AWSClient`, `S3Client`, `SQSClient`, `ExifWriter`
- Model classes: `Asset`, `Frame`, `User`, `Activity`, `Person`, `Comment`, `Reaction`
- Enum classes: `Feature`, `ActivityType`, `ReactionType`
- Partial/variant model classes append suffix: `AssetPartialId`, `FramePartial`, `AssetSetting`, `SuggestionManifest`

**Functions/Methods:**
- `snake_case` throughout: `get_frames()`, `get_assets()`, `build_path()`, `parse_aura_dt()`, `write_model()`
- Private methods prefixed with underscore: `_set_cookies()`, `_lookup_gps()`, `_init_logger()`, `_get_path_safe_datetime()`
- Boolean properties use `is_` prefix: `is_portrait()`, `is_local_asset`

**Variables:**
- `snake_case` for local variables and parameters: `frame_id`, `asset_data`, `json_response`, `query_params`
- Constants use `UPPER_SNAKE_CASE`: `AURA_API_BASE_URL`, `AURA_API_VERSION`, `USER_AGENT`, `BUCKET_KEY`, `AURA_DT_FORMAT`
- Module-level settings use `UPPER_SNAKE_CASE`: `LOCALE`, `DEVICE_IDENTIFIER`, `IMAGE_PROXY_BASE_URL`

**Parameters:**
- Pydantic model field names use `snake_case` matching the API's JSON keys directly: `added_by_id`, `created_at`, `frame_id`
- Avoid shadowing builtins: parameter `_filter` used instead of `filter` in `PlaylistApi.get_playlist_assets()`

## Code Style

**Formatting:**
- No formatter configuration detected (no `.prettierrc`, `pyproject.toml` with black config, or similar)
- Indentation: 4 spaces (PEP 8 standard)
- Line length: not enforced by config; some lines are long (e.g., multi-line `httpx.Client()` constructor in `client.py`)

**Linting:**
- No linting config detected (no `.flake8`, `pylintrc`, `ruff.toml`, or `pyproject.toml`)
- Codebase follows PEP 8 naming but has no automated enforcement

## Import Organization

**Order observed:**
1. Standard library imports (`os`, `sys`, `json`, `uuid`, `time`, `datetime`, `io`, `typing`)
2. Third-party imports (`boto3`, `httpx`, `loguru`, `pydantic`, `PIL`, `piexif`, `geopy`, `tqdm`)
3. Local package imports (`from pushframe.api...`, `from pushframe.models...`, `from pushframe.utils...`)

**Pattern:**
- Each import group separated by blank line (observed in `aura.py`, `client.py`, `export.py`)
- Explicit named imports preferred: `from httpx import Response, Timeout`
- `from __future__ import annotations` used in files with forward references (`asset.py`, `activity.py`)

**Path Aliases:**
- None — all imports use full `pushframe.*` package paths

## Error Handling

**Patterns:**
- Minimal error handling remains in the inherited API layer; one `# TODO: Error handling` stub with a bare `pass` survives (`accountApi.py:60`) — every write/delete endpoint and the whole CLI surface raise typed errors
- HTTP error responses checked via `json_response.get('error')` but not acted upon in most cases
- `exif.py` uses only typed `except Exception:` handlers; EXIF write failures fail loudly (D-11) — the old silent empty-`BytesIO` behavior is gone
- `try/except Exception as e` used in `aura.py:download_images_from_assets()` — failed assets are collected but not re-raised
- Typed exception hierarchy: `AuraError` base with `AuthenticationError` / `WriteEndpointError` / `RateLimitError` (`client.py`), plus `SafeSyncError` (`gsync.py`), `ScheduleError` (`schedule.py`), `PreflightError` (`preflight.py`), `ConfigError` (`config_store.py`)
- Pydantic validation is the primary mechanism for catching bad data from the API (model construction raises on invalid types)
- `AssetPartialId` uses a Pydantic `@model_validator(mode='after')` for cross-field validation (`asset.py:145`)

**Example of current error handling approach:**
```python
# accountApi.py
if json_response.get('error') or not json_response.get('result'):
    # TODO: Error handling
    pass
```

## Logging

**Framework:** `loguru` (`~=0.6.0`)

**Configuration:**
- Configured once in `Aura._init_logger()` (`aura.py:187`) under the MOD-04 process guard — the first `Aura()` construction sets the stderr + file sinks, later constructions are sink no-ops (no teardown; `cli.py`'s `_configure_cli_logging()` owns wholesale reconfiguration)
- Two sinks: `sys.stderr` at `INFO` level and `logs/file_{time}.log` (all levels)
- Structured format with timestamp, level, module/function/line, message, and `{extra}` context

**Patterns:**
- `logger.info(f'...')` for HTTP request logging (called with keyword args for extra context)
- `logger.debug(f'...')` for response bodies and cookies
- `logger.error(e)` for caught exceptions in `upload_image()`
- `loguru` structured binding via keyword args: `logger.info(f'GET request to {url}', query_params=..., headers=...)`

## Comments

**When to Comment:**
- TODOs remain (~26 instances) — mostly inherited-library markers in `api/`/`aws/` and open questions about the undocumented upstream API
- Inline comments explain unclear API behavior: `# Typical use of this endpoint results in a single AssetPartialId being sent per call.`
- Attribution comments for borrowed code: `# Most of the exif writing is from: <url>` (`exif.py:14`)
- The old commented-out `# logger.remove()` in `aura.py` is gone — MOD-04's `_LOGGER_READY` process guard replaced it

**Docstrings:**
- All public API methods in `frameApi.py`, `accountApi.py`, `assetApi.py`, `activityApi.py` have docstrings
- Format: reStructuredText (`:param name:`, `:return:`) inline style
- Docstrings document parameter semantics and known unknowns about the upstream API
- Model classes and utility functions generally lack docstrings

**Example docstring pattern:**
```python
def get_assets(self, frame_id: str, limit: int = 1000, cursor: str = None) -> tuple[list[Asset], str]:
    """
    Gets assets for a `frame_id`. The results are paginated with `limit` results per page.

    :param frame_id: Frame ID to retrieve assets
    :param limit: Maximum number of assets per page / callout.
    :param cursor: The cursor from the previous page.
    :return: List of all the assets, and the next page's cursor (will be `None` if no more pages)
    """
```

## Function Design

**Size:** Methods are small and single-purpose; most API methods are 5–15 lines

**Parameters:**
- Type hints used on all public method signatures
- Optional parameters typed with `Optional[T]` from `typing` or default `= None`
- Pydantic model instances passed as parameters rather than raw dicts: `update_frame(frame_id, frame_partial: FramePartial)`

**Return Values:**
- Typed with return type annotations on public methods
- Pydantic models returned from API calls (not raw dicts)
- Multiple return values via `tuple`: `get_frame() -> tuple[Frame, int]`, `get_comments() -> tuple[list[Comment], int, list[User]]`
- `.json()` always called immediately on httpx responses; raw `Response` objects never returned from the `Client` layer

## Module Design

**Exports:**
- No `__all__` declarations in any module
- `pushframe/models/__init__.py` and `pushframe/api/__init__.py` are empty
- `pushframe/__init__.py` defines `__version__` only (single-sourced with pyproject, pinned by `tests/test_version.py`); `models/__init__.py` and `api/__init__.py` are empty — consumers import specific submodules

**Barrel Files:**
- Not used; imports go directly to source modules: `from pushframe.api.frameApi import FrameApi`

## Data Modeling

**Framework:** Pydantic v2 (>=2; migrated from v1.10 in v1.0)

**Patterns:**
- All API response shapes modeled as `pydantic.BaseModel` subclasses
- Fields typed with `Optional[T]` for nullable/missing API fields
- `Any` used when field type is unknown: `burst_id: Any`, `playlist: typing.Any`
- `from __future__ import annotations` enables forward references in models
- Partial model pattern via the `make_partial()` `create_model` factory (`meta.py`): `FramePartial = make_partial(Frame, "FramePartial")`
- Enums used for known string-valued discriminators: `ActivityType`, `ReactionType`, `Feature`
- Pydantic `@model_validator` used for cross-field validation: `AssetPartialId.check_id_or_local_id`
- `.model_dump(include={...})` used to build API request payloads from model instances

## Configuration

**Pattern:** Call-time resolution from a `SETTINGS` registry (`utils/settings.py`) — `PUSHFRAME_*` env vars primary, `AURA_*` legacy fallback, then config.json, then defaults; never read at import time (the v1.0 HTTP-475 early-bound-default lesson)

- Credentials: token-first sessions — `auth_token` in `~/.config/pushframe/config.json` (0600); `PUSHFRAME_EMAIL`/`PUSHFRAME_PASSWORD` (legacy `AURA_*`) remain the CI/script override; the password is never persisted
- AWS pool IDs and bucket name are settings-backed since MOD-02 (`AWS_S3_BUCKET`, `AWS_UPLOAD_IDENTITY_POOL_ID`, `AWS_SQS_IDENTITY_POOL_ID`) — no hardcoded literals

---

*Convention analysis: 2026-06-29, corrected 2026-10-04 against the 5.1.28 tree.*

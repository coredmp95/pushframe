<!-- GSD:project-start source:PROJECT.md -->
## Project

**pushframe — unofficial Aura Frames CLI**

An unofficial, reverse-engineered Python CLI for the Aura Frames (Pushd) digital
photo-frame cloud API. It authenticates with an Aura *account* and pulls/pushes photos
through the cloud API (`api.pushd.com/v5`) plus AWS S3/SQS — it does not talk to the
frame over the local network. Shipped as the `pushframe` package (PyPI + signed APT +
GitHub Releases — one tag, every channel); current line 5.1.x.

**Core Value:** an unattended-operable CLI — one conversational `pushframe config`
(token-first sessions, the password never persisted), named album↔frame pairs,
`pushframe schedule` systemd user timers for unattended mirrors, and every foreseeable
failure surfacing as a named error with a remedy instead of a traceback.

### Constraints

- **Tech stack**: Python 3.14 with `uv` for env/dependency/interpreter management — user decision.
- **API**: Unofficial, reverse-engineered Aura/Pushd cloud API (`api.pushd.com/v5`) — undocumented and may change without notice; verification is inherently against a moving target.
- **Auth**: Requires live Aura account credentials (`AURA_EMAIL`/`AURA_PASSWORD`); secrets must stay out of version control.
- **Modernization scope**: Bounded changes; the offline suite stays green (`uv run --extra dev pytest -q -m "not live"` — 636 tests, DeprecationWarning errors); live-API assumptions get a disposable probe before a design commits to them.
<!-- GSD:project-end -->

<!-- GSD:stack-start source:codebase/STACK.md -->
## Technology Stack

*Corrected 2026-10-04 against the 5.1.28 tree; original analysis dated 2026-06-29.*

## Languages
- Python 3.14 (`requires-python >=3.14` — the floor the dependency set is resolved and tested against)
## Runtime & Packaging
- `uv`-managed: PEP 621 `pyproject.toml` + committed `uv.lock` for reproducible installs; dev/test commands run via `uv run`
- Build backend: hatchling + custom hook (`hatch_build.py`); `uv build` emits wheel + sdist
- Console script: `pushframe = "pushframe.cli:main"` — the packaged CLI entry point
## Frameworks
- pydantic >=2 — every API response model and domain object is a `BaseModel` (v2 idioms only: `model_dump`, `field_validator`/`model_validator`, `make_partial()` factory); see `pushframe/models/`
- pytest >=8 (dev extra) — offline suite (636 tests, zero warnings) plus the `@live`-marked drift oracle; run `uv run --extra dev pytest -q -m "not live"`
## Key Dependencies
- httpx[http2]>=0.27 + h2>=4 — primary HTTP client (`pushframe/client.py`), HTTP/2 enabled via `httpx.Client(http2=True)`
- boto3>=1.34 / botocore>=1.34 — AWS SDK used in `pushframe/aws/awsclient.py`, `s3client.py`, `sqsclient.py`
- Pillow>=10.4 + pillow-heif>=1.6 — image reading/thumbnails; HEIC uploads go to S3 as-is (D-09: original bytes untouched, `data_uti='public.heic'`)
- piexif>=1.1.3 - EXIF data reading and writing in `pushframe/exif.py`
- geopy>=2.4 — reverse geocoding via Nominatim in `pushframe/exif.py`
- loguru>=0.7 - Structured logging; configured in `pushframe/aura.py` via `_init_logger()` under a process-level sink guard (MOD-04); writes to `logs/file_{time}.log` and stderr
- tqdm>=4.66 - Progress bars for batch operations
- python-dotenv>=1.0 — `.env` loading for local dev (`main.py`)
- requests>=2.31 — declared but not imported by the package source (kept as a resolved pin)
## Configuration
- Primary: `~/.config/pushframe/config.json` (mode 0600, atomic tmp+rename writes) — email, `auth_token` (token-first sessions; the password is never persisted), default frame, named pairs
- Environment: `PUSHFRAME_*` vars are primary, `AURA_*` kept as a legacy fallback (IDN-04 deprecation window); values resolve at call time via `pushframe/utils/settings.py`, never at import
- `pushframe config` (wizard / show / import / set / get) manages the file; secrets render redacted everywhere
## Platform Requirements
- Python 3.14 + uv; AWS credentials provisioned via Cognito Identity Pool (no static AWS keys required)
- Network access to `api.pushd.com`, `imgproxy.pushd.com`, AWS us-east-1 endpoints
- Google verbs additionally need the `google-browser` extra (playwright) with a Chrome/Chromium for the one-time link
- Entry point: the `pushframe` console script (`pushframe/cli.py`); `main.py` remains the legacy facade-only demo
<!-- GSD:stack-end -->

<!-- GSD:conventions-start source:CONVENTIONS.md -->
## Conventions

## Naming Patterns
- Modules use `camelCase` with a capital first letter for the class they contain: `frameApi.py`, `accountApi.py`, `assetApi.py`, `baseApi.py`
- Exception: utility modules use `snake_case`: `settings.py`, `io.py`, `dt.py`
- AWS client files follow the pattern: `awsclient.py`, `s3client.py`, `sqsclient.py`
- Model files use `snake_case` for the concept they model: `asset.py`, `activity.py`, `frame.py`, `user.py`, `person.py`, `meta.py`
- PascalCase throughout: `FrameApi`, `AccountApi`, `AssetApi`, `BaseApi`, `AWSClient`, `S3Client`, `SQSClient`, `ExifWriter`
- Model classes: `Asset`, `Frame`, `User`, `Activity`, `Person`, `Comment`, `Reaction`
- Enum classes: `Feature`, `ActivityType`, `ReactionType`
- Partial/variant model classes append suffix: `AssetPartialId`, `FramePartial`, `AssetSetting`, `SuggestionManifest`
- `snake_case` throughout: `get_frames()`, `get_assets()`, `build_path()`, `parse_aura_dt()`, `write_model()`
- Private methods prefixed with underscore: `_set_cookies()`, `_lookup_gps()`, `_init_logger()`, `_get_path_safe_datetime()`
- Boolean properties use `is_` prefix: `is_portrait()`, `is_local_asset`
- `snake_case` for local variables and parameters: `frame_id`, `asset_data`, `json_response`, `query_params`
- Constants use `UPPER_SNAKE_CASE`: `AURA_API_BASE_URL`, `AURA_API_VERSION`, `USER_AGENT`, `BUCKET_KEY`, `AURA_DT_FORMAT`
- Module-level settings use `UPPER_SNAKE_CASE`: `LOCALE`, `DEVICE_IDENTIFIER`, `IMAGE_PROXY_BASE_URL`
- Pydantic model field names use `snake_case` matching the API's JSON keys directly: `added_by_id`, `created_at`, `frame_id`
- Avoid shadowing builtins: parameter `_filter` used instead of `filter` in `PlaylistApi.get_playlist_assets()`
## Code Style
- No formatter configuration detected (no `.prettierrc`, `pyproject.toml` with black config, or similar)
- Indentation: 4 spaces (PEP 8 standard)
- Line length: not enforced by config; some lines are long (e.g., multi-line `httpx.Client()` constructor in `client.py`)
- No linting config detected (no `.flake8`, `pylintrc`, `ruff.toml`, or `pyproject.toml`)
- Codebase follows PEP 8 naming but has no automated enforcement
## Import Organization
- Each import group separated by blank line (observed in `aura.py`, `client.py`, `export.py`)
- Explicit named imports preferred: `from httpx import Response, Timeout`
- `from __future__ import annotations` used in files with forward references (`asset.py`, `activity.py`)
- None — all imports use full `pushframe.*` package paths
## Error Handling
- Minimal error handling remains in the inherited API layer; one `# TODO: Error handling` stub with a bare `pass` survives (`accountApi.py:60`) — every write/delete endpoint and the whole CLI surface raise typed errors
- HTTP error responses checked via `json_response.get('error')` but not acted upon in most cases
- `exif.py` uses only typed `except Exception:` handlers; EXIF write failures fail loudly (D-11) — the old silent empty-`BytesIO` behavior is gone
- `try/except Exception as e` used in `aura.py:download_images_from_assets()` — failed assets are collected but not re-raised
- Typed exception hierarchy: `AuraError` base with `AuthenticationError` / `WriteEndpointError` / `RateLimitError` (`client.py`), plus `SafeSyncError` (`gsync.py`), `ScheduleError` (`schedule.py`), `PreflightError` (`preflight.py`), `ConfigError` (`config_store.py`)
- Pydantic validation is the primary mechanism for catching bad data from the API (model construction raises on invalid types)
- `AssetPartialId` uses a Pydantic `@model_validator(mode='after')` for cross-field validation (`asset.py:145`)
## Logging
- Configured once in `Aura._init_logger()` (`aura.py:187`) under the MOD-04 process guard — the first construction sets the sinks, later `Aura()` constructions no longer accumulate duplicates
- Two sinks: `sys.stderr` at `INFO` level and `logs/file_{time}.log` (all levels)
- Structured format with timestamp, level, module/function/line, message, and `{extra}` context
- `logger.info(f'...')` for HTTP request logging (called with keyword args for extra context)
- `logger.debug(f'...')` for response bodies and cookies
- `logger.error(e)` for caught exceptions in `upload_image()`
- `loguru` structured binding via keyword args: `logger.info(f'GET request to {url}', query_params=..., headers=...)`
## Comments
- TODOs remain (~26 instances) — mostly inherited-library markers in `api/`/`aws/` and open questions about the undocumented upstream API
- Inline comments explain unclear API behavior: `# Typical use of this endpoint results in a single AssetPartialId being sent per call.`
- Attribution comments for borrowed code: `# Most of the exif writing is from: <url>` (`exif.py:14`)
- Commented-out code left in place: `# logger.remove()` in `aura.py:132`
- All public API methods in `frameApi.py`, `accountApi.py`, `assetApi.py`, `activityApi.py` have docstrings
- Format: reStructuredText (`:param name:`, `:return:`) inline style
- Docstrings document parameter semantics and known unknowns about the upstream API
- Model classes and utility functions generally lack docstrings
## Function Design
- Type hints used on all public method signatures
- Optional parameters typed with `Optional[T]` from `typing` or default `= None`
- Pydantic model instances passed as parameters rather than raw dicts: `update_frame(frame_id, frame_partial: FramePartial)`
- Typed with return type annotations on public methods
- Pydantic models returned from API calls (not raw dicts)
- Multiple return values via `tuple`: `get_frame() -> tuple[Frame, int]`, `get_comments() -> tuple[list[Comment], int, list[User]]`
- `.json()` always called immediately on httpx responses; raw `Response` objects never returned from the `Client` layer
## Module Design
- No `__all__` declarations in any module
- `pushframe/models/__init__.py` and `pushframe/api/__init__.py` are empty
- `pushframe/__init__.py` defines `__version__` only (single-sourced with pyproject, pinned by `tests/test_version.py`); `models/__init__.py` and `api/__init__.py` are empty — consumers import specific submodules
- Not used; imports go directly to source modules: `from pushframe.api.frameApi import FrameApi`
## Data Modeling
- All API response shapes modeled as `pydantic.BaseModel` subclasses
- Fields typed with `Optional[T]` for nullable/missing API fields
- `Any` used when field type is unknown: `burst_id: Any`, `playlist: typing.Any`
- `from __future__ import annotations` enables forward references in models
- Partial model pattern via the `make_partial()` `create_model` factory (`meta.py`): `FramePartial = make_partial(Frame, "FramePartial")` (`frame.py:105`)
- Enums used for known string-valued discriminators: `ActivityType`, `ReactionType`, `Feature`
- Pydantic `@model_validator` used for cross-field validation: `AssetPartialId.check_id_or_local_id`
- `.model_dump(include={...})` used to build API request payloads from model instances
## Configuration
- Credentials: token-first sessions — `auth_token` in `~/.config/pushframe/config.json` (0600); `PUSHFRAME_EMAIL`/`PUSHFRAME_PASSWORD` (legacy `AURA_*`) remain the CI/script override; the password is never persisted
- AWS pool IDs and bucket name are settings-backed since MOD-02 (`utils/settings.py`: `AWS_S3_BUCKET`, `AWS_UPLOAD_IDENTITY_POOL_ID`, `AWS_SQS_IDENTITY_POOL_ID`, with `PUSHFRAME_*`/`AURA_*` env overrides) — no hardcoded literals
<!-- GSD:conventions-end -->

<!-- GSD:architecture-start source:ARCHITECTURE.md -->
## Architecture

## System Overview
```text
```
## Component Responsibilities
| Component | Responsibility | File |
|-----------|----------------|------|
| `cli` | argparse CLI surface — verbs `status`/`inspect`/`sync`/`push`/`google-*`/`config`/`pair`/`schedule`/`logout`, dispatch, scheduled-run bookkeeping (`--scheduled`/`--report-tag`) | `pushframe/cli.py` |
| `Aura` | Facade; composes all API clients; drives high-level workflows (dump, clone, upload) | `pushframe/aura.py` |
| `Client` | Synchronous HTTP client; manages auth headers, cookies, request history; typed `AuraError` hierarchy | `pushframe/client.py` |
| `session` | Token-first session resolution — config token first, transparent re-login fallback; password never persisted | `pushframe/session.py` |
| `config_store` | `~/.config/pushframe/config.json` (0600, atomic writes, key whitelist, `ConfigError`) | `pushframe/config_store.py` |
| `pairs` | Named album↔frame mappings (N:M) from the config `pairs` key | `pushframe/pairs.py` |
| `sync` | Content-hash directory mirroring — pure `compute_plan()` diff + mutating `execute_plan()`, structurally dry-run default | `pushframe/sync.py` |
| `ratelimit` | `WriteBudget` injected-clock token bucket (persisted per account) + fail-open `check_geo` preflight | `pushframe/ratelimit.py` |
| `gsync` + `google/` | Local Google Photos mirror — cookie-vault link, album walk, safe-sync pipeline (concurrent downloads, pruned cache, manifest, `SafeSyncError`) | `pushframe/gsync.py`, `pushframe/google/` |
| `schedule` | systemd **user** timer/service generation (`schedule add/list/remove`), per-job logs under `~/.local/state/pushframe/` | `pushframe/schedule.py` |
| `preflight` | Machine-prerequisite guards that fail with remedies (browser, vault, systemd session, target dirs) | `pushframe/preflight.py` |
| `BaseApi` | Holds `Client` reference via constructor injection; base class for all API modules | `pushframe/api/baseApi.py` |
| `AccountApi` | Login, register, delete account | `pushframe/api/accountApi.py` |
| `FrameApi` | Frame CRUD, asset association, asset listing, activity listing, playlist management | `pushframe/api/frameApi.py` |
| `AssetApi` | Asset metadata updates, crop, date overrides, delete | `pushframe/api/assetApi.py` |
| `ActivityApi` | Activity comments, asset retrieval, activity deletion | `pushframe/api/activityApi.py` |
| `PeopleApi` | People (face recognition) listing and asset queries | `pushframe/api/peopleApi.py` |
| `PlaylistApi` | Playlist asset retrieval | `pushframe/api/playlistApi.py` |
| `NotificationApi` | Notification settings | `pushframe/api/notificationApi.py` |
| `AWSClient` | Base AWS client; authenticates via Cognito identity pool (anonymous) | `pushframe/aws/awsclient.py` |
| `S3Client` | Uploads images to S3 (bucket from `settings.AWS_S3_BUCKET` — config-ized, MOD-02) | `pushframe/aws/s3client.py` |
| `SQSClient` | Polls per-frame SQS queues for upload confirmation signals | `pushframe/aws/sqsclient.py` |
| `ExifWriter` | Writes EXIF metadata (datetime, GPS) back into downloaded images | `pushframe/exif.py` |
| `export` | Downloads images from the Aura image proxy, handles EXIF injection and local caching | `pushframe/export.py` |
| `cache` | File-based JSON caching decorators (`@cache`, `@async_cache`) | `pushframe/cache.py` |
| `settings` | Call-time configuration resolution (`PUSHFRAME_*` primary, `AURA_*` legacy) | `pushframe/utils/settings.py` |
## Pattern Overview
- `Aura` is the single public entry point; callers never instantiate API classes directly
- Each `*Api` class is a thin wrapper over one REST resource domain with zero business logic of its own
- Pydantic models serve as both validated DTOs and serialization targets (`.model_dump()` for request payloads, `**json_response` for hydration)
- AWS auth uses Cognito anonymous identity pools — no long-lived AWS credentials needed
- All HTTP is synchronous (`httpx.Client`); the codebase has acknowledged debt to migrate to async
## Layers
- Purpose: The packaged user surface — argument parsing, dispatch, scheduled-run bookkeeping
- Location: `pushframe/cli.py` (console script `pushframe`)
- Contains: One parser per verb, `_main` dispatch, status/schedule/report rendering
- Depends on: `pushframe/session.py`, `config_store`, `schedule`, `gsync`, `sync`, `aura.py`
- Used by: End users and systemd user units
- Purpose: Composes all API and AWS clients; provides workflow methods (dump, clone, upload)
- Location: `pushframe/aura.py`
- Contains: `Aura` class
- Depends on: All `*Api` classes, `Client`, `S3Client`, `SQSClient`, `ExifWriter`, `export`
- Used by: `pushframe/cli.py`, external callers
- Purpose: One class per REST resource; maps Python method calls to HTTP calls and hydrates Pydantic models
- Location: `pushframe/api/`
- Contains: `AccountApi`, `FrameApi`, `AssetApi`, `ActivityApi`, `PeopleApi`, `PlaylistApi`, `NotificationApi`
- Depends on: `Client` (injected), `models/`
- Used by: `Aura`
- Purpose: Manages a persistent `httpx.Client` session with HTTP/2, shared headers and cookies
- Location: `pushframe/client.py`
- Contains: `Client` class with `get`, `post`, `put`, `delete` methods
- Depends on: `httpx`
- Used by: All `*Api` classes via `BaseApi`
- Purpose: Pydantic data classes representing API response shapes
- Location: `pushframe/models/`
- Contains: `User`, `Frame`, `FramePartial`, `Asset`, `AssetPartialId`, `Activity`, `Person`, `Reaction`, `Comment`
- Depends on: `pydantic`, `pushframe/utils/dt.py`
- Used by: All API classes, `Aura`, `export.py`, `exif.py`
- Purpose: Provides Cognito-authenticated S3 and SQS clients
- Location: `pushframe/aws/`
- Contains: `AWSClient`, `S3Client`, `SQSClient`
- Depends on: `boto3`, `botocore`
- Used by: `Aura`
- Purpose: Downloads images from the image proxy and injects EXIF metadata (dates, GPS)
- Location: `pushframe/export.py`, `pushframe/exif.py`
- Contains: `get_image_from_asset()`, `ExifWriter`
- Depends on: `httpx`, `piexif`, `geopy`, `Pillow`, `models/`
- Used by: `Aura.dump_frame()`, `Aura.download_images_from_assets()`
- Purpose: Shared helpers with no business logic
- Location: `pushframe/utils/`
- Contains: `settings.py` (env vars), `dt.py` (datetime formatting), `io.py` (path building, Pydantic JSON serialisation)
- Depends on: stdlib only
- Used by: All layers
## Data Flow
### Authentication Flow
### Frame Dump / Download Flow
### Image Upload Flow
- Auth state held inside `Client.http2_client.headers` and `Client.http2_client.cookies` (session-level mutation after login)
- No in-process cache is used at runtime; `cache.py` provides opt-in file-based JSON cache decorators
## Key Abstractions
- Purpose: Single public interface; hides multi-step orchestration from callers
- Examples: `pushframe/aura.py`
- Pattern: Facade — aggregates `AccountApi`, `FrameApi`, `AssetApi`, `ActivityApi`, `PeopleApi`, `S3Client`, `SQSClient`, `ExifWriter`
- Purpose: Receives the shared `Client` instance; all resource APIs extend it
- Examples: `pushframe/api/baseApi.py` — extended by all 7 API classes
- Pattern: Constructor injection of `Client`; no interface/protocol defined
- Purpose: Validate, hydrate, and serialise API response JSON
- Examples: `pushframe/models/frame.py`, `pushframe/models/asset.py`
- Pattern: `Model(**json_response.get('key'))` for hydration; `.model_dump(include={...})` for request payloads
- Purpose: Generates a "partial" variant of any model (all fields become `Optional`) for PATCH-style updates
- Examples: `pushframe/models/meta.py` — used by `FramePartial` in `pushframe/models/frame.py:105`
- Pattern: `FramePartial = make_partial(Frame, "FramePartial")` (pydantic v2 `create_model`)
- Purpose: Fetches temporary AWS credentials from Cognito identity pools
- Examples: `pushframe/aws/awsclient.py` — extended by `S3Client` and `SQSClient`
- Pattern: Inheritance; each subclass calls `super().auth(pool_id)` then constructs its own boto3 client
## Entry Points
- Location: `pushframe/cli.py` — the `pushframe` console script
- Triggers: `pushframe <verb> ...` (verbs: status, inspect, sync, push, google-link, google-album, google-sync, config, pair, schedule, logout)
- Responsibilities: Parses and dispatches every verb; scheduled runs carry `--scheduled`/`--report-tag` bookkeeping
- Location: `main.py` (legacy demo)
- Triggers: Direct Python execution (`python main.py`)
- Responsibilities: Instantiates `Aura`, calls `login()`, demonstrates API usage
- Location: `pushframe/aura.py`
- Triggers: Instantiation by caller
- Responsibilities: Constructor wires all dependencies; `login()` must be called before any other method
## Architectural Constraints
- **Threading:** Single-threaded, synchronous throughout. `client.py:174` has a noted TODO to make it async. `SQSClient` long-polls which blocks the calling thread. (Concurrent downloads exist only on the Google side of `gsync`.)
- **Global state:** `settings.py` resolves env/config values **at call time** (`SETTINGS` registry; `PUSHFRAME_*` primary, `AURA_*` legacy) — never at import (the v1.0 HTTP-475 early-bound-default lesson). `S3Client.s3_client` is a class-level attribute (`None` until `auth()` is called).
- **Circular imports:** None detected. Models depend only on each other (e.g., `asset.py` imports `user.py`); API classes import models unidirectionally.
- **Auth ordering:** `Client` headers are mutated after `login()`. Calling any API method before `Aura.login()` sends unauthenticated requests with no error guard.
- **Pagination:** Cursor-based pagination is manual — callers (or `Aura.get_all_assets()`) must loop and handle `next_page_cursor`.
## Anti-Patterns
### Hardcoded AWS Pool IDs and Bucket Name — RESOLVED (MOD-02: settings-backed, `PUSHFRAME_AWS_*` env overrides)
### Unguarded Post-Login State — LARGELY RESOLVED (preflights + token sessions guard the CLI surface; the bare `Aura` facade still expects `login()` first)
### Silent Error Handling in API Responses — MOSTLY RESOLVED (typed exception hierarchy + fail-loud write endpoints; one inherited `pass` stub remains at `accountApi.py:60`)
## Error Handling
- `try/except Exception as e` with `logger.error(e)` used in `Aura.upload_image()` and `Aura.download_images_from_assets()` — failures are logged and assets are collected in a `failed_to_retrieve` list
- EXIF write failures fail loudly (D-11): `exif.py` logs and re-raises instead of returning an empty `BytesIO`
- Typed exception hierarchy exists (`AuraError` + subclasses in `client.py`; `SafeSyncError`, `ScheduleError`, `PreflightError`, `ConfigError` in their modules)
## Cross-Cutting Concerns
<!-- GSD:architecture-end -->

## Agent skills

### Issue tracker

Issues are tracked in GitHub Issues (`coredmp95/pushframe`) via the `gh` CLI. External PRs are **not** a triage surface. See `docs/agents/issue-tracker.md`.

### Triage labels

Default vocabulary: `needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: `CONTEXT.md` + `docs/adr/` at the repo root. See `docs/agents/domain.md`.

<!-- GSD:skills-start source:skills/ -->
## Project Skills

No project skills found. Add skills to any of: `.claude/skills/`, `.agents/skills/`, `.cursor/skills/`, `.github/skills/`, or `.codex/skills/` with a `SKILL.md` index file.
<!-- GSD:skills-end -->

<!-- GSD:workflow-start source:GSD defaults -->
## GSD Workflow Enforcement

Before using Edit, Write, or other file-changing tools, start work through a GSD command so planning artifacts and execution context stay in sync.

Use these entry points:
- `/gsd-quick` for small fixes, doc updates, and ad-hoc tasks
- `/gsd-debug` for investigation and bug fixing
- `/gsd-execute-phase` for planned phase work

Do not make direct repo edits outside a GSD workflow unless the user explicitly asks to bypass it.
<!-- GSD:workflow-end -->



<!-- GSD:profile-start -->
## Developer Profile

> Profile not yet configured. Run `/gsd-profile-user` to generate your developer profile.
> This section is managed by `generate-claude-profile` -- do not edit manually.
<!-- GSD:profile-end -->

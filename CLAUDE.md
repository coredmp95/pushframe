<!-- GSD:project-start source:PROJECT.md -->
## Project

**Aura Frames Python Client — Revive & Verify**

An unofficial, reverse-engineered Python client for the Aura Frames (Pushd) digital
photo-frame cloud API. It authenticates with an Aura *account* and pulls/pushes photos
through the cloud API (`api.pushd.com/v5`) plus AWS S3/SQS — it does not talk to the
frame over the local network. This milestone revives the ~3-year-old codebase so it runs
again on a current toolchain and verifies the core read flow still works against the live
service.

**Core Value:** Prove the existing client still works end-to-end (login → list → download) on a current
Python toolchain, so we know exactly what survives before building anything new.

### Constraints

- **Tech stack**: Python 3.14 with `uv` for env/dependency/interpreter management — user decision.
- **API**: Unofficial, reverse-engineered Aura/Pushd cloud API (`api.pushd.com/v5`) — undocumented and may change without notice; verification is inherently against a moving target.
- **Auth**: Requires live Aura account credentials (`AURA_EMAIL`/`AURA_PASSWORD`); secrets must stay out of version control.
- **Modernization scope**: Pragmatic — change only what's needed to run on 3.14 and prove the read path; avoid broad refactors.
<!-- GSD:project-end -->

<!-- GSD:stack-start source:codebase/STACK.md -->
## Technology Stack

## Languages
- Python 3 - All source code; confirmed Python 3.14.4 on development machine
## Runtime
- Python 3 (no version pin file detected; system Python used)
- pip with `requirements.txt`
- Lockfile: Not present (no `requirements.lock` or `poetry.lock`)
## Frameworks
- None - This is a standalone Python library/CLI tool; no web framework is used
- pydantic ~=1.10.4 - All API response models and domain objects use `BaseModel`; see `auraframes/models/`
- Not detected - No test files, pytest config, or test runner found
- None detected - No build tooling or Makefile present
## Key Dependencies
- httpx==0.23.1 - Primary HTTP client used in `auraframes/client.py`; HTTP/2 enabled via `httpx.Client(http2=True)`
- h2==4.1.0 - HTTP/2 protocol implementation required by httpx for `http2=True` mode
- requests==2.28.1 - Listed in `requirements.txt` but not observed in active source files; httpx is the actual client used
- boto3==1.26.38 - Used in `auraframes/aws/awsclient.py`, `auraframes/aws/s3client.py`, `auraframes/aws/sqsclient.py`
- botocore~=1.29.38 - AWS SDK core, used in `auraframes/aws/awsclient.py` for `botocore.config.Config`
- Pillow~=9.5.0 - Image reading and thumbnail generation in `auraframes/aura.py` and `auraframes/export.py`
- piexif~=1.1.3 - EXIF data reading and writing in `auraframes/exif.py`
- geopy~=2.3.0 - Reverse geocoding via Nominatim in `auraframes/exif.py`
- loguru~=0.6.0 - Structured logging throughout; configured in `auraframes/aura.py` via `_init_logger()`; writes to `logs/file_{time}.log` and stderr
- tqdm~=4.65.0 - Progress bars for batch download operations in `auraframes/aura.py`
## Configuration
- Configured exclusively via environment variables, read in `auraframes/utils/settings.py`
- Required vars: `AURA_EMAIL`, `AURA_PASSWORD`
- Optional vars with defaults: `AURA_LOCALE` (default: `en-US`), `AURA_APP_IDENTIFIER` (default: `com.pushd.client`), `AURA_DEVICE_IDENTIFIER` (default: `0000000000000000`)
- No `.env` file loader detected; vars must be set in shell environment
- No build config files present (`setup.py`, `pyproject.toml`, `Makefile` absent)
## Platform Requirements
- Python 3 with pip
- AWS credentials provisioned via Cognito Identity Pool (no static AWS keys required)
- Network access to `api.pushd.com`, `imgproxy.pushd.com`, AWS us-east-1 endpoints
- Not applicable; this is an offline/scripting tool intended for direct execution via `main.py`
- Entry point: `python main.py` (see `main.py`)
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
- None — all imports use full `auraframes.*` package paths
## Error Handling
- Minimal error handling throughout; many API error paths have `# TODO: Error handling` comments with a bare `pass` (see `accountApi.py:29`, `accountApi.py:57`)
- HTTP error responses checked via `json_response.get('error')` but not acted upon in most cases
- Broad `except:` clauses with no exception type used in `exif.py` — e.g., `except:` catches all exceptions silently, logging only via `logger.info`
- `try/except Exception as e` used in `aura.py:download_images_from_assets()` — failed assets are collected but not re-raised
- No custom exception classes defined anywhere in the codebase
- Pydantic validation is the primary mechanism for catching bad data from the API (model construction raises on invalid types)
- `AssetPartialId` uses a Pydantic `@validator` for cross-field validation: `asset.py:118`
## Logging
- Configured once in `Aura._init_logger()` (`aura.py:131`)
- Two sinks: `sys.stderr` at `INFO` level and `logs/file_{time}.log` (all levels)
- Structured format with timestamp, level, module/function/line, message, and `{extra}` context
- `logger.info(f'...')` for HTTP request logging (called with keyword args for extra context)
- `logger.debug(f'...')` for response bodies and cookies
- `logger.error(e)` for caught exceptions in `upload_image()`
- `loguru` structured binding via keyword args: `logger.info(f'GET request to {url}', query_params=..., headers=...)`
## Comments
- TODOs are pervasive (28+ instances) — used to mark unimplemented features, known issues, and open questions
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
- `auraframes/models/__init__.py` and `auraframes/api/__init__.py` are empty
- `auraframes/__init__.py` is empty — consumers must import specific submodules
- Not used; imports go directly to source modules: `from auraframes.api.frameApi import FrameApi`
## Data Modeling
- All API response shapes modeled as `pydantic.BaseModel` subclasses
- Fields typed with `Optional[T]` for nullable/missing API fields
- `Any` used when field type is unknown: `burst_id: Any`, `playlist: typing.Any`
- `from __future__ import annotations` enables forward references in models
- Partial model pattern via custom `AllOptional` metaclass (`meta.py`): `class FramePartial(Frame, metaclass=AllOptional)`
- Enums used for known string-valued discriminators: `ActivityType`, `ReactionType`, `Feature`
- Pydantic `@validator` used for cross-field validation: `AssetPartialId.check_id_or_local_id`
- `.model_dump(include={...})` used to build API request payloads from model instances
## Configuration
- Credentials passed via env vars: `AURA_EMAIL`, `AURA_PASSWORD`
- AWS pool IDs hardcoded as module constants (flagged as TODO to move to config)
<!-- GSD:conventions-end -->

<!-- GSD:architecture-start source:ARCHITECTURE.md -->
## Architecture

## System Overview
```text
```
## Component Responsibilities
| Component | Responsibility | File |
|-----------|----------------|------|
| `Aura` | Facade; composes all API clients; drives high-level workflows (dump, clone, upload) | `auraframes/aura.py` |
| `Client` | Synchronous HTTP client; manages auth headers, cookies, request history | `auraframes/client.py` |
| `BaseApi` | Holds `Client` reference via constructor injection; base class for all API modules | `auraframes/api/baseApi.py` |
| `AccountApi` | Login, register, delete account | `auraframes/api/accountApi.py` |
| `FrameApi` | Frame CRUD, asset association, asset listing, activity listing, playlist management | `auraframes/api/frameApi.py` |
| `AssetApi` | Asset metadata updates, crop, date overrides, delete | `auraframes/api/assetApi.py` |
| `ActivityApi` | Activity comments, asset retrieval, activity deletion | `auraframes/api/activityApi.py` |
| `PeopleApi` | People (face recognition) listing and asset queries | `auraframes/api/peopleApi.py` |
| `PlaylistApi` | Playlist asset retrieval | `auraframes/api/playlistApi.py` |
| `NotificationApi` | Notification settings | `auraframes/api/notificationApi.py` |
| `AWSClient` | Base AWS client; authenticates via Cognito identity pool (anonymous) | `auraframes/aws/awsclient.py` |
| `S3Client` | Uploads images to S3 bucket `images.senseapp.co` | `auraframes/aws/s3client.py` |
| `SQSClient` | Polls per-frame SQS queues for upload confirmation signals | `auraframes/aws/sqsclient.py` |
| `ExifWriter` | Writes EXIF metadata (datetime, GPS) back into downloaded images | `auraframes/exif.py` |
| `export` | Downloads images from the Aura image proxy, handles EXIF injection and local caching | `auraframes/export.py` |
| `cache` | File-based JSON caching decorators (`@cache`, `@async_cache`) | `auraframes/cache.py` |
| `settings` | Env-var based runtime configuration | `auraframes/utils/settings.py` |
## Pattern Overview
- `Aura` is the single public entry point; callers never instantiate API classes directly
- Each `*Api` class is a thin wrapper over one REST resource domain with zero business logic of its own
- Pydantic models serve as both validated DTOs and serialization targets (`.model_dump()` for request payloads, `**json_response` for hydration)
- AWS auth uses Cognito anonymous identity pools — no long-lived AWS credentials needed
- All HTTP is synchronous (`httpx.Client`); the codebase has acknowledged debt to migrate to async
## Layers
- Purpose: Demonstrates or exercises the client
- Location: `main.py`
- Contains: Minimal bootstrap code
- Depends on: `auraframes/aura.py`
- Used by: Developer directly
- Purpose: Composes all API and AWS clients; provides workflow methods (dump, clone, upload)
- Location: `auraframes/aura.py`
- Contains: `Aura` class
- Depends on: All `*Api` classes, `Client`, `S3Client`, `SQSClient`, `ExifWriter`, `export`
- Used by: `main.py`, external callers
- Purpose: One class per REST resource; maps Python method calls to HTTP calls and hydrates Pydantic models
- Location: `auraframes/api/`
- Contains: `AccountApi`, `FrameApi`, `AssetApi`, `ActivityApi`, `PeopleApi`, `PlaylistApi`, `NotificationApi`
- Depends on: `Client` (injected), `models/`
- Used by: `Aura`
- Purpose: Manages a persistent `httpx.Client` session with HTTP/2, shared headers and cookies
- Location: `auraframes/client.py`
- Contains: `Client` class with `get`, `post`, `put`, `delete` methods
- Depends on: `httpx`
- Used by: All `*Api` classes via `BaseApi`
- Purpose: Pydantic data classes representing API response shapes
- Location: `auraframes/models/`
- Contains: `User`, `Frame`, `FramePartial`, `Asset`, `AssetPartialId`, `Activity`, `Person`, `Reaction`, `Comment`
- Depends on: `pydantic`, `auraframes/utils/dt.py`
- Used by: All API classes, `Aura`, `export.py`, `exif.py`
- Purpose: Provides Cognito-authenticated S3 and SQS clients
- Location: `auraframes/aws/`
- Contains: `AWSClient`, `S3Client`, `SQSClient`
- Depends on: `boto3`, `botocore`
- Used by: `Aura`
- Purpose: Downloads images from the image proxy and injects EXIF metadata (dates, GPS)
- Location: `auraframes/export.py`, `auraframes/exif.py`
- Contains: `get_image_from_asset()`, `ExifWriter`
- Depends on: `httpx`, `piexif`, `geopy`, `Pillow`, `models/`
- Used by: `Aura.dump_frame()`, `Aura.download_images_from_assets()`
- Purpose: Shared helpers with no business logic
- Location: `auraframes/utils/`
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
- Examples: `auraframes/aura.py`
- Pattern: Facade — aggregates `AccountApi`, `FrameApi`, `AssetApi`, `ActivityApi`, `PeopleApi`, `S3Client`, `SQSClient`, `ExifWriter`
- Purpose: Receives the shared `Client` instance; all resource APIs extend it
- Examples: `auraframes/api/baseApi.py` — extended by all 7 API classes
- Pattern: Constructor injection of `Client`; no interface/protocol defined
- Purpose: Validate, hydrate, and serialise API response JSON
- Examples: `auraframes/models/frame.py`, `auraframes/models/asset.py`
- Pattern: `Model(**json_response.get('key'))` for hydration; `.model_dump(include={...})` for request payloads
- Purpose: Generates a "partial" variant of any model (all fields become `Optional`) for PATCH-style updates
- Examples: `auraframes/models/meta.py` — used by `FramePartial` in `auraframes/models/frame.py:93`
- Pattern: `class FramePartial(Frame, metaclass=AllOptional): pass`
- Purpose: Fetches temporary AWS credentials from Cognito identity pools
- Examples: `auraframes/aws/awsclient.py` — extended by `S3Client` and `SQSClient`
- Pattern: Inheritance; each subclass calls `super().auth(pool_id)` then constructs its own boto3 client
## Entry Points
- Location: `main.py`
- Triggers: Direct Python execution (`python main.py`)
- Responsibilities: Instantiates `Aura`, calls `login()`, demonstrates API usage
- Location: `auraframes/aura.py`
- Triggers: Instantiation by caller
- Responsibilities: Constructor wires all dependencies; `login()` must be called before any other method
## Architectural Constraints
- **Threading:** Single-threaded, synchronous throughout. `client.py:18` has a noted TODO to make it async. `SQSClient` long-polls which blocks the calling thread.
- **Global state:** `settings.py` reads env vars at module import time — values are module-level constants. `S3Client.s3_client` is a class-level attribute (`None` until `auth()` is called).
- **Circular imports:** None detected. Models depend only on each other (e.g., `asset.py` imports `user.py`); API classes import models unidirectionally.
- **Auth ordering:** `Client` headers are mutated after `login()`. Calling any API method before `Aura.login()` sends unauthenticated requests with no error guard.
- **Pagination:** Cursor-based pagination is manual — callers (or `Aura.get_all_assets()`) must loop and handle `next_page_cursor`.
## Anti-Patterns
### Hardcoded AWS Pool IDs and Bucket Name
### Unguarded Post-Login State
### Silent Error Handling in API Responses
## Error Handling
- `try/except Exception as e` with `logger.error(e)` used in `Aura.upload_image()` and `Aura.download_images_from_assets()` — failures are logged and assets are collected in a `failed_to_retrieve` list
- EXIF write failures silently return an empty `BytesIO` (`auraframes/exif.py:92`)
- No custom exception hierarchy exists
## Cross-Cutting Concerns
<!-- GSD:architecture-end -->

## Agent skills

### Issue tracker

Issues are tracked in GitHub Issues (`coredmp95/auraframes`) via the `gh` CLI. External PRs are **not** a triage surface. See `docs/agents/issue-tracker.md`.

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

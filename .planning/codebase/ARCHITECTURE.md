<!-- refreshed: 2026-06-29 -->
# Architecture

**Analysis Date:** 2026-06-29

## System Overview

```text
┌─────────────────────────────────────────────────────────────┐
│                        Entry Point                           │
│                       `main.py`                              │
└─────────────────────────┬───────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────────┐
│                    Facade / Orchestrator                      │
│                  `pushframe/aura.py`                        │
│  (Aura class — composes all API clients, drives workflows)   │
└──────┬──────────────────┬──────────────────┬────────────────┘
       │                  │                  │
       ▼                  ▼                  ▼
┌──────────────┐  ┌──────────────────┐  ┌──────────────────┐
│   API Layer  │  │   AWS Layer      │  │  Export / EXIF   │
│`pushframe/  │  │`pushframe/aws/` │  │`pushframe/      │
│   api/`      │  │                  │  │  export.py`      │
│              │  │ S3Client         │  │`pushframe/      │
│ AccountApi   │  │ SQSClient        │  │  exif.py`        │
│ FrameApi     │  │ (both extend     │  │                  │
│ AssetApi     │  │  AWSClient)      │  │                  │
│ ActivityApi  │  └─────────────────-┘  └──────────────────┘
│ PeopleApi    │          │
│ PlaylistApi  │          ▼
│ NotificationA│  ┌──────────────────┐
│ (all extend  │  │  AWS Cognito     │
│  BaseApi)    │  │  (anonymous auth)│
└──────┬───────┘  └──────────────────┘
       │
       ▼
┌─────────────────────────────────────────────────────────────┐
│                     HTTP Client                              │
│                  `pushframe/client.py`                      │
│      (httpx with HTTP/2, session-level auth headers)         │
└─────────────────────────────────────────────────────────────┘
       │
       ▼
┌─────────────────────────────────────────────────────────────┐
│                  Aura Frames REST API                        │
│          https://api.pushd.com/v5                            │
└─────────────────────────────────────────────────────────────┘

       │  (all layers use)
       ▼
┌─────────────────────────────────────────────────────────────┐
│                     Model Layer                              │
│                  `pushframe/models/`                        │
│     Pydantic BaseModel DTOs: Frame, Asset, User, etc.        │
└─────────────────────────────────────────────────────────────┘
```

## Component Responsibilities

| Component | Responsibility | File |
|-----------|----------------|------|
| `Aura` | Facade; composes all API clients; drives high-level workflows (dump, clone, upload) | `pushframe/aura.py` |
| `Client` | Synchronous HTTP client; manages auth headers, cookies, request history | `pushframe/client.py` |
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
| `settings` | Env-var based runtime configuration | `pushframe/utils/settings.py` |

## Pattern Overview

**Overall:** Facade + Thin API Wrapper (resource-oriented)

**Key Characteristics:**
- `Aura` is the single public entry point; callers never instantiate API classes directly
- Each `*Api` class is a thin wrapper over one REST resource domain with zero business logic of its own
- Pydantic models serve as both validated DTOs and serialization targets (`.model_dump()` for request payloads, `**json_response` for hydration)
- AWS auth uses Cognito anonymous identity pools — no long-lived AWS credentials needed
- All HTTP is synchronous (`httpx.Client`); the codebase has acknowledged debt to migrate to async

## Layers

**CLI Entry Point:**
- Purpose: The packaged user surface — argument parsing, dispatch, scheduled-run bookkeeping
- Location: `pushframe/cli.py` (console script `pushframe`)
- Contains: One parser per verb, `_main` dispatch, status/schedule/report rendering
- Depends on: `pushframe/session.py`, `config_store`, `schedule`, `gsync`, `sync`, `aura.py`
- Used by: End users and systemd user units

**Facade / Orchestrator:**
- Purpose: Composes all API and AWS clients; provides workflow methods (dump, clone, upload)
- Location: `pushframe/aura.py`
- Contains: `Aura` class
- Depends on: All `*Api` classes, `Client`, `S3Client`, `SQSClient`, `ExifWriter`, `export`
- Used by: `pushframe/cli.py`, external callers

**API Layer:**
- Purpose: One class per REST resource; maps Python method calls to HTTP calls and hydrates Pydantic models
- Location: `pushframe/api/`
- Contains: `AccountApi`, `FrameApi`, `AssetApi`, `ActivityApi`, `PeopleApi`, `PlaylistApi`, `NotificationApi`
- Depends on: `Client` (injected), `models/`
- Used by: `Aura`

**HTTP Client:**
- Purpose: Manages a persistent `httpx.Client` session with HTTP/2, shared headers and cookies
- Location: `pushframe/client.py`
- Contains: `Client` class with `get`, `post`, `put`, `delete` methods
- Depends on: `httpx`
- Used by: All `*Api` classes via `BaseApi`

**Model Layer:**
- Purpose: Pydantic data classes representing API response shapes
- Location: `pushframe/models/`
- Contains: `User`, `Frame`, `FramePartial`, `Asset`, `AssetPartialId`, `Activity`, `Person`, `Reaction`, `Comment`
- Depends on: `pydantic`, `pushframe/utils/dt.py`
- Used by: All API classes, `Aura`, `export.py`, `exif.py`

**AWS Layer:**
- Purpose: Provides Cognito-authenticated S3 and SQS clients
- Location: `pushframe/aws/`
- Contains: `AWSClient`, `S3Client`, `SQSClient`
- Depends on: `boto3`, `botocore`
- Used by: `Aura`

**Export / EXIF Layer:**
- Purpose: Downloads images from the image proxy and injects EXIF metadata (dates, GPS)
- Location: `pushframe/export.py`, `pushframe/exif.py`
- Contains: `get_image_from_asset()`, `ExifWriter`
- Depends on: `httpx`, `piexif`, `geopy`, `Pillow`, `models/`
- Used by: `Aura.dump_frame()`, `Aura.download_images_from_assets()`

**Utilities:**
- Purpose: Shared helpers with no business logic
- Location: `pushframe/utils/`
- Contains: `settings.py` (env vars), `dt.py` (datetime formatting), `io.py` (path building, Pydantic JSON serialisation)
- Depends on: stdlib only
- Used by: All layers

## Data Flow

### Authentication Flow

1. `main.py` creates `Aura()` — `Client` is instantiated with base URL `https://api.pushd.com/v5` (`pushframe/client.py:24`)
2. `Aura.login()` delegates to `AccountApi.login()` (`pushframe/api/accountApi.py:8`)
3. `AccountApi.login()` calls `Client.post('/login.json', ...)` with email/password payload
4. Response is hydrated into `User` model; `auth_token` and `id` are extracted
5. `Client.add_default_headers({'x-token-auth': ..., 'x-user-id': ...})` attaches auth to all subsequent requests (`pushframe/aura.py:45`)

### Frame Dump / Download Flow

1. `Aura.dump_frame(frame_id, path)` (`pushframe/aura.py:65`)
2. `FrameApi.get_frame(frame_id)` → `Client.get('/frames/{id}.json')` → `Frame` model
3. `FrameApi.get_assets(frame_id)` (paginated, cursor-based) → list of `Asset` models
4. `write_model(assets, path)` serialises models to JSON via `pydantic_encoder` (`pushframe/utils/io.py`)
5. For each asset: `export.get_image_from_asset(asset, path, exif_writer)` (`pushframe/export.py:41`)
6. Image bytes fetched from `https://imgproxy.pushd.com/{user_id}/{file_name}`
7. `ExifWriter.write_exif(image, asset)` injects EXIF datetime + GPS coordinates (`pushframe/exif.py:58`)
8. Image written to disk under `{frame.name}-{frame.id}/asset_images/`

### Image Upload Flow

1. `Aura.upload_image(frame_id, image_path, asset)` (`pushframe/aura.py:101`)
2. `FrameApi.select_asset(frame_id, AssetPartialId)` — associates `local_identifier` to frame (`pushframe/api/frameApi.py:93`)
3. `SQSClient.receive_message(queue_url)` — polls frame's SQS queue (`pushframe/aws/sqsclient.py:26`)
4. `FrameApi.select_asset()` called a second time (mirrors device behaviour)
5. `S3Client.upload_file(image_bytes, '.jpg')` → `put_object` to the settings-backed bucket (`bucket_key()` → `settings.AWS_S3_BUCKET`)
6. `Asset` fields updated with S3 filename and MD5
7. `AssetApi.batch_update(asset)` → `PUT /assets/batch_update.json` (`pushframe/api/assetApi.py:9`)
8. `SQSClient.receive_message()` polled again

**State Management:**
- Auth state held inside `Client.http2_client.headers` and `Client.http2_client.cookies` (session-level mutation after login)
- No in-process cache is used at runtime; `cache.py` provides opt-in file-based JSON cache decorators

## Key Abstractions

**`Aura` (Facade):**
- Purpose: Single public interface; hides multi-step orchestration from callers
- Examples: `pushframe/aura.py`
- Pattern: Facade — aggregates `AccountApi`, `FrameApi`, `AssetApi`, `ActivityApi`, `PeopleApi`, `S3Client`, `SQSClient`, `ExifWriter`

**`BaseApi` (Base class with DI):**
- Purpose: Receives the shared `Client` instance; all resource APIs extend it
- Examples: `pushframe/api/baseApi.py` — extended by all 7 API classes
- Pattern: Constructor injection of `Client`; no interface/protocol defined

**Pydantic Models (DTOs):**
- Purpose: Validate, hydrate, and serialise API response JSON
- Examples: `pushframe/models/frame.py`, `pushframe/models/asset.py`
- Pattern: `Model(**json_response.get('key'))` for hydration; `.model_dump(include={...})` for request payloads

**`make_partial()` factory:**
- Purpose: Generates a "partial" variant of any model (all fields become `Optional`) for PATCH-style updates
- Examples: `pushframe/models/meta.py` — used by `FramePartial` in `pushframe/models/frame.py:105`
- Pattern: `FramePartial = make_partial(Frame, "FramePartial")` (pydantic v2 `create_model`)

**`AWSClient` (Base AWS auth):**
- Purpose: Fetches temporary AWS credentials from Cognito identity pools
- Examples: `pushframe/aws/awsclient.py` — extended by `S3Client` and `SQSClient`
- Pattern: Inheritance; each subclass calls `super().auth(pool_id)` then constructs its own boto3 client

## Entry Points

**`main.py`:**
- Location: `main.py`
- Triggers: Direct Python execution (`python main.py`)
- Responsibilities: Instantiates `Aura`, calls `login()`, demonstrates API usage

**`Aura` class:**
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

### Hardcoded AWS Pool IDs and Bucket Name — RESOLVED (MOD-02, v4.0 Phase 19)

**Resolution:** `settings.AWS_S3_BUCKET`, `AWS_UPLOAD_IDENTITY_POOL_ID`, `AWS_SQS_IDENTITY_POOL_ID` live in `pushframe/utils/settings.py` with `PUSHFRAME_AWS_*`/`AURA_AWS_*` env overrides; `s3client.bucket_key()` reads the bucket. No string literals remain.

### Unguarded Post-Login State — LARGELY RESOLVED (v5.1 preflights + token sessions)

**Resolution:** The CLI surface resolves sessions through `pushframe/session.py` (stored token or explicit login) and `pushframe/preflight.py` guards machine prerequisites before verbs run. The bare `Aura` facade itself still expects `login()` before use — by design, it is now an internal layer.

### Silent Error Handling in API Responses — MOSTLY RESOLVED (typed exceptions + fail-loud writes)

**Resolution:** `pushframe/client.py` defines `AuraError` with `AuthenticationError` / `WriteEndpointError` / `RateLimitError`; all write/delete endpoints raise on API errors, and the CLI maps foreseeable failures to named errors with remedies (PRF-02 traceback-free contract, test-swept). One inherited `pass` stub remains at `pushframe/api/accountApi.py:60`.

## Error Handling

**Strategy:** Minimal — most errors result in exceptions propagating naturally from httpx or Pydantic. Explicit error checks exist but are stubbed with `pass`.

**Patterns:**
- `try/except Exception as e` with `logger.error(e)` used in `Aura.upload_image()` and `Aura.download_images_from_assets()` — failures are logged and assets are collected in a `failed_to_retrieve` list
- EXIF write failures fail loudly (D-11): `exif.py` logs and re-raises instead of returning an empty `BytesIO`
- Typed exception hierarchy exists (`AuraError` + subclasses in `client.py`; `SafeSyncError`, `ScheduleError`, `PreflightError`, `ConfigError` in their modules)

## Cross-Cutting Concerns

**Logging:** `loguru` (`pushframe/aura.py:_init_logger`, `aura.py:187`). INFO level to stderr with structured context. DEBUG level to rotating file at `logs/file_{time}.log`. Configured exactly once per process under the MOD-04 `_LOGGER_READY` guard; `cli.py`'s `_configure_cli_logging()` owns wholesale reconfiguration.
**Validation:** Pydantic v2 model validation on all API response hydration. No request-side validation beyond type hints.
**Authentication:** Token-based (`x-token-auth` header + `x-user-id`). Tokens obtained via `/login.json` and stored as `Client` default headers. AWS resources use Cognito anonymous identity.

---

*Architecture analysis: 2026-06-29, corrected 2026-10-04 against the 5.1.28 tree.*

# Codebase Structure

**Analysis Date:** 2026-06-29

## Directory Layout

```
pushframe/                    # Project root
├── main.py                    # Script entry point — demonstrates client usage
├── requirements.txt           # Pinned Python dependencies
├── README.md                  # Project overview, upload/download flow docs
├── .gitignore
├── .planning/
│   └── codebase/              # Architecture analysis documents
└── pushframe/                # Main Python package
    ├── __init__.py
    ├── aura.py                # Facade class (Aura) — primary public API
    ├── client.py              # HTTP client (httpx/HTTP2 wrapper)
    ├── cache.py               # File-based JSON cache decorators
    ├── exif.py                # EXIF metadata writing (piexif + geopy)
    ├── export.py              # Image download + EXIF injection utilities
    ├── api/                   # Resource-specific REST API wrappers
    │   ├── __init__.py
    │   ├── baseApi.py         # BaseApi — injects Client into all subclasses
    │   ├── accountApi.py      # Login, register, delete account
    │   ├── activityApi.py     # Activity comments and assets
    │   ├── assetApi.py        # Asset CRUD and metadata updates
    │   ├── frameApi.py        # Frame CRUD, asset/playlist management
    │   ├── notificationApi.py # Notification settings (stub)
    │   ├── peopleApi.py       # People/face recognition queries
    │   └── playlistApi.py     # Playlist asset retrieval
    ├── aws/                   # AWS integration (boto3)
    │   ├── __init__.py
    │   ├── awsclient.py       # Base AWS client — Cognito anonymous auth
    │   ├── s3client.py        # S3 image upload/download
    │   └── sqsclient.py       # SQS queue polling
    ├── models/                # Pydantic data models (DTOs)
    │   ├── __init__.py
    │   ├── meta.py            # AllOptional metaclass for partial models
    │   ├── user.py            # User model
    │   ├── frame.py           # Frame, FramePartial models
    │   ├── asset.py           # Asset, AssetPartialId, AssetSetting, AssetPadding
    │   ├── activity.py        # Activity, Reaction, Comment, ActivityType enums
    │   └── person.py          # Person, PersonAssetSetting models
    └── utils/                 # Shared utility modules (no business logic)
        ├── dt.py              # Datetime parsing/formatting for Aura's ISO format
        ├── io.py              # build_path(), write_model() (Pydantic → JSON file)
        └── settings.py        # Env-var constants (LOCALE, DEVICE_IDENTIFIER, etc.)
```

## Directory Purposes

**`pushframe/api/`:**
- Purpose: One class per REST resource domain; each class maps Python methods to HTTP calls and hydrates Pydantic models
- Contains: `*Api` classes, all extending `BaseApi`
- Key files: `frameApi.py` (largest; covers assets, playlists, activities, frame control)

**`pushframe/aws/`:**
- Purpose: AWS service clients authenticated via Cognito anonymous identity pools
- Contains: `AWSClient` (base), `S3Client` (images), `SQSClient` (frame event queues)
- Key files: `s3client.py` — hardcodes bucket `images.senseapp.co` and pool ID

**`pushframe/models/`:**
- Purpose: Pydantic BaseModel definitions matching the Aura REST API response schema
- Contains: Domain models for all major resources
- Key files: `asset.py` (most complex model, ~130 fields), `frame.py` (includes `FramePartial` using `AllOptional`)

**`pushframe/utils/`:**
- Purpose: Stateless helper functions; no imports from other `pushframe` subpackages
- Contains: `settings.py`, `dt.py`, `io.py`

## Key File Locations

**Entry Points:**
- `main.py`: Script-level entry; run with `python main.py`
- `pushframe/aura.py`: `Aura` class — start here for any programmatic use

**Configuration:**
- `pushframe/utils/settings.py`: All env-var-based settings (`AURA_LOCALE`, `AURA_APP_IDENTIFIER`, `AURA_DEVICE_IDENTIFIER`, `IMAGE_PROXY_BASE_URL`)
- `requirements.txt`: Python dependency pinning

**Core Logic:**
- `pushframe/aura.py`: High-level workflows (dump_frame, upload_image, clone)
- `pushframe/client.py`: HTTP session management; auth header injection
- `pushframe/export.py`: Image download and local file caching logic
- `pushframe/exif.py`: EXIF write pipeline (datetime + GPS lookup via geopy)

**AWS Integration:**
- `pushframe/aws/awsclient.py`: Cognito credential exchange
- `pushframe/aws/s3client.py`: Image upload (`put_object`) to S3
- `pushframe/aws/sqsclient.py`: SQS long-polling for upload confirmation

**Testing:**
- Not present — no test files detected

## Naming Conventions

**Files:**
- API classes: `{resource}Api.py` in camelCase (e.g., `frameApi.py`, `assetApi.py`)
- AWS clients: `{service}client.py` in lowercase (e.g., `s3client.py`, `sqsclient.py`)
- Utility modules: lowercase single word (e.g., `dt.py`, `io.py`, `cache.py`, `exif.py`)
- Models: lowercase resource name (e.g., `frame.py`, `asset.py`, `user.py`)

**Classes:**
- API classes: PascalCase with `Api` suffix (e.g., `FrameApi`, `AccountApi`)
- AWS clients: PascalCase with `Client` suffix (e.g., `S3Client`, `AWSClient`)
- Models: PascalCase noun (e.g., `Frame`, `Asset`, `User`, `FramePartial`)
- Enums: PascalCase (e.g., `ActivityType`, `ReactionType`, `Feature`)

**Methods:**
- snake_case throughout (e.g., `get_frames`, `batch_update`, `upload_file`)
- API methods named after HTTP semantics or REST conventions: `get_*`, `create_*`, `update_*`, `delete_*`, `remove_*`

**Constants:**
- SCREAMING_SNAKE_CASE (e.g., `AURA_API_BASE_URL`, `BUCKET_KEY`, `UPLOAD_IDENTITY_POOL_ID`)

## Where to Add New Code

**New API resource (e.g., `GiftApi`):**
- Implementation: `pushframe/api/giftApi.py` — extend `BaseApi`, inject `Client` via `super().__init__(client)`
- Register in facade: Add `self.gift_api = GiftApi(self._client)` in `Aura.__init__` (`pushframe/aura.py`)
- Models: Add `pushframe/models/gift.py` with Pydantic `BaseModel` subclass

**New model:**
- Implementation: `pushframe/models/{resource}.py`
- Use `Optional[T]` for nullable fields; use `parse_aura_dt()` from `pushframe/utils/dt.py` for datetime properties
- For PATCH/partial models: define base model then `class {Name}Partial({Name}, metaclass=AllOptional): pass`

**New AWS service integration:**
- Implementation: `pushframe/aws/{service}client.py` — extend `AWSClient`, override `auth()` to build the boto3 service client after calling `super().auth(pool_id)`

**New utility:**
- Shared stateless helpers: `pushframe/utils/{name}.py`
- No imports from `pushframe.api` or `pushframe.aws` in utils (keep utils dependency-free)

**New workflow / orchestration:**
- Add a method on the `Aura` class in `pushframe/aura.py`; do not add business logic to `*Api` classes

**Configuration / settings:**
- Add new env vars to `pushframe/utils/settings.py` using `os.getenv('VAR_NAME', 'default')`

## Special Directories

**`.planning/codebase/`:**
- Purpose: Architecture analysis documents for AI-assisted planning
- Generated: Yes (by GSD mapper)
- Committed: Yes

**`logs/` (runtime-generated):**
- Purpose: Rotating loguru log files (`logs/file_{time}.log`)
- Generated: Yes, at runtime by `Aura._init_logger()`
- Committed: No (should be in `.gitignore`)

**`cache/` (runtime-generated):**
- Purpose: File-based JSON cache for API responses (opt-in via `@cache` decorator in `pushframe/cache.py`)
- Generated: Yes, at runtime when `@cache`-decorated functions are called
- Committed: No

---

*Structure analysis: 2026-06-29*

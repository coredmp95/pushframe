# External Integrations

**Analysis Date:** 2026-06-29

## APIs & External Services

**Aura Frames REST API (primary target):**
- Service: Aura Frames / PUSHD backend (`api.pushd.com`)
  - Base URL: `https://api.pushd.com/v5`
  - SDK/Client: Custom `Client` class in `pushframe/client.py` using httpx with HTTP/2
  - Auth: Token-based — after login, `x-token-auth` and `x-user-id` headers are injected into all requests via `pushframe/aura.py:Aura.login()`
  - User-Agent spoofed as: `Aura/4.7.790 (Android 30; Client)`
  - Note: This is an unofficial reverse-engineered client; no official SDK exists

**API Endpoints used:**

| Method | Endpoint | Purpose | File |
|--------|----------|---------|------|
| POST | `/login.json` | Authenticate user | `pushframe/api/accountApi.py` |
| POST | `/account/register.json` | Register new account | `pushframe/api/accountApi.py` |
| DELETE | `/account/delete` | Delete account | `pushframe/api/accountApi.py` |
| GET | `/frames.json` | List all frames | `pushframe/api/frameApi.py` |
| GET | `/frames/{id}.json` | Get single frame | `pushframe/api/frameApi.py` |
| GET | `/frames/{id}/assets.json` | Paginated asset list | `pushframe/api/frameApi.py` |
| GET | `/frames/{id}/activities.json` | Frame activity log | `pushframe/api/frameApi.py` |
| POST | `/frames/{id}/goto.json` | Force frame to display asset | `pushframe/api/frameApi.py` |
| PUT | `/frames/{id}.json` | Update frame metadata | `pushframe/api/frameApi.py` |
| POST | `/frames/{id}/select_asset.json` | Associate asset to frame pre-upload | `pushframe/api/frameApi.py` |
| POST | `/frames/{id}/exclude_asset` | Hide asset from slideshow | `pushframe/api/frameApi.py` |
| POST | `/frames/{id}/remove_asset.json` | Disassociate asset from frame | `pushframe/api/frameApi.py` |
| POST | `/frames/{id}/reconfigure.json` | Unknown reconfigure action | `pushframe/api/frameApi.py` |
| PUT | `/assets/batch_update.json` | Upload asset metadata post-S3 | `pushframe/api/assetApi.py` |
| GET | `/assets/asset_for_local_identifier.json` | Fetch asset by local id | `pushframe/api/assetApi.py` |
| POST | `/assets/update_taken_at_date.json` | Update asset capture date | `pushframe/api/assetApi.py` |
| POST | `/assets/crop.json` | Update asset crop/rotation | `pushframe/api/assetApi.py` |
| DELETE | `/assets/{id}.json` | Delete asset | `pushframe/api/assetApi.py` |
| GET | `/people.json` | List recognized people | `pushframe/api/peopleApi.py` |
| GET | `/people/all_assets.json` | Assets grouped by person | `pushframe/api/peopleApi.py` |

**Aura Image Proxy:**
- Service: `imgproxy.pushd.com`
  - Base URL: `https://imgproxy.pushd.com` (configured in `pushframe/utils/settings.py:IMAGE_PROXY_BASE_URL`)
  - Usage: Full image download — URL pattern `{IMAGE_PROXY_BASE_URL}/{asset.user_id}/{asset.file_name}`
  - Client: Direct `httpx.get()` call in `pushframe/export.py`
  - Auth: None detected (public proxy URLs)

**Nominatim / OpenStreetMap Geocoding:**
- Service: Nominatim (via geopy)
  - SDK/Client: `geopy.Nominatim` in `pushframe/exif.py`
  - Usage: Converts location names from asset metadata to GPS coordinates for EXIF embedding
  - User-Agent: `"Upload Scripting Test"` (hardcoded in `pushframe/exif.py:ExifWriter`)
  - Auth: None (public service)
  - Note: Simple dict-based in-memory cache prevents repeated geocode lookups

## Data Storage

**Databases:**
- None - No database is used; this is a stateless scripting tool

**File Storage (AWS S3):**
- Provider: AWS S3 via boto3
  - Implementation: `pushframe/aws/s3client.py`
  - Bucket: `images.senseapp.co` (hardcoded constant `BUCKET_KEY`)
  - Region: `us-east-1`
  - Auth: Temporary credentials via AWS Cognito Identity Pool (see Authentication section)
  - Upload Identity Pool ID: `us-east-1:b92826c0-8274-43db-abff-136977c13598` (hardcoded in `pushframe/aws/s3client.py`)
  - Operations: `put_object` (upload), `head_object` (verify upload)
  - File naming: UUID v4 + extension (e.g. `{uuid}.jpg`)

**Local File Cache:**
- Simple filesystem cache in `cache/` directory, managed by `pushframe/cache.py`
- JSON responses are persisted to disk with filename-based keying
- Not used for images — only for JSON API responses
- No TTL/expiry mechanism

**Log Files:**
- Written to `logs/file_{time}.log` (configured in `pushframe/aura.py:_init_logger()`)
- Format: loguru structured logging

## Authentication & Identity

**Aura Frames Auth:**
- Flow: Email + password POST to `/login.json`; response contains `auth_token` and `user.id`
- Token storage: In-memory only, held in `Client.http2_client.headers` after `Aura.login()`
- Credentials source: `AURA_EMAIL` and `AURA_PASSWORD` environment variables (read in `pushframe/aura.py:Aura.login()`)
- Token headers: `x-token-auth` (the auth token) and `x-user-id` (user ID)

**AWS Authentication (Cognito Identity):**
- Provider: AWS Cognito Identity (unauthenticated identity pools)
  - Implementation: `pushframe/aws/awsclient.py:AWSClient`
  - Flow: `cognito-identity.get_id()` → `cognito-identity.get_credentials_for_identity()` → temporary STS credentials
  - Credentials: `AccessKeyId`, `SecretKey`, `SessionToken` (temporary, scoped)
  - Two separate identity pools:
    - S3 uploads: `us-east-1:b92826c0-8274-43db-abff-136977c13598` (`pushframe/aws/s3client.py`)
    - SQS polling: `us-east-1:98ccd0ff-69fe-4e9a-ad34-671b4381ab12` (`pushframe/aws/sqsclient.py`)
  - User-Agent spoofed as Android SDK: `aws-sdk-android/2.13.1 Linux/5.4.61-android11 Dalvik/2.1.0/0 en_US`

## Monitoring & Observability

**Error Tracking:**
- None - No external error tracking service (Sentry, etc.)

**Logs:**
- loguru to stderr (INFO level) and rotating file at `logs/file_{time}.log`
- Format includes timestamp, log level, module/function/line, message, and `extra` context dict

## CI/CD & Deployment

**Hosting:**
- Not applicable — local scripting tool

**CI Pipeline:**
- None detected

## Environment Configuration

**Required env vars:**
- `AURA_EMAIL` - Aura Frames account email for login
- `AURA_PASSWORD` - Aura Frames account password for login

**Optional env vars (with defaults):**
- `AURA_LOCALE` - Device locale sent in API requests (default: `en-US`)
- `AURA_APP_IDENTIFIER` - App bundle ID sent in API requests (default: `com.pushd.client`)
- `AURA_DEVICE_IDENTIFIER` - Device identifier sent in API requests (default: `0000000000000000`)

**Secrets location:**
- Environment variables only; no `.env` file loader present

## Webhooks & Callbacks

**Incoming:**
- None

**Outgoing:**
- None

## AWS SQS Integration

**Service:** AWS SQS (`pushframe/aws/sqsclient.py`)
- Region: `us-east-1`
- Auth: Cognito Identity Pool `us-east-1:98ccd0ff-69fe-4e9a-ad34-671b4381ab12`
- Queue naming convention: `frame-{frame_id}-client`
- Hardcoded queue ID in `pushframe/aura.py:get_sqs()`: `4ab446b4-33a7-4a76-881d-d545d153ab5a`
- Purpose: Polling for push-style frame update notifications during image upload flow
- Operation: Long-poll `receive_message` with configurable `wait_time_seconds`
- Note: README indicates SQS polling behavior is not fully understood; the queue result may not be used meaningfully

---

*Integration audit: 2026-06-29*

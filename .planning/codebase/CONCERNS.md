# Codebase Concerns

**Analysis Date:** 2026-06-29

## Tech Debt

**Synchronous HTTP client — no async support:**
- Issue: `pushframe/client.py` uses `httpx.Client` (synchronous). Mass upload/clone operations must execute sequentially, which is extremely slow. A TODO comment on line 19 acknowledges this.
- Files: `pushframe/client.py`, `pushframe/aura.py`
- Impact: `dump_frame`, `get_all_assets`, and future `upload_images` operations block the process for the entire duration. For a frame with hundreds of assets this is severe.
- Fix approach: Migrate to `httpx.AsyncClient` and `async/await` throughout all API classes. `pushframe/cache.py` already has an `async_cache` decorator prepared for this migration.

**Pydantic v1 API locked in at ~1.10.4:**
- Issue: `requirements.txt` pins `pydantic~=1.10.4`. The code uses v1-only APIs: `pydantic.json.pydantic_encoder` (removed in v2), `.dict()` method (deprecated in v2 in favor of `.model_dump()`), `@validator` (deprecated in v2 in favor of `@field_validator`), and `pydantic.main.ModelMetaclass` (internal v1 API used in `AllOptional`).
- Files: `pushframe/utils/io.py:6`, `pushframe/models/asset.py:118`, `pushframe/models/meta.py:6`, `pushframe/api/frameApi.py:90`
- Impact: Upgrading to Pydantic v2 (which is the current major version) requires changes across nearly every model file.
- Fix approach: Migrate models to Pydantic v2 APIs — replace `@validator` with `@field_validator`, `.dict()` with `.model_dump()`, `pydantic_encoder` with `model.model_dump()` + `json.dumps`, and rewrite `AllOptional` metaclass using `model_config` or a class decorator.

**`requirements.txt` appears UTF-16 encoded:**
- Issue: `requirements.txt` contains wide-character spacing between every character, indicating the file may have been saved as UTF-16 instead of UTF-8. This will likely fail `pip install -r requirements.txt` on a clean environment.
- Files: `requirements.txt`
- Impact: Project cannot be installed from a fresh clone without manually fixing the file encoding.
- Fix approach: Recreate `requirements.txt` as UTF-8, e.g., `pip freeze > requirements.txt`.

**`AllOptional` metaclass uses internal Pydantic API:**
- Issue: `AllOptional` in `pushframe/models/meta.py` subclasses `pydantic.main.ModelMetaclass` and directly manipulates `__annotations__`, an internal implementation detail not part of the public API.
- Files: `pushframe/models/meta.py`, `pushframe/models/frame.py:93`
- Impact: Silently breaks on any Pydantic version change. The approach also does not handle nested model defaults correctly.
- Fix approach: Replace with a `model_rebuild`-based approach or a classmethod factory in Pydantic v2. In v1 context, a simpler dataclass approach avoids metaclass manipulation.

**`datetime.utcnow()` is deprecated:**
- Issue: `pushframe/utils/dt.py:11` uses `datetime.utcnow()`, which is deprecated in Python 3.12 and produces naive (timezone-unaware) datetime objects.
- Files: `pushframe/utils/dt.py`
- Impact: Will emit deprecation warnings on Python 3.12+; produces ambiguous naive datetimes.
- Fix approach: Replace with `datetime.now(timezone.utc)` and update `format_dt_to_aura` to strip timezone info if the API requires naive strings.

**`main.py` is a non-functional stub:**
- Issue: `main.py` contains only an instantiation and a single API call, with a TODO to port the actual working example. There is no usable entry point for the library.
- Files: `main.py`
- Impact: New users cannot run anything meaningful.
- Fix approach: Implement or document a working example for `dump_frame` and `upload_image`.

## Known Bugs

**`upload_image` uses `sqsClient` before it is initialized in `__init__`:**
- Symptoms: `AttributeError: 'Aura' object has no attribute 'sqsClient'` if `get_sqs()` is not called before any direct reference to `self.sqsClient` outside the method's call path.
- Files: `pushframe/aura.py:109-128`
- Trigger: Within `upload_image`, `get_sqs()` is called first (line 109), which sets `self.sqsClient` as a side effect. However, `sqsClient` is not initialized in `__init__`, so any other code path accessing `self.sqsClient` directly will raise `AttributeError`.
- Workaround: Always call `get_sqs()` before accessing `self.sqsClient`. Proper fix: initialize `self.sqsClient = None` in `__init__` and instantiate lazily with a guard.

**`get_sqs()` uses a hardcoded frame ID instead of the frame's SQS queue URL:**
- Symptoms: SQS polling during upload connects to a fixed, hardcoded frame (`4ab446b4-33a7-4a76-881d-d545d153ab5a`) rather than the target frame being uploaded to.
- Files: `pushframe/aura.py:127-128`
- Trigger: Any call to `upload_image`. The `Frame` model already has `client_queue_url` and `frame_queue_url` fields that provide the correct queue identifier.
- Workaround: None — the SQS polling step in `upload_image` is effectively broken for any frame other than the hardcoded one.
- Fix approach: Pass the `Frame` object (or its `client_queue_url`) into `upload_image` and use it instead of the hardcoded queue name.

**`PlaylistApi.get_playlist_assets()` silently returns `None`:**
- Symptoms: Calling `playlist_api.get_playlist_assets(...)` always returns `None` because the result of `self._client.get(...)` is not returned.
- Files: `pushframe/api/playlistApi.py:8-9`
- Trigger: Any call to `get_playlist_assets`.
- Workaround: None.
- Fix approach: Add `return` before `self._client.get(...)`.

**`AssetApi.update_taken_at_date()` parses API response incorrectly:**
- Symptoms: Returns a partially-constructed `Asset` object or raises `ValidationError` because `Asset(**json_response)` is called with the full API response dict instead of `Asset(**json_response.get('asset'))`.
- Files: `pushframe/api/assetApi.py:72`
- Trigger: Any call to `update_taken_at_date`.
- Fix approach: Change line 72 to `return Asset(**json_response.get('asset'))`.

**`NotificationApi.get_notification_settings()` has a URL typo:**
- Symptoms: Makes a GET request to `f/notifications/settings/` (a relative path starting with `f` and missing the leading `/`), which resolves to an invalid URL and likely returns a 404.
- Files: `pushframe/api/notificationApi.py:8`
- Trigger: Any call to `get_notification_settings`.
- Fix approach: Change `'f/notifications/settings/'` to `'/notifications/settings.json'` (matching the pattern of other endpoints).

**Unclosed file handle in `upload_image`:**
- Symptoms: `open(image_path, 'rb')` on line 114 is never closed. On long-running processes or repeated calls, this leaks file descriptors.
- Files: `pushframe/aura.py:114`
- Fix approach: Replace `open(image_path, 'rb').read()` with a `with open(image_path, 'rb') as f: data = f.read()` block, then pass `data` to `client.upload_file`.

**`logs/` and `cache/` directories are never created:**
- Symptoms: `aura.py:137` configures loguru to write to `logs/file_{time}.log` but the `logs/` directory is not in the repo and not created at startup. Similarly, `cache.py` writes to `cache/` which may not exist. Both raise `FileNotFoundError` on first run.
- Files: `pushframe/aura.py:137`, `pushframe/cache.py:10`, `pushframe/cache.py:25`
- Trigger: First run on a fresh clone.
- Fix approach: Add `os.makedirs('logs/', exist_ok=True)` in `_init_logger()` and similarly for `cache/` in `cache.py`. Better: make both paths configurable.

## Security Considerations

**Plaintext passwords and auth tokens logged to file:**
- Risk: `client.py` logs the full `data` dict for every POST request at INFO level (line 46), and logs the full response body at DEBUG level (line 50). The login payload at `/login.json` contains the user's plaintext password. The login response contains `auth_token`. Both are written to `logs/file_{time}.log`.
- Files: `pushframe/client.py:46`, `pushframe/client.py:50`, `pushframe/api/accountApi.py:27`
- Current mitigation: None.
- Recommendations: Redact sensitive keys (`password`, `auth_token`, `x-token-auth`) before logging. Use a structured log filter or sanitize the payload dict before passing to logger.

**Hardcoded AWS Cognito Identity Pool IDs in source:**
- Risk: The Cognito pool IDs `us-east-1:b92826c0-8274-43db-abff-136977c13598` (S3) and `us-east-1:98ccd0ff-69fe-4e9a-ad34-671b4381ab12` (SQS) are committed directly in source. These are semi-public but their exposure in a public repo allows anyone to request temporary credentials for the Aura Frames AWS infrastructure without authentication.
- Files: `pushframe/aws/s3client.py:9-10`, `pushframe/aws/sqsclient.py:5-6`
- Current mitigation: TODOs acknowledge the issue but no action taken.
- Recommendations: Move to environment variables (e.g., `AURA_S3_POOL_ID`, `AURA_SQS_POOL_ID`) read in `settings.py`, consistent with existing credential handling.

**`auth_token` stored on `User` model with no expiry tracking:**
- Risk: The `User` model in `pushframe/models/user.py:22` carries `auth_token` but there is no expiry timestamp, refresh mechanism, or invalidation. A stale token is used indefinitely once set.
- Files: `pushframe/models/user.py:22`, `pushframe/aura.py:45-48`
- Current mitigation: None.
- Recommendations: Track token age or re-authenticate on 401 responses.

**No HTTP response status validation — API errors silently ignored:**
- Risk: Every method in `pushframe/client.py` calls `response.json()` unconditionally regardless of HTTP status code. A 401, 403, or 500 response that returns JSON will be silently processed, potentially leading to `None` being passed to Pydantic models, raising `ValidationError` with no useful context. A non-JSON error response will raise an `httpx.JSONDecodeError` with no indication of the underlying HTTP error.
- Files: `pushframe/client.py:43`, `pushframe/client.py:54`, `pushframe/client.py:65`, `pushframe/client.py:76`
- Current mitigation: `accountApi.py` has `# TODO: Error handling` stubs (lines 29, 57) that check for the `'error'` key but take no action.
- Recommendations: Call `response.raise_for_status()` before `response.json()` in all `Client` methods and define a custom exception hierarchy.

**Nominatim user-agent identifies as test application:**
- Risk: `ExifWriter.geolocator` in `pushframe/exif.py:34` uses `user_agent="Upload Scripting Test"`. Nominatim's ToS require a unique, descriptive user-agent identifying the real application. Using a test identifier on production traffic violates the ToS and may result in IP bans.
- Files: `pushframe/exif.py:34`
- Current mitigation: None.
- Recommendations: Set a proper user-agent string (e.g., `"pushframe-python-client/1.0"`).

## Performance Bottlenecks

**SQS long polling blocks the main thread for up to 20 seconds:**
- Problem: `SQSClient.receive_message()` defaults to `wait_time_seconds=20` (line 26 of `sqsclient.py`). When called from `upload_image`, this blocks the calling thread for up to 20 seconds per upload, twice per upload.
- Files: `pushframe/aws/sqsclient.py:26`, `pushframe/aura.py:110`, `pushframe/aura.py:122`
- Cause: Synchronous boto3 call with long polling on the main thread with no timeout or threading.
- Improvement path: Run SQS polling in a background thread or migrate to async boto3 (`aiobotocore`). The TODO in `sqsclient.py:9` acknowledges this.

**`get_all_assets` uses a fixed 1-second sleep between pages:**
- Problem: `pushframe/aura.py:60` sleeps 1 second between every paginated asset fetch. For a frame with 2000 assets and a page size of 1000, this adds a mandatory 1-second delay that is not necessary if the API is not rate-limiting.
- Files: `pushframe/aura.py:60`
- Cause: Defensive rate-limit workaround with no adaptive backoff. The `# TODO: Make better (tm)` comment acknowledges this.
- Improvement path: Use adaptive backoff (retry with exponential delay on 429 responses) instead of unconditional sleep.

**Nominatim geocoding is synchronous with per-request network I/O:**
- Problem: `ExifWriter._lookup_gps()` makes a synchronous HTTP request to Nominatim for each unique `location_name` encountered during image export. This serializes geocoding with image download.
- Files: `pushframe/exif.py:44`, `pushframe/export.py:49`
- Cause: No pre-fetch, no async, no batching.
- Improvement path: Pre-geocode all location names before starting image export, or move to async geocoding.

## Fragile Areas

**`AllOptional` metaclass in `pushframe/models/meta.py`:**
- Files: `pushframe/models/meta.py`, `pushframe/models/frame.py:93`
- Why fragile: Directly manipulates `pydantic.main.ModelMetaclass.__new__` and `__annotations__`. This is an internal Pydantic implementation detail. Any Pydantic patch release that changes metaclass internals will silently break `FramePartial` in ways that may not surface until runtime.
- Safe modification: Do not add new fields to `Frame` without verifying `FramePartial` still works. Do not upgrade Pydantic without testing `FramePartial` instantiation.
- Test coverage: None.

**`Aura.upload_image()` — multiple untested integration points:**
- Files: `pushframe/aura.py:101-123`
- Why fragile: The method chains five external I/O operations (local file read, two API calls, SQS poll, S3 upload, another API call, another SQS poll) with no error handling beyond the initial image open. Any step failure crashes silently or raises an unhandled exception. The `AssetApi` class is annotated `# TODO: Untested`.
- Safe modification: Treat as experimental. Do not use in production without adding error handling to each step.
- Test coverage: None.

**`cache.py` — file system cache with no error handling:**
- Files: `pushframe/cache.py`
- Why fragile: The `cache` decorator opens and writes files without any try/except. A partial write (e.g., crash during `json.dump`) will leave a corrupt cache file that will be read back on the next invocation and silently return corrupt data (since only `os.path.isfile` is checked). There is also no cache invalidation, TTL, or clearing mechanism.
- Safe modification: Clear the `cache/` directory manually if API responses change.
- Test coverage: None.

**`exif.py` — bare `except:` clauses swallow all exceptions:**
- Files: `pushframe/exif.py:45`, `pushframe/exif.py:91`
- Why fragile: Both bare `except:` blocks (geocoding failure and piexif insertion failure) log a message but return or continue silently. This means images can be saved with missing or corrupted EXIF data with no indication to the caller.
- Safe modification: Inspect log output carefully; image EXIF may be incomplete.
- Test coverage: None.

## Test Coverage Gaps

**No tests exist:**
- What's not tested: The entire codebase has zero test files and no test framework configured. No `pytest.ini`, `setup.cfg`, `pyproject.toml`, or test directories were found.
- Files: All files under `pushframe/`
- Risk: Any change to the API response format, Pydantic model fields, or internal logic will fail silently. Several known bugs (see above) would be caught by basic unit tests.
- Priority: High

**`AssetApi` explicitly marked untested:**
- What's not tested: All methods in `pushframe/api/assetApi.py` — `batch_update`, `get_asset_by_local_identifier`, `update_taken_at_date`, `delete_asset`, `crop_asset`.
- Files: `pushframe/api/assetApi.py:3`
- Risk: `update_taken_at_date` has a confirmed parsing bug (see Known Bugs) that would be caught by a simple mock test.
- Priority: High

**`NotificationApi` marked untested:**
- What's not tested: All methods in `pushframe/api/notificationApi.py`.
- Files: `pushframe/api/notificationApi.py:4`
- Risk: The URL typo bug (see Known Bugs) would be caught immediately by a test that validates the request URL.
- Priority: Medium

**`PeopleApi` methods marked potentially non-operational:**
- What's not tested: `get_person` and `get_person_assets` in `pushframe/api/peopleApi.py:17,23` are marked with `# TODO: This may not be operational`.
- Files: `pushframe/api/peopleApi.py`
- Priority: Low

## Missing Critical Features

**Error handling framework:**
- Problem: No custom exception types exist. API errors, authentication failures, and network errors all surface as raw `httpx` exceptions, `KeyError`, `AttributeError`, or Pydantic `ValidationError` with no actionable context.
- Blocks: Reliable use of any API method in non-interactive scripts.

**`Aura.clone()` is unimplemented:**
- Problem: `pushframe/aura.py:91-93` defines `clone()` as a pass statement with a TODO. Cloning a frame (copying assets between frames) is described as a primary use case in the README.
- Blocks: Frame cloning/migration workflows.

**`Aura.upload_images()` is unimplemented:**
- Problem: `pushframe/aura.py:95-99` defines `upload_images()` as a pass statement with TODOs. Bulk upload is described as a core workflow.
- Blocks: Batch photo upload workflows.

**No retry logic:**
- Problem: Network failures, transient 5xx errors, and rate limit (429) responses are not retried. A single transient failure aborts the entire operation.
- Blocks: Reliable operation over unreliable networks or with large asset counts.

---

*Concerns audit: 2026-06-29*

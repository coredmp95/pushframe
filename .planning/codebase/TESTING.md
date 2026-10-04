# Testing Patterns

**Analysis Date:** 2026-06-29

## Test Framework

**Runner:**
- None detected. No `pytest.ini`, `setup.cfg [tool:pytest]`, `pyproject.toml [tool.pytest]`, `tox.ini`, or `jest.config.*` found.
- No test runner is configured.

**Assertion Library:**
- None in use.

**Run Commands:**
- No test commands defined. No `Makefile`, `scripts/` directory, or `pyproject.toml` with test scripts found.

## Test File Organization

**Location:**
- No test files exist in the repository. The search for `*.test.*` and `*.spec.*` files, and `test_*` / `*_test.py` Python test files, returned no results.

**Structure:**
```
(no tests)
```

## Test Coverage

**Requirements:** None enforced — no coverage config found (no `.coveragerc`, `coverage.ini`, or `[tool.coverage]` section).

**Current state:** 0% — there are no automated tests of any kind.

**Explicit acknowledgement in source:**
- `pushframe/api/assetApi.py:3` — `# TODO: Untested`
- `pushframe/api/notificationApi.py:4` — `# TODO: Test`

## Test Types

**Unit Tests:** Not present

**Integration Tests:** Not present

**E2E Tests:** Not present

## What Needs Testing

The following areas have zero test coverage and represent the highest-value targets for initial test authoring:

**Data models (`pushframe/models/`):**
- Pydantic model construction from realistic API JSON payloads
- `AssetPartialId` validator: `check_id_or_local_id` (`asset.py:118`) — requires either `id` or `local_identifier`
- `AllOptional` metaclass behavior (`meta.py`) making all fields optional on `FramePartial`
- Enum deserialization for `ActivityType`, `ReactionType`, `Feature`

**HTTP client (`pushframe/client.py`):**
- `Client.get/post/put/delete` request formation and cookie forwarding
- `_set_cookies` behavior
- History deque rotation (maxlen enforcement)

**API layer (`pushframe/api/`):**
- `FrameApi.get_frames` — JSON-to-model mapping
- `FrameApi.get_assets` — pagination cursor handling
- `AccountApi.login` — credential payload construction and error path (currently a `pass`)
- `AssetApi.batch_update` — `include={}` dict field filtering
- `ActivityApi` — comment creation and deletion

**Utility functions (`pushframe/utils/`):**
- `parse_aura_dt` / `format_dt_to_aura` round-trip (`dt.py`)
- `build_path` with and without `make_dir=True` (`io.py`)
- `write_model` for both single models and lists (`io.py`)

**Business logic (`pushframe/aura.py`):**
- `get_all_assets` cursor pagination loop
- `download_images_from_assets` failure collection behavior

## Recommended Testing Stack

Given the project uses Python with `httpx` as the HTTP client, the natural choice is:

```bash
# Install
pip install pytest pytest-httpx pytest-mock coverage

# Run tests (once written)
pytest                         # Run all tests
pytest --cov=pushframe        # With coverage
pytest -x                      # Stop on first failure
```

**Mocking HTTP:**
- `pytest-httpx` provides `httpx_mock` fixture for intercepting `httpx.Client` calls without network access
- Alternative: `unittest.mock.patch` on `Client.get/post/put/delete` directly

**Example pattern for future tests:**
```python
# tests/test_dt.py
from pushframe.utils.dt import parse_aura_dt, format_dt_to_aura

def test_parse_aura_dt_round_trip():
    dt_str = '2023-06-15T14:30:00.000000Z'
    assert format_dt_to_aura(parse_aura_dt(dt_str)) == dt_str
```

```python
# tests/api/test_frame_api.py
from unittest.mock import MagicMock
from pushframe.api.frameApi import FrameApi

def test_get_frames_returns_frame_list():
    client = MagicMock()
    client.get.return_value = {
        'frames': [{'id': 'abc', 'name': 'Test', ...}]
    }
    api = FrameApi(client)
    frames = api.get_frames()
    assert len(frames) == 1
    assert frames[0].id == 'abc'
```

## Test File Placement Convention (Recommended)

No convention exists yet. Suggested approach consistent with Python norms:

```
pushframe-repo/
├── tests/
│   ├── __init__.py
│   ├── test_client.py
│   ├── api/
│   │   ├── __init__.py
│   │   ├── test_frame_api.py
│   │   ├── test_account_api.py
│   │   └── test_asset_api.py
│   ├── models/
│   │   ├── __init__.py
│   │   ├── test_asset.py
│   │   └── test_frame.py
│   └── utils/
│       ├── __init__.py
│       ├── test_dt.py
│       └── test_io.py
```

---

*Testing analysis: 2026-06-29*

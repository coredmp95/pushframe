# Technology Stack

**Analysis Date:** 2026-06-29

## Languages

**Primary:**
- Python 3 - All source code; confirmed Python 3.14.4 on development machine

## Runtime

**Environment:**
- Python 3.14 (`requires-python >=3.14` — the floor the dependency set is resolved and tested against)

**Package Manager:**
- `uv` — PEP 621 `pyproject.toml` + committed `uv.lock` for reproducible installs; dev/test commands run via `uv run`

## Frameworks

**Core:**
- None - Standalone CLI tool; no web framework. Console script: `pushframe = "pushframe.cli:main"`

**Data Validation:**
- pydantic >=2 - All API response models and domain objects use `BaseModel` (v2 idioms only); see `pushframe/models/`

**Testing:**
- pytest >=8 (dev extra) - offline suite (636 tests, zero warnings, DeprecationWarning errors) plus `@live`-marked drift oracle; `uv run --extra dev pytest -q -m "not live"`

**Build/Dev:**
- hatchling + custom build hook (`hatch_build.py`); `uv build` emits wheel + sdist

## Key Dependencies

**HTTP Client:**
- httpx[http2]>=0.27 - Primary HTTP client used in `pushframe/client.py`; HTTP/2 enabled via `httpx.Client(http2=True)`
- h2>=4 - HTTP/2 protocol implementation required by httpx for `http2=True` mode
- requests>=2.31 - declared but not imported by the package source (kept as a resolved pin)

**AWS SDK:**
- boto3>=1.34 - Used in `pushframe/aws/awsclient.py`, `pushframe/aws/s3client.py`, `pushframe/aws/sqsclient.py`
- botocore>=1.34 - AWS SDK core, used in `pushframe/aws/awsclient.py` for `botocore.config.Config`

**Image Processing:**
- Pillow>=10.4 - Image reading and thumbnail generation in `pushframe/aura.py` and `pushframe/export.py`
- pillow-heif>=1.6 - HEIC passthrough uploads (D-09: original bytes untouched, `data_uti='public.heic'`)
- piexif>=1.1.3 - EXIF data reading and writing in `pushframe/exif.py`

**Geocoding:**
- geopy>=2.4 - Reverse geocoding via Nominatim in `pushframe/exif.py`

**Logging:**
- loguru>=0.7 - Structured logging throughout; configured in `pushframe/aura.py` via `_init_logger()` under the MOD-04 process-level sink guard; writes to `logs/file_{time}.log` and stderr

**Progress Display:**
- tqdm>=4.66 - Progress bars for batch operations

## Configuration

**Environment:**
- Primary: `~/.config/pushframe/config.json` (mode 0600, atomic tmp+rename writes) — email, `auth_token` (token-first sessions; the password is never persisted), default frame, named pairs; managed by `pushframe config` (wizard / show / import / set / get)
- `PUSHFRAME_*` env vars are primary, `AURA_*` kept as a legacy fallback (IDN-04 deprecation window); values resolve at call time via `pushframe/utils/settings.py`, never at import
- AWS identifiers are settings-backed since MOD-02 (`AWS_S3_BUCKET`, `AWS_UPLOAD_IDENTITY_POOL_ID`, `AWS_SQS_IDENTITY_POOL_ID` with `PUSHFRAME_AWS_*` overrides)

**Build:**
- `pyproject.toml` (PEP 621) + committed `uv.lock`; hatchling build backend with custom hook

## Platform Requirements

**Development:**
- Python 3 with pip
- AWS credentials provisioned via Cognito Identity Pool (no static AWS keys required)
- Network access to `api.pushd.com`, `imgproxy.pushd.com`, AWS us-east-1 endpoints

**Production:**
- Shipped as the `pushframe` package (PyPI + signed APT repo + GitHub Releases — one tag, every channel)
- Entry point: the `pushframe` console script (`pushframe/cli.py`); `main.py` remains the legacy facade-only demo

---

*Stack analysis: 2026-06-29, corrected 2026-10-04 against the 5.1.28 tree.*

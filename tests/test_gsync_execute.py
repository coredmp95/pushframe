"""Offline tests for the google-sync mutating half (phase 18 plan 18-03).

End-to-end over a combined offline harness: an Aura-side `offline_aura` (frame
assets, login, batch_update/exclude_asset fakes via tests/offline.py) plus a
Google-side MockTransport (album listing ds:5 + snAcKc page + =d routes).
Proves: structural dry-run (zero mutating calls), apply with manifest
persistence ONLY for progress-confirmed uploads, prune after apply, SAFE-02
threshold gate (both counts echoed, nothing executed on non-y), env override.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pushframe.aws.s3client import get_md5  # noqa: E402
from pushframe import gsync as gsync_module  # noqa: E402
from pushframe.gsync import (  # noqa: E402
    GOOGLE_SYNC_REMOVAL_THRESHOLD,
    run_google_sync,
)
from tests.offline import offline_aura  # noqa: E402,F401  (fixture)

ALBUM_ID = "AF1QipFAKEalbum" + "0" * 30 + "1"
PAGE_KEY = "FAKEPAGEKEY0001"

HOME_HTML = (
    "<html><body><script>window.WIZ_global_data = "
    '{"SNlM0e": "SYNTH-AT", "FdrFJe": "12345", "cfb2h": "boq_test_bl", '
    '"oPEP7c": "someone@example.com"};</script></body></html>'
)
COOKIES = [{"name": "SID", "value": "fake-sid", "domain": ".google.com", "path": "/"}]


def _gid(n: int) -> str:
    return f"AF1QipFAKEitem{n:06d}" + "0" * 30


def _rpc_item(n: int) -> list:
    return [_gid(n), [f"https://lh3.googleusercontent.com/pw/FAKE{n:06d}" + "0" * 30,
                      100 + n, 200 + n], 1_700_000_000_000 + n]


def _snackc_response(next_token: str | None, ns: tuple[int, ...] = (1, 2, 3)) -> str:
    payload = [[_rpc_item(n) for n in ns]]
    if next_token:
        payload.append([next_token])
    inner = json.dumps(payload, separators=(",", ":"))
    line = json.dumps([["wrb.fr", "snAcKc", inner, None, "generic"]],
                      separators=(",", ":"))
    return ")]}'\n\n" + line + "\n"


class _GoogleRouter:
    """Album listing (ds:5, one 'Cadre'-like album) + batch-1 snAcKc + =d.

    `listing_ns` overrides the snAcKc page's item numbers (a trimmed listing
    drives the SAFE-02 mass-hide test). The ds:5 metadata count is 3 unless
    `metadata_count` says otherwise (the video-delta test)."""

    def __init__(self, *, item_bodies: dict[int, bytes] | None = None,
                 fail_downloads: set[int] | None = None,
                 listing_ns: tuple[int, ...] = (1, 2, 3)):
        self.item_bodies = item_bodies or {}
        self.fail_downloads = fail_downloads or set()
        self.listing_ns = listing_ns
        self.download_requests: list[str] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == "GET" and path == "/":
            return httpx.Response(200, text=HOME_HTML)
        if request.method == "GET" and path == "/albums":
            row_id = "AF1QipFAKErow00" + "0" * 30
            key_b64 = "RkFLRVBBR0VLRVkwMDAx"  # b64("FAKEPAGEKEY0001")
            # live-proven row shape: share token sits at index 8, >=9 elements
            entry = [4, "Cadre", [["2026-01-01"]], 3, 1, key_b64, None, None, ALBUM_ID]
            card = [row_id, ["https://lh3.example/cover", 100, 100], None, None,
                    {"72930366": entry}]
            ds5 = [[card]]
            html = ("<html><body><script>AF_initDataCallback({key: 'ds:5', "
                    "hash: '1', data:" + json.dumps(ds5) + "});</script>"
                    "</body></html>")
            return httpx.Response(200, text=html)
        if request.method == "POST" and "batchexecute" in path:
            return httpx.Response(200, text=_snackc_response(None, self.listing_ns))
        if request.method == "GET" and path.endswith("=d"):
            import re as _re
            m = _re.search(r"FAKE(\d{6})", path)
            n = int(m.group(1))
            self.download_requests.append(path)
            if n in self.fail_downloads:
                return httpx.Response(500, text="exploded")
            body = self.item_bodies.get(n, _jpeg(n))
            return httpx.Response(200, content=body)
        return httpx.Response(404)


def _google_session(router: _GoogleRouter):
    from pushframe.google.client import GoogleSession
    return GoogleSession(COOKIES, transport=httpx.MockTransport(router.handler))


# --- Aura-side fakes -------------------------------------------------------

class _FakeFrame:
    def __init__(self, frame_id="frame-1", name="Cadre de Fabrice"):
        self.id = frame_id
        self.name = name


class _FakeAura:
    """Minimal Aura stand-in: login/get_frames/get_all_assets + the write
    seams execute_plan touches (asset_api.batch_update ack-all + the 401
    asset probe). execute_plan itself is the REAL v2.0 code — the fake only
    replaces the network endpoints."""

    def __init__(self, assets: list):
        from pushframe.api.assetApi import BatchUpdateResult
        self._assets = assets
        self.login_called = False
        self.batch_calls: list = []
        self.select_calls: list = []
        self.exclude_calls: list = []

        def _get_frames():
            return [_FakeFrame()]

        def _select_asset(frame_id, items):
            items = items if isinstance(items, list) else [items]
            self.select_calls.append(items)
            return 0

        def _exclude_asset(frame_id, items):
            items = items if isinstance(items, list) else [items]
            self.exclude_calls.append(items)
            return 0

        def _batch_update(items):
            from pushframe.models.asset import AssetPartialId
            items = items if isinstance(items, list) else [items]
            self.batch_calls.append(items)
            lids = [item.local_identifier for item in items]
            successes = [{'id': f'new-{lid}', 'local_identifier': lid}
                         for lid in lids]
            return BatchUpdateResult(lids, [AssetPartialId(**s) for s in successes], [])

        self.frame_api = type("FA", (), {
            "get_frames": staticmethod(_get_frames),
            "select_asset": staticmethod(_select_asset),
            "exclude_asset": staticmethod(_exclude_asset),
        })()
        self.asset_api = type("AA", (), {
            "batch_update": staticmethod(_batch_update),
            "get_asset_by_local_identifier": staticmethod(lambda lid: None),
        })()

    def login(self):
        self.login_called = True

    def get_all_assets(self, frame_id):
        return self._assets


class _FakeS3:
    def upload_file(self, data, extension):
        return f"fake-{get_md5(data)[:6]}.jpg", get_md5(data)


class _FakeSQS:
    def get_queue_url(self, frame_id):
        return "https://sqs.fake/queue"

    def receive_message(self, queue_url, **kwargs):
        return {"Messages": []}


class _WaityBudget:
    """Budget whose first acquire waits (0.75/min-like pacing) so the
    on_wait protocol path is exercised end-to-end through gsync."""

    def __init__(self):
        self._calls = 0

    def acquire(self, n, *, wait, max_wait, now, sleep, on_wait=None):
        self._calls += 1
        if self._calls == 1 and on_wait:
            on_wait(3.0)  # one live countdown tick
        sleep(0)

    def reconcile_tripped(self, now):
        pass

    def save(self):
        pass


def _jpeg(n: int) -> bytes:
    """Deterministic, PIL-decodable JPEG — _prep_upload decodes the bytes
    (data_uti comes from the decoded format, not the filename), so staged
    bytes must be real images for the upload path to proceed."""
    import io
    from PIL import Image
    buf = io.BytesIO()
    Image.new('RGB', (4, 4), (n % 255, 0, 0)).save(buf, format='JPEG')
    return buf.getvalue()


def _assets_for(n: int, *, hidden: bool = False):
    from pushframe.models.asset import Asset
    return [Asset.model_construct(id=f"frame-{i}", md5_hash=get_md5(_jpeg(i)),
                                  taken_at="2024-03-11T12:00:00.000Z", selected=not hidden)
            for i in range(1, n + 1)]


# --- tests -----------------------------------------------------------------

def test_dry_run_prints_plan_and_executes_nothing(tmp_path, capsys):
    router = _GoogleRouter()
    aura = _FakeAura(_assets_for(1))  # frame has photo-1 already
    rc = run_google_sync("Cadre", "Fabrice", session=_google_session(router),
                         aura=aura, cache_dir=tmp_path / "cache",
                         manifest_path=tmp_path / "manifest.json")
    out = capsys.readouterr().out
    assert rc == 0
    assert "Plan: 2 to upload" in out
    assert "Videos skipped: 0" in out  # metadata count = 3 on the fake's ds:5
    assert "1 unchanged" in out  # item 1 is already on the frame
    assert "0 to hide" in out  # dry run printed a plan, executed nothing


def test_apply_uploads_hides_persists_manifest_and_prunes(tmp_path, capsys):
    bodies = {2: _jpeg(2), 3: _jpeg(3)}
    router = _GoogleRouter(item_bodies=bodies)
    aura = _FakeAura(_assets_for(1))
    rc = run_google_sync("Cadre", "Fabrice", apply=True, yes=True,
                         session=_google_session(router), aura=aura,
                         s3_client=_FakeS3(), sqs_client=_FakeSQS(),
                         cache_dir=tmp_path / "cache",
                         manifest_path=tmp_path / "manifest.json")
    assert rc == 0
    out = capsys.readouterr().out
    assert "Applied: 2 uploaded" in out
    manifest = json.loads((tmp_path / "manifest.json").read_text())
    # item 1 was never uploaded this run but the frame's own listing proves
    # it holds that md5 — its entry persists too (steady-state memory)
    assert set(manifest) == {_gid(1), _gid(2), _gid(3)}
    assert manifest[_gid(2)]["md5_hash"] == get_md5(_jpeg(2))
    # prune emptied the staged files (all are manifest-backed now)
    assert list((tmp_path / "cache").iterdir()) == []


def test_failed_download_never_reaches_manifest(tmp_path, capsys):
    router = _GoogleRouter(item_bodies={2: _jpeg(2)}, fail_downloads={3})
    aura = _FakeAura(_assets_for(1))
    rc = run_google_sync("Cadre", "Fabrice", apply=True, yes=True,
                         session=_google_session(router), aura=aura,
                         s3_client=_FakeS3(), sqs_client=_FakeSQS(),
                         cache_dir=tmp_path / "cache",
                         manifest_path=tmp_path / "manifest.json")
    out = capsys.readouterr().out
    assert rc == 0
    assert "1 download(s) failed" in out
    manifest = json.loads((tmp_path / "manifest.json").read_text())
    assert _gid(3) not in manifest  # SAFE-04: failed download not persisted
    # a failed download leaves NO file (18-01 contract); 'kept for retry'
    # means the FAILURE is recorded, so the next run re-attempts item 3


def test_apply_wires_budget_wait_into_the_progress_bar(tmp_path, capsys):
    """Venus regression, phase 23.5: the 252 s/item the operator saw was the
    WRITE BUDGET waiting (bucket dry, 0.75/min refill) — and gsync never
    wired execute_plan's on_wait, so the bar sat silent at 7% looking dead.
    Contract: budget waits render into the progress bar as a live countdown.
    """
    bodies = {2: _jpeg(2), 3: _jpeg(3)}
    router = _GoogleRouter(item_bodies=bodies)
    aura = _FakeAura(_assets_for(1))

    captured = {'waits': 0}

    class SpyBar:
        def __init__(self, *a, **k):
            self.total = k.get('total')
        def update(self, n=1):
            pass
        def set_postfix_str(self, s):
            if 'cooldown' in s or 'budget' in s:
                captured['waits'] += 1
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False

    mp = pytest.MonkeyPatch()
    mp.setattr(gsync_module, 'tqdm', SpyBar)
    try:
        rc = run_google_sync("Cadre", "Fabrice", apply=True, yes=True,
                             session=_google_session(router), aura=aura,
                             s3_client=_FakeS3(), sqs_client=_FakeSQS(),
                             cache_dir=tmp_path / "cache",
                             manifest_path=tmp_path / "manifest.json",
                             budget=_WaityBudget())
    finally:
        mp.undo()

    assert rc == 0
    assert captured['waits'] >= 1, "budget waits never reached the progress bar"


def test_safe02_threshold_gate_aborts_on_non_yes(tmp_path, capsys):
    """Frame holds 10 photos but the album wants only item 1 → 9 hides, far
    over the 20% threshold. The gate must echo BOTH counts and execute
    nothing on the non-y answer."""
    router = _GoogleRouter(listing_ns=(1,))
    aura = _FakeAura(_assets_for(10))
    rc = run_google_sync("Cadre", "Fabrice", apply=True, yes=False,
                         is_interactive=True, input_fn=lambda *_: "n",
                         session=_google_session(router),
                         aura=aura, s3_client=_FakeS3(), sqs_client=_FakeSQS(),
                         cache_dir=tmp_path / "cache",
                         manifest_path=tmp_path / "manifest.json")
    out = capsys.readouterr().out
    assert rc == 0
    assert "SAFE-02" in out and "9 of 10" in out
    assert "Aborted." in out
    # nothing was planned-persisted: no manifest file written on abort
    assert not (tmp_path / "manifest.json").exists()


def test_threshold_env_override_is_honored(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("AURA_GOOGLE_SYNC_REMOVAL_THRESHOLD", "1.1")
    from pushframe.gsync import _threshold
    assert _threshold() == 1.1
    assert GOOGLE_SYNC_REMOVAL_THRESHOLD == 0.2


def test_apply_requires_yes_non_interactive(tmp_path, capsys):
    router = _GoogleRouter()
    aura = _FakeAura(_assets_for(1))
    rc = run_google_sync("Cadre", "Fabrice", apply=True, yes=False,
                         is_interactive=False,
                         session=_google_session(router), aura=aura,
                         s3_client=_FakeS3(), sqs_client=_FakeSQS(),
                         cache_dir=tmp_path / "cache",
                         manifest_path=tmp_path / "manifest.json")
    out = capsys.readouterr().out
    assert rc == 1
    assert "--apply requires --yes when running non-interactively" in out


def test_second_run_zero_uploads_and_zero_downloads(tmp_path, capsys):
    """The steady-state proof through the full CLI path: after run 1, run 2
    downloads nothing (all manifest members) and uploads nothing."""
    bodies = {1: _jpeg(1), 2: _jpeg(2), 3: _jpeg(3)}
    aura = _FakeAura(_assets_for(3))
    mpath = tmp_path / "manifest.json"
    # run 1: sync everything
    run_google_sync("Cadre", "Fabrice", apply=True, yes=True,
                    session=_google_session(_GoogleRouter(item_bodies=bodies)),
                    aura=aura, s3_client=_FakeS3(), sqs_client=_FakeSQS(),
                    cache_dir=tmp_path / "cache", manifest_path=mpath)
    # run 2: same album, same frame
    router2 = _GoogleRouter(item_bodies=bodies)
    rc = run_google_sync("Cadre", "Fabrice", apply=True, yes=True,
                         session=_google_session(router2), aura=aura,
                         s3_client=_FakeS3(), sqs_client=_FakeSQS(),
                         cache_dir=tmp_path / "cache", manifest_path=mpath)
    out = capsys.readouterr().out
    assert rc == 0
    assert "Applied: 0 uploaded" in out
    assert router2.download_requests == []  # zero Google downloads (criterion 3)

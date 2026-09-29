"""Phase 23.5 — `pushframe doctor`, the field write-probe.

The release gap the venus regressions exposed: install journeys + offline
tests never exercise a real WRITE against the real anti-abuse surface, so
a release can ship while every check is green yet a sync fails on the
target machine. Doctor closes that gate deliberately — five checks
(session, frames, write, verify, cleanup) ending in a GO/NO-GO verdict.

Offline tests here: the CLI wiring, the read-only mode, the NO-GO paths,
and the diagnosis line. The REAL probe runs on the target machine (venus)
by design — it is never executed in CI.
"""
import httpx
import pytest

from pushframe import doctor


# --- fakes ------------------------------------------------------------------

class _FakeFrame:
    def __init__(self, frame_id="frame-1", name="Cadre de Fabrice"):
        self.id = frame_id
        self.name = name


class _FakeAssetsApi:
    def __init__(self, assets):
        self._assets = assets

    def get_assets(self, frame_id, limit=1000):
        return self._assets, None


class _FakeAura:
    def __init__(self, frames=None, assets=None):
        self._frames = frames if frames is not None else [_FakeFrame()]
        self.frame_api = type("F", (), {"get_frames": lambda s: self._frames})()
        self.asset_api = _FakeAssetsApi(assets or [])

    def login(self):
        return self


class _FakeS3:
    pass


class _FakeSQS:
    def get_queue_url(self, frame_id):
        return "https://sqs.fake/queue"


def _exec_stub(succeed=True, fail_reason="Client error '401 Unauthorized'"):
    """Patch execute_plan inside pushframe.doctor's import site."""
    import pushframe.sync as sync_mod

    def stub(plan, aura, frame_id, **kwargs):
        from pushframe.sync import ExecutionResult, SyncPlan
        if succeed:
            return ExecutionResult(upload_succeeded=len(plan.to_upload))
        return ExecutionResult(
            upload_failures=[(plan.to_upload[0], fail_reason)] if plan.to_upload else [])

    return stub


# --- tests ------------------------------------------------------------------

def test_read_only_go(capsys):
    rc = doctor.run_doctor(aura=_FakeAura(), do_write=False)
    out = capsys.readouterr().out
    assert rc == 0
    assert "✓ session" in out and "✓ frames read" in out
    assert "READ-ONLY GO" in out


def test_no_go_when_session_fails(capsys):
    aura = _FakeAura()

    def _boom():
        raise RuntimeError("login refused")

    aura.login = _boom
    rc = doctor.run_doctor(aura=aura)
    out = capsys.readouterr().out
    assert rc == 1
    assert "✗ session (login)" in out
    assert "NO-GO" in out


def test_no_go_when_write_401_without_body_has_no_false_verdict(capsys, monkeypatch):
    """A bare 401 (no server body captured — pre-5.0.6 client) must NOT
    invent a diagnosis: NO-GO stands, no verdict line."""
    aura = _FakeAura()
    monkeypatch.setattr("pushframe.sync.execute_plan",
                        _exec_stub(succeed=False))
    rc = doctor.run_doctor(aura=aura, s3_client=_FakeS3(),
                           sqs_client=_FakeSQS())
    out = capsys.readouterr().out
    assert rc == 1
    assert "✗ write probe" in out and "401" in out
    assert "Diagnosis" not in out and "NO-GO" in out


def test_no_go_with_401_body_prints_the_verdict(capsys, monkeypatch):
    """With the 5.0.6 body capture, the NO-GO carries the trip/token verdict."""
    aura = _FakeAura()
    monkeypatch.setattr("pushframe.sync.execute_plan", _exec_stub(
        succeed=False,
        fail_reason="401 Unauthorized for https://api.pushd.com/v5/... "
                    "— server body: {'error': True}"))
    rc = doctor.run_doctor(aura=aura, s3_client=_FakeS3(),
                           sqs_client=_FakeSQS())
    out = capsys.readouterr().out
    assert rc == 1
    assert "Diagnosis" in out and "anti-abuse trip" in out


def test_go_when_write_lands_and_cleanup_runs(capsys, monkeypatch):
    from pushframe.models.asset import Asset
    probe_md5 = "p" * 32
    seen = {}

    def exec_spy(plan, aura, frame_id, **kwargs):
        from pushframe.sync import ExecutionResult
        seen.setdefault("deletes", []).extend(plan.to_delete)
        return ExecutionResult(upload_succeeded=len(plan.to_upload))

    monkeypatch.setattr("pushframe.sync.execute_plan", exec_spy)
    monkeypatch.setattr(doctor, "_probe_image", lambda: type(
        "P", (), {"read_bytes": lambda s: b"\x89PNG" + bytes(64)})())

    class FakeFrameApi:
        def get_frames(self):
            return [_FakeFrame()]

        def get_assets(self, frame_id, limit=1000):
            return [Asset.model_construct(id="asset-probe-1",
                                          md5_hash=probe_md5)], None

    aura = _FakeAura()
    aura.frame_api = FakeFrameApi()
    monkeypatch.setattr("pushframe.aws.s3client.get_md5", lambda b: probe_md5)

    rc = doctor.run_doctor(aura=aura, s3_client=_FakeS3(),
                           sqs_client=_FakeSQS())
    out = capsys.readouterr().out
    assert rc == 0
    assert "✓ write probe" in out
    assert "✓ verify probe" in out and "asset-probe-1" in out
    assert "✓ cleanup" in out
    assert "VERDICT: GO" in out
    # exactly one delete was executed (the probe asset, hidden)
    assert len(seen["deletes"]) == 1

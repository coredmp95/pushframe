"""Phase 25 (MTF-02/03): --pair and --all in google-sync.

Per-pair state shards under the pair name; --all runs every pair with ONE
shared budget instance, records per-pair outcomes, keeps going on failure
(D-02) and exits 1 if any pair failed, 0 only if all OK.
"""
import httpx
import pytest

from pushframe.utils import settings
from pushframe import pairs as pairs_mod


# --- reuse the gsync_execute fakes -------------------------------------------

from tests.test_gsync_execute import (  # noqa: E402
    _GoogleRouter, _google_session, _FakeAura, _FakeS3, _FakeSQS,
    _assets_for, _jpeg, _gid, get_md5,
)


class _TwoFrameAura(_FakeAura):
    """Frames matching the two pairs (Frame A / Frame B); each holds item 1
    (non-empty: SAFE-01 refuses an empty frame listing)."""
    def __init__(self):
        super().__init__(_assets_for(1))
        self.frame_api = type("F", (), {"get_frames": lambda s: [
            type("Fr", (), {"id": "frame-a", "name": "Frame A"})(),
            type("Fr", (), {"id": "frame-b", "name": "Frame B"})()]})()


@pytest.fixture
def cfg_path(tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    monkeypatch.setattr(settings, "CONFIG_PATH", path)
    # pair state paths MUST shard under tmp too — the real ~/.config and
    # ~/.local/state hold accumulated state that would trip SAFE-01 drift.
    monkeypatch.setattr(pairs_mod, "pair_state_paths",
                        lambda name: (tmp_path / "pairs" / name
                                      / "google-manifest.json",
                                      tmp_path / "state" / name / "cache"))
    return path


def _pair(name, album, frame):
    pairs_mod.pair_add(name, album=album, frame=frame)


# --- --pair resolves and shards ---------------------------------------------

def test_run_by_pair_shards_state_under_pair_name(tmp_path, cfg_path):
    _pair("cadre-venus", "Cadre", "Cadre de Fabrice")
    from pushframe.gsync import run_google_sync
    rc = run_google_sync("Cadre", "Cadre de Fabrice", apply=True, yes=True,
                         session=_google_session(_GoogleRouter(
                             item_bodies={2: _jpeg(2)})),
                         aura=_FakeAura(_assets_for(1)),   # non-empty: SAFE-01
                         s3_client=_FakeS3(), sqs_client=_FakeSQS(),
                         cache_dir=tmp_path / "cache",
                         manifest_path=tmp_path / "manifest.json",
                         pair="cadre-venus")
    assert rc == 0
    # explicit paths win (test-scoped), but the pair RESOLVED (no error, and
    # the run didn't fall back to album-substring ambiguity handling)
    from pushframe import config_store
    assert config_store.load()["pairs"]["cadre-venus"]["album"] == "Cadre"


def test_run_by_unknown_pair_is_named_error(tmp_path, cfg_path, capsys):
    from pushframe.gsync import run_google_sync
    rc = run_google_sync("Cadre", "Cadre de Fabrice", apply=False,
                         session=_google_session(_GoogleRouter()),
                         aura=_FakeAura(_assets_for(0)),
                         cache_dir=tmp_path / "cache",
                         manifest_path=tmp_path / "manifest.json",
                         pair="ghost")
    assert rc == 2
    out = capsys.readouterr().out
    assert 'unknown pair "ghost"' in out
    # 5.1.21: the "none configured" remedy used to be truncated (the second
    # half of the string literal sat orphaned on the next line).
    assert 'none configured' in out
    assert 'add one with `pushframe config pair add`' in out


# --- --all: shared budget, continue-on-failure, exit codes -------------------

def test_all_runs_every_pair_with_one_shared_budget(tmp_path, cfg_path):
    _pair("a-first", "Cadre", "Frame A")
    _pair("b-second", "Cadre", "Frame B")
    from pushframe.gsync import run_google_sync

    budgets = []

    class _SharedBudgetProbe:
        """A budget identity the runner must pass to BOTH pairs."""
        def __init__(self):
            pass
        def acquire(self, *a, **k):
            pass
        def reconcile_tripped(self, now):
            pass
        def save(self):
            pass

    shared = _SharedBudgetProbe()

    import pushframe.sync as sync_mod
    def spy_execute(plan, aura_, frame_id, **kwargs):
        budgets.append(id(kwargs.get('budget')))
        from pushframe.sync import ExecutionResult
        return ExecutionResult(upload_succeeded=len(plan.to_upload))

    mp = pytest.MonkeyPatch()
    mp.setattr(sync_mod, 'execute_plan', spy_execute)
    try:
        rc = run_google_sync("Cadre", "--all", apply=True, yes=True,
                             run_all=True,
                             session=_google_session(_GoogleRouter(
                                 item_bodies={2: _jpeg(2), 3: _jpeg(3)})),
                             aura=_TwoFrameAura(),
                             s3_client=_FakeS3(), sqs_client=_FakeSQS(),
                             cache_dir=tmp_path / "c",
                             manifest_path=tmp_path / "m.json",
                             budget=shared)
    finally:
        mp.undo()

    assert rc == 0
    assert len(budgets) == 2                     # both pairs ran
    assert budgets[0] == budgets[1] == id(shared)  # ONE shared instance


def test_all_continues_after_first_pair_failure_and_exits_1(tmp_path, cfg_path, capsys):
    _pair("a-fails", "Cadre", "Frame A")
    _pair("b-works", "Cadre", "Frame B")
    from pushframe.gsync import run_google_sync

    ran = []

    import pushframe.sync as sync_mod
    def flaky_execute(plan, aura_, frame_id, **kwargs):
        ran.append(frame_id)
        from pushframe.sync import ExecutionResult
        if len(ran) == 1:   # the FIRST pair (sorted: a-fails) fails
            raise RuntimeError("boom")
        return ExecutionResult(upload_succeeded=len(plan.to_upload))

    mp = pytest.MonkeyPatch()
    mp.setattr(sync_mod, 'execute_plan', flaky_execute)
    try:
        rc = run_google_sync("Cadre", "--all", apply=True, yes=True,
                             run_all=True,
                             session=_google_session(_GoogleRouter()),
                             aura=_TwoFrameAura(),
                             s3_client=_FakeS3(), sqs_client=_FakeSQS(),
                             cache_dir=tmp_path / "c",
                             manifest_path=tmp_path / "m.json")
    finally:
        mp.undo()

    out = capsys.readouterr().out
    assert rc == 1                               # at least one failed
    assert len(ran) == 2                         # the second pair STILL ran
    assert "a-fails" in out and "FAILED" in out
    assert "b-works" in out and "ok" in out.lower()


def test_all_zero_pairs_named_error(tmp_path, cfg_path, capsys):
    from pushframe.gsync import run_google_sync
    rc = run_google_sync("Cadre", "--all", apply=False, run_all=True,
                         session=_google_session(_GoogleRouter()),
                         aura=_FakeAura(_assets_for(0)),
                         cache_dir=tmp_path / "c",
                         manifest_path=tmp_path / "m.json")
    out = capsys.readouterr().out
    assert rc == 2
    assert "no pairs configured" in out


# --- anti-ping-pong: two pairs on the SAME frame are refused (5.1.31) ---------

def test_all_refuses_two_pairs_on_the_same_frame(tmp_path, cfg_path, capsys):
    """Two mirroring pairs resolving to one frame fight forever (each run
    hides the other's photos, the next re-shows them). --all refuses up
    front with a named error instead of running the silent ping-pong."""
    _pair("a-salon", "Cadre", "Frame A")
    _pair("b-salon", "Cadre", "Frame A")
    from pushframe.gsync import run_google_sync
    rc = run_google_sync("Cadre", "--all", apply=True, yes=True, run_all=True,
                         session=_google_session(_GoogleRouter()),
                         aura=_TwoFrameAura(),
                         s3_client=_FakeS3(), sqs_client=_FakeSQS(),
                         cache_dir=tmp_path / "c",
                         manifest_path=tmp_path / "m.json")
    out = capsys.readouterr().out
    assert rc == 2
    assert "a-salon" in out and "b-salon" in out
    assert "SAME frame" in out
    assert 'pair remove' in out          # the remedy names the exact command
    assert 'push' in out                 # ... and the additive alternative


def test_same_frame_guard_is_case_insensitive(tmp_path, cfg_path, capsys):
    """Frame resolution downstream is case-insensitive substring matching,
    so the guard compares casefolded names — 'Frame A' and 'frame a' clash."""
    _pair("a-salon", "Cadre", "Frame A")
    _pair("b-salon", "Cadre", "frame a")
    from pushframe.gsync import run_google_sync
    rc = run_google_sync("Cadre", "--all", apply=True, yes=True, run_all=True,
                         session=_google_session(_GoogleRouter()),
                         aura=_TwoFrameAura(),
                         s3_client=_FakeS3(), sqs_client=_FakeSQS(),
                         cache_dir=tmp_path / "c",
                         manifest_path=tmp_path / "m.json")
    assert rc == 2
    assert "SAME frame" in capsys.readouterr().out


def test_clash_refuses_everything_before_any_pair_runs(tmp_path, cfg_path, capsys):
    """One clashing duo refuses the WHOLE --all run — nothing executes,
    not even the healthy pairs; only the clashing pairs are named."""
    _pair("a-clash1", "Cadre", "Frame A")
    _pair("b-clash2", "Cadre", "Frame A")
    _pair("c-healthy", "Cadre", "Frame B")
    from pushframe.gsync import run_google_sync

    ran = []
    import pushframe.sync as sync_mod
    mp = pytest.MonkeyPatch()
    mp.setattr(sync_mod, 'execute_plan',
               lambda plan, aura_, frame_id, **k: ran.append(frame_id) or
               __import__('pushframe.sync', fromlist=['ExecutionResult'])
               .ExecutionResult(upload_succeeded=len(plan.to_upload)))
    try:
        rc = run_google_sync("Cadre", "--all", apply=True, yes=True,
                             run_all=True,
                             session=_google_session(_GoogleRouter()),
                             aura=_TwoFrameAura(),
                             s3_client=_FakeS3(), sqs_client=_FakeSQS(),
                             cache_dir=tmp_path / "c",
                             manifest_path=tmp_path / "m.json")
    finally:
        mp.undo()

    out = capsys.readouterr().out
    assert rc == 2
    assert ran == []                     # nothing executed — fail closed
    assert "=== pair [" not in out
    assert "a-clash1" in out and "b-clash2" in out
    assert "c-healthy" not in out        # only the clash is named

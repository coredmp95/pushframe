"""Offline tests for `pushframe.reconcile` (Phase 11 Plan 03) -- the pure
placeholder predicate (`find_placeholders`, Task 1) and the bounded, gated
removal path (`apply_reconciliation`, Task 3).

Unmarked (no @pytest.mark.live) -- Task 1's tests exercise plain `Asset`
model instances only, no `offline_aura` transport, proving classification
performs no network call. Task 3's `apply_reconciliation` tests DO use
`offline_aura` (`httpx.MockTransport`), matching `tests/test_execute_plan.py`'s
harness, since that function is this module's one network-touching path.
"""
import json
from datetime import datetime, timedelta

import httpx
import pytest
from loguru import logger

from pushframe.models.asset import Asset
from pushframe.reconcile import (
    RECONCILE_PROBE_CANDIDATE_LIMIT,
    ReconcileResult,
    apply_reconciliation,
    find_placeholders,
)
from pushframe.utils.dt import format_dt_to_aura
from tests.offline import FIXTURES_DIR, offline_aura

FRAME_ID = 'frame-fake-0001'
REMOVE_ASSET_PATH = f'/v5/frames/{FRAME_ID}/remove_asset.json'

RECENT_TOKEN = '__RECENT_CREATED_AT_TOKEN__'
# Fixed reference instant for every test below -- far enough in the future
# of the fixture's hardcoded old dates (2020/2023) that those rows are
# unambiguously "old" without depending on wall-clock time at test-run time.
NOW = datetime(2030, 6, 15, 12, 0, 0)
# Default fill for the recently-created row's token when a test doesn't care
# about that row -- old enough (relative to NOW) that it can never
# accidentally land in `recently_created` and pollute an unrelated
# assertion.
_OLD_FILL = '2020-01-01T00:00:00.000Z'


@pytest.fixture(autouse=True)
def _reset_loguru():
    # loguru's `logger` is a process-global singleton; reset around each
    # test so no sink from a prior test/module can fire unexpectedly here.
    logger.remove()
    yield
    logger.remove()


def _load_placeholder_assets(recent_created_at: str = _OLD_FILL) -> list[Asset]:
    """Load tests/fixtures/assets_placeholders.json, substituting the
    recently-created row's placeholder token for `recent_created_at`, and
    hydrate every entry through the real `Asset` model (proving the fixture
    is real-shaped, not just a JSON blob)."""
    data = json.loads((FIXTURES_DIR / 'assets_placeholders.json').read_text())
    for entry in data['assets']:
        if entry.get('created_at') == RECENT_TOKEN:
            entry['created_at'] = recent_created_at
    return [Asset(**a) for a in data['assets']]


def _ids(assets) -> set:
    return {a.id for a in assets}


# ---------------------------------------------------------------------------
# Test 1: a stuck placeholder (older than the threshold) lands in `stuck`.
# ---------------------------------------------------------------------------

def test_stuck_placeholder_older_than_threshold_lands_in_stuck():
    assets = _load_placeholder_assets()

    result = find_placeholders(assets, now=NOW)

    assert {'asset-fake-stuck-001', 'asset-fake-stuck-002'} <= _ids(result.stuck)
    assert result.total_scanned == len(assets)


# ---------------------------------------------------------------------------
# Test 2: a video-shaped asset (md5_hash None, file_name/uploaded_at
# populated) lands in neither `stuck` nor `recently_created` -- the strict
# conjunction excludes it. It must not appear in `unknown_age` either.
# ---------------------------------------------------------------------------

def test_video_shaped_asset_excluded_from_every_bucket():
    assets = _load_placeholder_assets()

    result = find_placeholders(assets, now=NOW)

    every_bucket = _ids(result.stuck) | _ids(result.recently_created) | _ids(result.unknown_age)
    assert 'asset-fake-video-001' not in every_bucket


# ---------------------------------------------------------------------------
# Test 3: a partially-hydrated asset (only md5_hash None, or only file_name
# None) is likewise excluded from every bucket.
# ---------------------------------------------------------------------------

def test_partially_hydrated_assets_excluded_from_every_bucket():
    only_md5_null = Asset.model_construct(
        id='partial-only-md5-null', uploaded_at='2020-01-01T00:00:00.000Z',
        file_name='x.jpg', md5_hash=None, created_at='2020-01-01T00:00:00.000Z',
    )
    only_file_name_null = Asset.model_construct(
        id='partial-only-file-name-null', uploaded_at='2020-01-01T00:00:00.000Z',
        file_name=None, md5_hash='abc123', created_at='2020-01-01T00:00:00.000Z',
    )

    result = find_placeholders([only_md5_null, only_file_name_null], now=NOW)

    assert result.stuck == []
    assert result.recently_created == []
    assert result.unknown_age == []
    assert result.total_scanned == 2


# ---------------------------------------------------------------------------
# Test 4: a matching asset whose creation time is 1 hour old lands in
# `recently_created`, not `stuck`, at the 24h default threshold.
# ---------------------------------------------------------------------------

def test_recently_created_placeholder_at_default_threshold():
    one_hour_ago = format_dt_to_aura(NOW - timedelta(hours=1))
    assets = _load_placeholder_assets(recent_created_at=one_hour_ago)

    result = find_placeholders(assets, now=NOW)

    assert 'asset-fake-recent-001' in _ids(result.recently_created)
    assert 'asset-fake-recent-001' not in _ids(result.stuck)


# ---------------------------------------------------------------------------
# Test 5: the same asset with age_threshold_seconds=1800 (30 min) lands in
# `stuck` -- the threshold is genuinely overridable.
# ---------------------------------------------------------------------------

def test_overridden_age_threshold_reclassifies_the_same_row_as_stuck():
    one_hour_ago = format_dt_to_aura(NOW - timedelta(hours=1))
    assets = _load_placeholder_assets(recent_created_at=one_hour_ago)

    result = find_placeholders(assets, now=NOW, age_threshold_seconds=1800)

    assert 'asset-fake-recent-001' in _ids(result.stuck)
    assert 'asset-fake-recent-001' not in _ids(result.recently_created)


# ---------------------------------------------------------------------------
# Test 6: an asset matching the predicate with no resolvable creation time
# lands in `unknown_age`, never in `stuck`.
# ---------------------------------------------------------------------------

def test_unresolvable_creation_time_lands_in_unknown_age_never_stuck():
    assets = _load_placeholder_assets()

    result = find_placeholders(assets, now=NOW)

    assert 'asset-fake-unknown-age-001' in _ids(result.unknown_age)
    assert 'asset-fake-unknown-age-001' not in _ids(result.stuck)


def test_unresolvable_creation_time_still_excludes_unparseable_string():
    # _creation_instant must never raise on a garbage created_at value --
    # the same "fails toward not deleting" rule as an absent value.
    garbage = Asset.model_construct(
        id='garbage-created-at', uploaded_at=None, file_name=None, md5_hash=None,
        created_at='not-a-real-timestamp',
    )

    result = find_placeholders([garbage], now=NOW)

    assert 'garbage-created-at' in _ids(result.unknown_age)
    assert result.stuck == []


# ---------------------------------------------------------------------------
# Plan 11-06, Task 1: `unknown_age_policy` opt-in, default provably unchanged.
# ---------------------------------------------------------------------------

def test_unknown_age_policy_default_still_parks_unresolvable_row_in_unknown_age():
    # No unknown_age_policy passed at all -- must be byte-for-byte identical
    # to the pre-11-06 behaviour: unresolvable -> unknown_age, never stuck.
    assets = _load_placeholder_assets()

    result = find_placeholders(assets, now=NOW)

    assert 'asset-fake-unknown-age-001' in _ids(result.unknown_age)
    assert 'asset-fake-unknown-age-001' not in _ids(result.stuck)


def test_unknown_age_policy_stuck_promotes_unresolvable_row_to_stuck():
    assets = _load_placeholder_assets()

    result = find_placeholders(assets, now=NOW, unknown_age_policy='stuck')

    assert 'asset-fake-unknown-age-001' in _ids(result.stuck)
    assert 'asset-fake-unknown-age-001' not in _ids(result.unknown_age)


def test_unknown_age_policy_stuck_does_not_promote_a_resolvable_too_young_row():
    # The opt-in governs only the UNRESOLVABLE case. A row with a resolvable
    # but too-young instant must still land in recently_created regardless
    # of unknown_age_policy.
    one_hour_ago = format_dt_to_aura(NOW - timedelta(hours=1))
    assets = _load_placeholder_assets(recent_created_at=one_hour_ago)

    result = find_placeholders(assets, now=NOW, unknown_age_policy='stuck')

    assert 'asset-fake-recent-001' in _ids(result.recently_created)
    assert 'asset-fake-recent-001' not in _ids(result.stuck)
    # The unresolvable row in this same fixture IS promoted, proving the
    # opt-in is active for this call -- it just doesn't reach the young row.
    assert 'asset-fake-unknown-age-001' in _ids(result.stuck)


def test_unknown_age_policy_stuck_still_excludes_video_shaped_asset():
    # The three-way-null predicate runs before unknown_age_policy is ever
    # consulted -- a video (hashless but named and uploaded) must stay
    # excluded from every bucket under the opt-in too.
    assets = _load_placeholder_assets()

    result = find_placeholders(assets, now=NOW, unknown_age_policy='stuck')

    every_bucket = _ids(result.stuck) | _ids(result.recently_created) | _ids(result.unknown_age)
    assert 'asset-fake-video-001' not in every_bucket


def test_unknown_age_policy_rejects_unrecognized_value():
    assets = _load_placeholder_assets()

    with pytest.raises(ValueError, match='unknown_age_policy'):
        find_placeholders(assets, now=NOW, unknown_age_policy='delete-immediately')


# ---------------------------------------------------------------------------
# Test 7: find_placeholders([]) returns a well-defined, non-crashing result.
# ---------------------------------------------------------------------------

def test_find_placeholders_empty_list_returns_well_defined_empty_result():
    result = find_placeholders([])

    assert isinstance(result, ReconcileResult)
    assert result.stuck == []
    assert result.recently_created == []
    assert result.unknown_age == []
    assert result.removed == []
    assert result.failed == []
    assert result.total_scanned == 0
    assert result.placeholder_count == 0


# ---------------------------------------------------------------------------
# find_placeholders performs no network call: exercised here with plain
# Asset model objects and no offline_aura transport anywhere in this module.
# ---------------------------------------------------------------------------

def test_find_placeholders_performs_no_network_call():
    assets = _load_placeholder_assets()

    result = find_placeholders(assets, now=NOW)

    assert result.total_scanned == len(assets)


def test_placeholder_count_sums_all_three_buckets():
    assets = _load_placeholder_assets()

    result = find_placeholders(assets, now=NOW)

    assert result.placeholder_count == (
        len(result.stuck) + len(result.recently_created) + len(result.unknown_age)
    )
    # Default load uses _OLD_FILL for the recent-created row's token, so it
    # classifies as stuck too: 3 stuck (stuck-001, stuck-002, recent-001) + 0
    # recently_created + 1 unknown_age (unknown-age-001) = 4.
    assert result.placeholder_count == 4


# ===========================================================================
# Task 3: apply_reconciliation -- the bounded, gated removal path
# ===========================================================================

class _FakeBudget:
    """Records `acquire`/`save`/`reconcile_tripped` calls in order, without
    any real token-bucket math -- mirrors
    `tests/test_execute_plan_budget_geo.py`'s `_FakeBudget`. Asserts call
    sequencing/costing against the budget's public surface only."""

    def __init__(self):
        self.acquire_calls: list = []
        self.save_calls = 0
        self.reconcile_calls: list = []

    def acquire(self, n, *, wait, max_wait, now, sleep, on_wait=None):
        self.acquire_calls.append(n)

    def save(self):
        self.save_calls += 1

    def reconcile_tripped(self, now):
        self.reconcile_calls.append(now)


def _stuck(*ids) -> ReconcileResult:
    return ReconcileResult(stuck=[Asset.model_construct(id=i) for i in ids])


# ---------------------------------------------------------------------------
# Test 15: mechanism='remove' against a mocked 200 records every candidate
# in `removed` and issues exactly one remove_asset request per chunk.
# ---------------------------------------------------------------------------

def test_apply_reconciliation_remove_records_removed_and_issues_one_request_per_chunk():
    result = _stuck('asset-a', 'asset-b')
    aura = offline_aura(overrides={REMOVE_ASSET_PATH: httpx.Response(200, json={'number_failed': 0})})

    returned = apply_reconciliation(result, aura, FRAME_ID, mechanism='remove', sleep=lambda *_: None)

    assert returned is result
    assert sorted(result.removed) == ['asset-a', 'asset-b']
    assert result.failed == []
    remove_calls = [
        r for r in aura._client.history
        if r.request.method == 'POST' and r.request.url.path == REMOVE_ASSET_PATH
    ]
    assert len(remove_calls) == 1


# ---------------------------------------------------------------------------
# Test 16: a mocked 404 populates `failed` with the error string, does not
# raise.
# ---------------------------------------------------------------------------

def test_apply_reconciliation_remove_404_populates_failed_without_raising():
    result = _stuck('asset-a')
    aura = offline_aura(overrides={REMOVE_ASSET_PATH: httpx.Response(404, json={'error': 'not_found'})})

    apply_reconciliation(result, aura, FRAME_ID, mechanism='remove', sleep=lambda *_: None)

    assert result.removed == []
    assert len(result.failed) == 1
    failed_id, failed_reason = result.failed[0]
    assert failed_id == 'asset-a'
    assert failed_reason


# ---------------------------------------------------------------------------
# Test 17: apply_reconciliation is passed only `stuck` rows -- the request
# payload carries only the stuck ids even when other buckets are populated.
# ---------------------------------------------------------------------------

def test_apply_reconciliation_only_acts_on_stuck_bucket():
    result = ReconcileResult(
        stuck=[Asset.model_construct(id='stuck-1')],
        recently_created=[Asset.model_construct(id='recent-1')],
        unknown_age=[Asset.model_construct(id='unknown-1')],
    )
    captured_payloads: list = []

    def _capture(request: httpx.Request) -> httpx.Response:
        captured_payloads.append(json.loads(request.content))
        return httpx.Response(200, json={'number_failed': 0})

    aura = offline_aura(overrides={REMOVE_ASSET_PATH: _capture})

    apply_reconciliation(result, aura, FRAME_ID, mechanism='remove', sleep=lambda *_: None)

    assert len(captured_payloads) == 1
    sent_ids = {a['asset_id'] for a in captured_payloads[0]['assets']}
    assert sent_ids == {'stuck-1'}


# ---------------------------------------------------------------------------
# Test 18: acquires from the budget exactly once per chunk for 'remove'
# (cost 1), and one budget TOKEN per asset for 'hard-delete' (cost
# len(chunk)).
# ---------------------------------------------------------------------------

def test_apply_reconciliation_remove_acquires_once_per_chunk_cost_1():
    result = _stuck('asset-a', 'asset-b')
    budget = _FakeBudget()
    aura = offline_aura(overrides={REMOVE_ASSET_PATH: httpx.Response(200, json={'number_failed': 0})})

    apply_reconciliation(result, aura, FRAME_ID, mechanism='remove', budget=budget, sleep=lambda *_: None)

    assert budget.acquire_calls == [1]
    assert budget.save_calls == 1


def test_apply_reconciliation_hard_delete_acquires_one_token_per_asset():
    result = _stuck('a', 'b')
    budget = _FakeBudget()
    aura = offline_aura(overrides={
        '/v5/assets/a.json': httpx.Response(200, json={}),
        '/v5/assets/b.json': httpx.Response(200, json={}),
    })

    apply_reconciliation(result, aura, FRAME_ID, mechanism='hard-delete', budget=budget, sleep=lambda *_: None)

    # One chunk (both fit under batch_size) -> one acquire() call, but its
    # cost is len(chunk) == 2 -- one token per asset, mirroring
    # pushframe/sync.py's hard_delete costing.
    assert budget.acquire_calls == [2]
    assert sorted(result.removed) == ['a', 'b']


# ---------------------------------------------------------------------------
# Test 20: apply_reconciliation refuses more than
# RECONCILE_PROBE_CANDIDATE_LIMIT candidates in one call unless explicitly
# overridden, returning the refusal as a named ValueError.
# ---------------------------------------------------------------------------

def test_apply_reconciliation_refuses_more_than_candidate_limit():
    result = _stuck(*[f'a-{i}' for i in range(RECONCILE_PROBE_CANDIDATE_LIMIT + 1)])

    with pytest.raises(ValueError, match=str(RECONCILE_PROBE_CANDIDATE_LIMIT)):
        apply_reconciliation(result, None, FRAME_ID)


def test_apply_reconciliation_candidate_limit_override_is_honored():
    over_default = RECONCILE_PROBE_CANDIDATE_LIMIT + 1
    result = _stuck(*[f'a-{i}' for i in range(over_default)])
    aura = offline_aura(overrides={REMOVE_ASSET_PATH: httpx.Response(200, json={'number_failed': 0})})

    # Must not raise when the caller explicitly widens the cap.
    apply_reconciliation(result, aura, FRAME_ID, mechanism='remove', sleep=lambda *_: None,
                          candidate_limit=over_default)

    assert len(result.removed) == over_default


# ---------------------------------------------------------------------------
# apply_reconciliation's source references neither of find_placeholders'
# non-removal-eligible buckets (structural enforcement of D-15).
# ---------------------------------------------------------------------------

def test_apply_reconciliation_source_never_names_the_other_buckets():
    import inspect
    from pushframe import reconcile as reconcile_module

    source = inspect.getsource(reconcile_module.apply_reconciliation)
    assert 'recently_created' not in source
    assert 'unknown_age' not in source

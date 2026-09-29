"""Offline unit tests for `pushframe.ratelimit` -- the standalone,
injectable write-rate-budget (`WriteBudget` token bucket) and geo
pre-flight guard (`check_geo`) introduced in Phase 09
(ANTI-01, ANTI-02, ANTI-07).

Everything here is 100% offline: every `acquire()`/`reconcile_tripped()`
call is given a fixed `now` datetime VALUE (the module never calls
`datetime.utcnow()` internally), `sleep` is always an injected fake (never
real `time.sleep()`), the geo `resolver` is always an injected fake (never
a real network call -- `_default_resolver` is never invoked in this suite),
and all file I/O goes through pytest's `tmp_path` fixture (never a real
`~/.config` path).
"""
import json
from datetime import datetime, timedelta

import httpx
import pytest
from loguru import logger

from pushframe.ratelimit import BudgetExhausted, GeoMismatchError, WriteBudget, check_geo


@pytest.fixture(autouse=True)
def _reset_loguru():
    # Mirrors tests/test_write_throttling.py's autouse fixture -- the module
    # imports loguru.logger (used by the geo fail-open path added in Task 2).
    logger.remove()
    yield
    logger.remove()


class _WaitRecorder:
    def __init__(self):
        self.calls = []

    def __call__(self, remaining):
        self.calls.append(remaining)


class _SleepRecorder:
    def __init__(self):
        self.calls = []

    def __call__(self, seconds):
        self.calls.append(seconds)


T0 = datetime(2026, 7, 9, 12, 0, 0)


# ---------------------------------------------------------------------------
# Refill math (including the clock-skew clamp, Pitfall 2)
# ---------------------------------------------------------------------------

def test_acquire_refills_by_elapsed_minutes_then_consumes(tmp_path):
    budget = WriteBudget(capacity=30, refill_per_min=0.75, path=tmp_path / 'b.json',
                          tokens=0.0, updated_at=T0)

    budget.acquire(2, wait=False, max_wait=9999, now=T0 + timedelta(minutes=10),
                    sleep=lambda *_: None)

    assert budget.tokens == 5.5  # 0.75 * 10 = 7.5, minus 2 consumed
    assert budget.updated_at == T0 + timedelta(minutes=10)


def test_acquire_refill_never_pushes_tokens_above_capacity(tmp_path):
    budget = WriteBudget(capacity=10, refill_per_min=1.0, path=tmp_path / 'b.json',
                          tokens=5.0, updated_at=T0)

    budget.acquire(1, wait=False, max_wait=9999, now=T0 + timedelta(days=1),
                    sleep=lambda *_: None)

    assert budget.tokens == 9.0  # capped at capacity(10), then 1 consumed


def test_acquire_elapsed_clamp_backward_clock_adds_zero_tokens(tmp_path):
    budget = WriteBudget(capacity=30, refill_per_min=0.75, path=tmp_path / 'b.json',
                          tokens=5.0, updated_at=T0)

    budget.acquire(1, wait=False, max_wait=9999, now=T0 - timedelta(minutes=5),
                    sleep=lambda *_: None)

    assert budget.tokens == 4.0  # no refill added (clock moved backward), only consumption


def test_acquire_elapsed_clamp_same_instant_adds_zero_tokens(tmp_path):
    budget = WriteBudget(capacity=30, refill_per_min=0.75, path=tmp_path / 'b.json',
                          tokens=5.0, updated_at=T0)

    budget.acquire(1, wait=False, max_wait=9999, now=T0, sleep=lambda *_: None)

    assert budget.tokens == 4.0


def test_acquire_fresh_bucket_none_updated_at_does_not_crash(tmp_path):
    budget = WriteBudget(capacity=30, refill_per_min=0.75, path=tmp_path / 'b.json',
                          tokens=0.0, updated_at=None)

    budget.acquire(0, wait=False, max_wait=9999, now=T0, sleep=lambda *_: None)

    assert budget.tokens == 0.0  # no refill on this first call, just clock start
    assert budget.updated_at == T0


# ---------------------------------------------------------------------------
# acquire: consume / wait / stop
# ---------------------------------------------------------------------------

def test_acquire_consumes_and_returns_none_when_enough_tokens(tmp_path):
    budget = WriteBudget(capacity=30, refill_per_min=0.75, path=tmp_path / 'b.json',
                          tokens=10.0, updated_at=T0)

    result = budget.acquire(3, wait=False, max_wait=9999, now=T0, sleep=lambda *_: None)

    assert result is None
    assert budget.tokens == 7.0


def test_acquire_wait_mode_sleeps_in_1s_steps_calls_on_wait_then_consumes(tmp_path):
    # refill_per_min=60 keeps wait_seconds small and exact for a fast test:
    # need 3 tokens from 0 -> wait_seconds = 3/60*60 = 3.0
    budget = WriteBudget(capacity=30, refill_per_min=60.0, path=tmp_path / 'b.json',
                          tokens=0.0, updated_at=T0)
    sleep = _SleepRecorder()
    waits = _WaitRecorder()

    budget.acquire(3, wait=True, max_wait=10, now=T0, sleep=sleep, on_wait=waits)

    assert sleep.calls == [1.0, 1.0, 1.0]
    assert waits.calls == [3.0, 2.0, 1.0]
    # Waiting accrues exactly the 3-token deficit -> tokens == 3, then -3 -> 0.
    assert budget.tokens == 0.0


def test_acquire_wait_mode_works_without_on_wait_callback(tmp_path):
    budget = WriteBudget(capacity=30, refill_per_min=60.0, path=tmp_path / 'b.json',
                          tokens=0.0, updated_at=T0)
    sleep = _SleepRecorder()

    budget.acquire(3, wait=True, max_wait=10, now=T0, sleep=sleep)  # no on_wait kwarg

    assert sleep.calls == [1.0, 1.0, 1.0]
    assert budget.tokens == 0.0


def test_acquire_stop_mode_raises_budget_exhausted_when_wait_false(tmp_path):
    budget = WriteBudget(capacity=30, refill_per_min=60.0, path=tmp_path / 'b.json',
                          tokens=0.0, updated_at=T0)

    with pytest.raises(BudgetExhausted) as exc_info:
        budget.acquire(3, wait=False, max_wait=10, now=T0, sleep=lambda *_: None)

    assert exc_info.value.wait_seconds == 3.0


def test_acquire_stop_mode_raises_when_wait_seconds_exceeds_max_wait(tmp_path):
    budget = WriteBudget(capacity=30, refill_per_min=60.0, path=tmp_path / 'b.json',
                          tokens=0.0, updated_at=T0)

    with pytest.raises(BudgetExhausted) as exc_info:
        budget.acquire(3, wait=True, max_wait=1.0, now=T0, sleep=lambda *_: None)

    assert exc_info.value.wait_seconds == 3.0


# ---------------------------------------------------------------------------
# reconcile_tripped
# ---------------------------------------------------------------------------

def test_reconcile_tripped_zeroes_tokens_and_sets_updated_at(tmp_path):
    budget = WriteBudget(capacity=30, refill_per_min=0.75, path=tmp_path / 'b.json',
                          tokens=20.0, updated_at=T0)

    budget.reconcile_tripped(T0 + timedelta(minutes=5))

    assert budget.tokens == 0
    assert budget.updated_at == T0 + timedelta(minutes=5)


# ---------------------------------------------------------------------------
# save() / load() persistence round-trip
# ---------------------------------------------------------------------------

def test_save_then_load_round_trips_tokens_and_updated_at(tmp_path):
    path = tmp_path / 'state' / 'budget.json'  # parent dir does not exist yet
    budget = WriteBudget(capacity=30, refill_per_min=0.75, path=path,
                          tokens=12.5, updated_at=T0)

    budget.save()
    loaded = WriteBudget.load(path, capacity=30, refill_per_min=0.75)

    assert loaded.tokens == 12.5
    assert loaded.updated_at == T0
    assert loaded.path == path


def test_load_of_missing_path_returns_fresh_bucket_without_raising(tmp_path):
    path = tmp_path / 'does-not-exist.json'

    loaded = WriteBudget.load(path, capacity=30, refill_per_min=0.75)

    assert loaded.tokens == 0.0
    assert loaded.updated_at is None
    assert loaded.capacity == 30
    assert loaded.refill_per_min == 0.75
    assert loaded.path == path


def test_save_writes_no_credential_material_only_tokens_and_updated_at(tmp_path):
    path = tmp_path / 'budget.json'
    budget = WriteBudget(capacity=30, refill_per_min=0.75, path=path,
                          tokens=5.0, updated_at=T0)

    budget.save()

    data = json.loads(path.read_text())
    assert set(data.keys()) == {'tokens', 'updated_at'}
    body = path.read_text()
    assert 'password' not in body.lower()
    assert '@' not in body  # no email ever ends up in the body


# ---------------------------------------------------------------------------
# check_geo
# ---------------------------------------------------------------------------

def test_check_geo_skips_and_does_not_call_resolver_when_expected_country_falsy():
    called = []

    def resolver():
        called.append(1)
        return 'FR'

    assert check_geo('', resolver=resolver) is None
    assert check_geo(None, resolver=resolver) is None
    assert called == []


def test_check_geo_returns_none_on_case_insensitive_match():
    assert check_geo('FR', resolver=lambda: 'fr') is None
    assert check_geo('fr', resolver=lambda: 'FR') is None


def test_check_geo_raises_geo_mismatch_error_on_country_mismatch():
    with pytest.raises(GeoMismatchError) as exc_info:
        check_geo('FR', resolver=lambda: 'BE')

    assert exc_info.value.found == 'BE'
    assert exc_info.value.expected == 'FR'


def test_check_geo_fails_open_by_default_when_resolver_raises():
    def resolver():
        raise httpx.TimeoutException('timeout')

    assert check_geo('FR', resolver=resolver) is None


def test_check_geo_fails_closed_when_fail_open_false():
    def resolver():
        raise httpx.TimeoutException('timeout')

    with pytest.raises(httpx.TimeoutException):
        check_geo('FR', resolver=resolver, fail_open=False)

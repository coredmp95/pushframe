"""Standalone, injectable write-rate-budget + geo pre-flight guard module
(Phase 09: proactive-write-rate-limiter-geo-guard).

`WriteBudget` is a client-side token bucket that makes the Pushd anti-abuse
write-lockout structurally hard to hit: callers must `acquire()` tokens
before issuing a batch of write network calls, either waiting for enough
tokens to refill or stopping cleanly before ever making the call.

`check_geo` is a pre-flight guard comparing the caller's current exit-IP
country against the account's expected country -- root-cause mitigation for
the VPN-geo-mismatch write-lockout this phase's research identified.

This module touches no existing source and has no side effects at import
time -- it never calls `datetime.utcnow()`, `time.sleep()`, or `httpx`
directly during any *tested* path. `now`, `sleep`, and `resolver` are all
injected by the caller (mirroring the `sleep=time.sleep` seam already used
by `pushframe.sync.execute_plan`), so this module is 100% offline-testable.

`WriteBudget.save()`/`load()` persist ONLY `tokens` + `updated_at` to a
per-account JSON state file -- never the email, password, or any auth
token (see the phase's threat register, T-09-02).
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import httpx
from loguru import logger


class BudgetExhausted(Exception):
    """Raised by `WriteBudget.acquire()` in stop mode (`wait=False`, or a
    computed `wait_seconds` exceeding `max_wait`) instead of ever making the
    gated write call. Carries `wait_seconds` so the CLI (Plan 09-02) can
    print how long a retry would need to wait."""

    def __init__(self, wait_seconds: float):
        self.wait_seconds = wait_seconds
        super().__init__(
            f'Write budget exhausted; would need to wait {wait_seconds:.1f}s '
            'for enough tokens to refill.'
        )


class GeoMismatchError(Exception):
    """Raised by `check_geo()` when the resolved exit-IP country does not
    match the account's expected country. Carries `found` + `expected` so
    the CLI (Plan 09-02) can print an actionable "switch your VPN" message."""

    def __init__(self, found: str, expected: str):
        self.found = found
        self.expected = expected
        super().__init__(
            f'VPN/exit IP in {found}, account expects {expected} -- '
            'switch your VPN and retry.'
        )


@dataclass
class WriteBudget:
    """A per-account client-side token bucket gating batches of Pushd write
    network calls. Mirrors the `ExecutionResult`/`SyncPlan` plain-dataclass
    style used in `pushframe/sync.py` (pydantic is reserved for API DTOs).

    `path` is a constructor field (never hardcoded internally) so tests
    always pass a pytest `tmp_path` value and production wiring (Plan 09-02)
    computes the real `~/.config/pushframe/...` path at the CLI boundary.
    """

    capacity: float
    refill_per_min: float
    path: Path
    tokens: float = 0.0
    updated_at: datetime | None = None

    def acquire(self, n, *, wait, max_wait, now, sleep, on_wait=None) -> None:
        """Refill by elapsed minutes since `updated_at` (clamped to >=0 per
        the clock-skew/None-updated_at Pitfall 2 fix), cap at `capacity`,
        then either consume `n` tokens immediately, wait for them (sleeping
        in 1s steps via the injected `sleep`, surfacing `on_wait(remaining)`
        each second -- mirrors `execute_plan.interchunk_pause()`), or raise
        `BudgetExhausted` if `wait=False` or the wait would exceed `max_wait`.

        `now` is a VALUE supplied by the caller, never read internally --
        this module never calls `datetime.utcnow()`.
        """
        elapsed_minutes = max(0.0, (now - (self.updated_at or now)).total_seconds() / 60.0)
        self.tokens = min(self.capacity, self.tokens + self.refill_per_min * elapsed_minutes)
        self.updated_at = now

        if self.tokens >= n:
            self.tokens -= n
            return

        # WR-03: a non-positive refill rate (e.g. AURA_WRITE_BUDGET_REFILL_PER_MIN=0
        # to "pause" writes) can never satisfy the deficit -- the wait would be
        # infinite -- and dividing by it below would raise ZeroDivisionError.
        # Treat it as an immediate, clean stop instead.
        if self.refill_per_min <= 0:
            raise BudgetExhausted(float('inf'))

        wait_seconds = (n - self.tokens) / self.refill_per_min * 60.0
        if not wait or wait_seconds > max_wait:
            raise BudgetExhausted(wait_seconds)

        remaining = wait_seconds
        while remaining > 0:
            if on_wait:
                on_wait(remaining)
            step = 1.0 if remaining >= 1.0 else remaining
            sleep(step)
            remaining -= step

        # Waiting `wait_seconds` accrues exactly the deficit (`n - tokens`)
        # tokens -- reaching `n`, NOT `capacity` (wait_seconds was derived so
        # the deficit closes exactly). Resetting to `capacity` here would
        # over-grant `capacity - n` tokens on every wait and defeat the
        # anti-burst rate limit. Advance `updated_at` by the waited interval
        # so the next acquire() does not re-count it as fresh elapsed time.
        self.tokens = min(self.capacity, self.tokens + self.refill_per_min * wait_seconds / 60.0)
        self.updated_at = self.updated_at + timedelta(seconds=wait_seconds)
        self.tokens -= n

    def reconcile_tripped(self, now) -> None:
        """Force the bucket to empty (tokens=0, updated_at=now) after a real
        anti-abuse trip (`RateLimitError`/`ConsecutiveWriteFailureError`) so
        the next run's proactive budget reflects reality even though the
        server-side trip wasn't caused by this bucket running dry."""
        self.tokens = 0
        self.updated_at = now

    def save(self) -> None:
        """Write `tokens` + `updated_at` (isoformat, or null) as JSON to
        `self.path`, creating parent directories as needed. No path
        argument -- always writes to the field set at construction, so
        callers can call a bare `budget.save()`. Persists no credential
        material (T-09-02).

        WR-02: the write is atomic (temp file in the same dir + `os.replace`)
        so an interrupted write (Ctrl-C, disk full, crash mid-write) can never
        leave a truncated JSON file that would brick every subsequent run."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps({
            'tokens': self.tokens,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
        })
        tmp_path = self.path.with_name(self.path.name + '.tmp')
        tmp_path.write_text(payload)
        os.replace(tmp_path, self.path)

    @classmethod
    def load(cls, path: Path, *, capacity: float, refill_per_min: float) -> 'WriteBudget':
        """Reconstruct a `WriteBudget` from `path`. If `path` does not
        exist, returns a fresh bucket (tokens=0, updated_at=None) without
        raising -- the common case for a first-ever run.

        WR-02: a corrupt/truncated state file (bad JSON, missing keys, or an
        unparseable `updated_at`) also falls back to a fresh bucket with a
        warning rather than raising -- a single bad file must never brick every
        subsequent `push` / `sync --apply`."""
        if not path.exists():
            return cls(capacity=capacity, refill_per_min=refill_per_min, path=path)
        try:
            data = json.loads(path.read_text())
            tokens = data['tokens']
            updated_at = datetime.fromisoformat(data['updated_at']) if data['updated_at'] else None
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            logger.warning(f'Corrupt write-budget state at {path}; starting fresh.')
            return cls(capacity=capacity, refill_per_min=refill_per_min, path=path)
        return cls(
            capacity=capacity, refill_per_min=refill_per_min, path=path,
            tokens=tokens,
            updated_at=updated_at,
        )


def _default_resolver() -> str:
    """Production default resolver for `check_geo`: a bare, short-timeout
    `httpx.get` to ipinfo.io's unauthenticated legacy endpoint. Deliberately
    NOT routed through `pushframe.client.Client` -- that abstraction is
    Pushd-specific (base URL, headers, 429/475 classification) and would be
    misused here. No retry/backoff (YAGNI) -- `check_geo`'s fail-open
    default is the correct mitigation for a resolver hiccup.

    Never invoked by this module's own test suite (would violate the
    100%-offline test requirement) -- tests always inject a fake `resolver`.
    """
    response = httpx.get('https://ipinfo.io/json', timeout=4.0)
    response.raise_for_status()
    return response.json()['country']


def check_geo(expected_country, *, resolver=_default_resolver, fail_open: bool = True):
    """Pre-flight guard comparing the caller's current exit-IP country
    (via `resolver()`) against `expected_country`. Raises `GeoMismatchError`
    on a mismatch; returns None (no-op) on a match or when `expected_country`
    is falsy (feature disabled/unconfigured -- resolver is not even called).

    Fails OPEN by default (`fail_open=True`): a resolver exception (network
    error, ipinfo.io outage/throttle) logs a warning and returns None rather
    than blocking a legitimate write -- mirroring this codebase's existing
    "best-effort/observational, never gates the primary operation" precedent
    (`execute_plan`'s SQS poll, `pushframe/sync.py:460-461`). Pass
    `fail_open=False` to fail CLOSED instead (the resolver's exception
    propagates).
    """
    if not expected_country:
        return None
    try:
        found = resolver()
    except Exception as e:
        if fail_open:
            logger.warning(f'Geo pre-flight resolver failed ({e}); proceeding (fail-open).')
            return None
        raise
    if found.upper() != expected_country.upper():
        raise GeoMismatchError(found, expected_country)
    return None

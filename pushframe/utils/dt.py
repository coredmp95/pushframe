from datetime import datetime, timezone

AURA_DT_FORMAT = '%Y-%m-%dT%H:%M:%S.%fZ'


def parse_aura_dt(aura_dt_str: str):
    return datetime.strptime(aura_dt_str, AURA_DT_FORMAT)


def get_utc_now():
    """Naive UTC now -- the exact value `datetime.utcnow()` used to return.

    Deliberately NAIVE, not `datetime.now(timezone.utc)`: every consumer
    subtracts this from naive datetimes or stores it next to them --
    `find_placeholders` diffs it against `parse_aura_dt` results (strptime
    is naive) and `WriteBudget.acquire` does `(now - updated_at)` arithmetic
    on a value persisted via isoformat round-trip. One aware datetime in
    that pipeline raises `TypeError: can't subtract offset-naive and
    offset-aware datetimes`. `datetime.now(timezone.utc).replace(tzinfo=None)`
    is the deprecation-free equivalent Python recommends for the old
    `utcnow()` semantics (no wall-clock read, immune to NTP/adjtime jumps).
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)


def format_dt_to_aura(dt: datetime):
    return dt.strftime(AURA_DT_FORMAT)

"""Regression tests for the debug session s3-expiredtoken-long-run
(venus 2026-10-21, pushframe 5.1.38).

A scheduled `google-sync "Nous" --apply` (2555 items, paced by the
WriteBudget) confirmed 1835 items in ~60 min of apply, then aborted through
the lockout breaker: `Aborted after 5 consecutive write failures -- ...
Last error: An error occurred (ExpiredToken) when calling the PutObject
operation: The provided token has expired.` + the "wait 60+ min" lockout
advice. The Cognito temporary credentials (fetched ONCE by `AWSClient.auth`
and baked as static keys into the boto3 clients) had simply expired; nothing
tracked `Credentials.Expiration` or refreshed them, and the error seam
classified the expiry as a systemic write failure.

The contract pinned here:
  * `AWSClient` tracks the credentials' `Expiration` and refreshes them
    (re-running the Cognito auth and rebuilding the boto3 client) BEFORE an
    S3/SQS call once they are within `CREDENTIAL_REFRESH_MARGIN` of expiry;
  * `classify_write_failure` gives expired AWS credentials their own class
    (`FAILURE_CREDENTIALS_EXPIRED`); at the prep call site `execute_plan`
    refreshes and retries the item ONCE and never feeds the lockout breaker;
  * an expiry that survives the refresh (or a refresh that fails) aborts
    with a typed `AwsCredentialsError` that names the real cause -- never
    the account-lockout wording, never a budget trip reconcile;
  * the best-effort SQS frame-ack poll never gates a chunk (an expiry there
    refreshes the SQS credentials, any poll error is logged and ignored);
  * every NON-expiry S3 failure stays systemic and still trips the breaker.

Offline only: boto3.client is monkeypatched with fakes, execute_plan runs
against `offline_aura` (MockTransport) + duck-typed S3/SQS fakes.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import boto3
import pytest
from botocore.exceptions import ClientError
from loguru import logger
from PIL import Image

from pushframe.aws.s3client import get_md5
from pushframe.sync import (
    ConsecutiveWriteFailureError,
    MAX_CONSECUTIVE_WRITE_FAILURES,
    SyncPlan,
    execute_plan,
)
from tests.offline import offline_aura
from tests.test_execute_plan import (
    FRAME_ID,
    _default_overrides,
    _FakeS3Client,
    _FakeSQSClient,
    _install_ack_all_batch_update,
    _install_select_asset_recorder,
)


@pytest.fixture(autouse=True)
def _reset_loguru():
    logger.remove()
    yield
    logger.remove()


T0 = datetime(2026, 10, 21, 0, 14, 3, tzinfo=timezone.utc)
LIFETIME = timedelta(hours=1)


def _client_error(code: str, operation: str = 'PutObject',
                  message: str = 'The provided token has expired.') -> ClientError:
    return ClientError({'Error': {'Code': code, 'Message': message}}, operation)


# --- 1. AWSClient: Expiration tracking + proactive refresh -----------------

class _Clock:
    def __init__(self, now: datetime):
        self.now = now

    def __call__(self) -> datetime:
        return self.now


class _FakeCognito:
    """Cognito identity fake: every get_credentials_for_identity mints a new
    numbered key set expiring `LIFETIME` after the injected clock's now."""

    def __init__(self, clock: _Clock, *, expiration=True, naive=False):
        self.clock = clock
        self.expiration = expiration
        self.naive = naive
        self.get_id_calls = 0
        self.credential_calls = 0

    def get_id(self, IdentityPoolId):
        self.get_id_calls += 1
        return {'IdentityId': f'us-east-1:identity-{self.get_id_calls}'}

    def get_credentials_for_identity(self, IdentityId):
        self.credential_calls += 1
        n = self.credential_calls
        creds = {'AccessKeyId': f'AK{n}', 'SecretKey': f'SK{n}', 'SessionToken': f'ST{n}'}
        if self.expiration:
            exp = self.clock() + LIFETIME
            creds['Expiration'] = exp.replace(tzinfo=None) if self.naive else exp
        return {'IdentityId': IdentityId, 'Credentials': creds}


class _FakeServiceClient:
    """Stands in for a boto3 s3/sqs client; remembers the session token it
    was built with so a test can prove which credentials signed a call."""

    def __init__(self, service: str, kwargs: dict):
        self.service = service
        self.session_token = kwargs.get('aws_session_token')
        self.calls: list = []

    def put_object(self, **kwargs):
        self.calls.append(('put_object', self.session_token))

    def head_object(self, **kwargs):
        self.calls.append(('head_object', self.session_token))
        return {}

    def get_queue_url(self, **kwargs):
        self.calls.append(('get_queue_url', self.session_token))
        return {'QueueUrl': 'https://sqs.fake/q'}

    def receive_message(self, **kwargs):
        self.calls.append(('receive_message', self.session_token))
        return {'Messages': []}


@pytest.fixture
def fake_boto(monkeypatch):
    clock = _Clock(T0)
    state = {'cognito': _FakeCognito(clock), 'built': []}

    def _factory(service, **kwargs):
        if service == 'cognito-identity':
            return state['cognito']
        built = _FakeServiceClient(service, kwargs)
        state['built'].append(built)
        return built

    monkeypatch.setattr(boto3, 'client', _factory)
    state['clock'] = clock
    return state


def _signed_tokens(state, service: str) -> list:
    return [token for c in state['built'] if c.service == service for (_, token) in c.calls]


def test_credentials_expiration_is_tracked(fake_boto):
    from pushframe.aws.s3client import S3Client

    s3 = S3Client(pool_id='us-east-1:pool', clock=fake_boto['clock'])

    assert s3.credentials_expiration == T0 + LIFETIME


def test_fresh_credentials_are_not_refreshed(fake_boto):
    from pushframe.aws.s3client import S3Client

    s3 = S3Client(pool_id='us-east-1:pool', clock=fake_boto['clock'])
    fake_boto['clock'].now = T0 + timedelta(minutes=30)
    s3.upload_file(b'bytes', '.jpg')

    assert fake_boto['cognito'].credential_calls == 1
    assert _signed_tokens(fake_boto, 's3') == ['ST1']


# Boundary neighbours around the refresh margin: one second before it opens
# (still fresh), exactly at it, and already past the expiration (venus).
@pytest.mark.parametrize('offset, refreshed', [
    ('margin-1s', False),
    ('margin', True),
    ('expired+1s', True),
])
def test_s3_upload_refreshes_proactively_within_the_margin(fake_boto, offset, refreshed):
    from pushframe.aws.awsclient import CREDENTIAL_REFRESH_MARGIN
    from pushframe.aws.s3client import S3Client

    s3 = S3Client(pool_id='us-east-1:pool', clock=fake_boto['clock'])
    expiration = T0 + LIFETIME
    fake_boto['clock'].now = {
        'margin-1s': expiration - CREDENTIAL_REFRESH_MARGIN - timedelta(seconds=1),
        'margin': expiration - CREDENTIAL_REFRESH_MARGIN,
        'expired+1s': expiration + timedelta(seconds=1),
    }[offset]

    s3.upload_file(b'bytes', '.jpg')

    assert fake_boto['cognito'].credential_calls == (2 if refreshed else 1)
    # The put was signed with the credentials current AT CALL TIME.
    assert _signed_tokens(fake_boto, 's3') == (['ST2'] if refreshed else ['ST1'])


def test_refresh_reruns_the_cognito_auth_and_rebuilds_the_boto3_client(fake_boto):
    from pushframe.aws.s3client import S3Client

    s3 = S3Client(pool_id='us-east-1:pool', clock=fake_boto['clock'])
    first_client = s3.s3_client
    s3.refresh_credentials()

    # The live-proven sequence (get_id + get_credentials_for_identity), not
    # an unprobed variant.
    assert fake_boto['cognito'].get_id_calls == 2
    assert fake_boto['cognito'].credential_calls == 2
    assert s3.s3_client is not first_client
    assert s3.s3_client.session_token == 'ST2'
    assert s3.credentials_expiration == T0 + LIFETIME  # clock did not move


def test_long_run_keeps_refreshing_every_lifetime(fake_boto):
    # A multi-hour paced apply: every call past each expiry margin rolls the
    # credentials once -- never a refresh per call.
    from pushframe.aws.s3client import S3Client

    s3 = S3Client(pool_id='us-east-1:pool', clock=fake_boto['clock'])
    for minute in range(0, 4 * 60, 10):           # 4 h, one upload / 10 min
        fake_boto['clock'].now = T0 + timedelta(minutes=minute)
        s3.upload_file(b'bytes', '.jpg')

    assert fake_boto['cognito'].credential_calls == 4   # initial + 3 refreshes


def test_sqs_calls_refresh_proactively(fake_boto):
    from pushframe.aws.sqsclient import SQSClient

    sqs = SQSClient(pool_id='us-east-1:pool', clock=fake_boto['clock'])
    queue_url = sqs.get_queue_url('frame-1')
    fake_boto['clock'].now = T0 + LIFETIME + timedelta(minutes=1)
    sqs.receive_message(queue_url, wait_time_seconds=5)

    assert fake_boto['cognito'].credential_calls == 2
    assert _signed_tokens(fake_boto, 'sqs') == ['ST1', 'ST2']


def test_missing_expiration_never_refreshes_proactively(fake_boto):
    from pushframe.aws.s3client import S3Client

    fake_boto['cognito'].expiration = False
    s3 = S3Client(pool_id='us-east-1:pool', clock=fake_boto['clock'])
    fake_boto['clock'].now = T0 + 10 * LIFETIME
    s3.upload_file(b'bytes', '.jpg')

    assert s3.credentials_expiration is None
    assert fake_boto['cognito'].credential_calls == 1


def test_naive_expiration_is_read_as_utc(fake_boto):
    from pushframe.aws.s3client import S3Client

    fake_boto['cognito'].naive = True
    s3 = S3Client(pool_id='us-east-1:pool', clock=fake_boto['clock'])
    fake_boto['clock'].now = T0 + LIFETIME + timedelta(seconds=1)
    s3.upload_file(b'bytes', '.jpg')

    assert s3.credentials_expiration.tzinfo is not None
    assert fake_boto['cognito'].credential_calls == 2


# --- 2. the classification seam --------------------------------------------

@pytest.mark.parametrize('code', [
    'ExpiredToken', 'ExpiredTokenException', 'RequestExpired', 'TokenRefreshRequired'])
def test_expired_aws_credentials_get_their_own_class(code):
    from pushframe.aws.awsclient import is_expired_credentials_error
    from pushframe.sync import FAILURE_CREDENTIALS_EXPIRED, classify_write_failure

    exc = _client_error(code)
    assert is_expired_credentials_error(exc)
    assert classify_write_failure(exc) == FAILURE_CREDENTIALS_EXPIRED


@pytest.mark.parametrize('exc', [
    _client_error('AccessDenied', message='Access Denied'),
    _client_error('InvalidAccessKeyId', message='nope'),
    RuntimeError('An error occurred (ExpiredToken) -- but not a ClientError'),
])
def test_other_aws_failures_stay_systemic(exc):
    from pushframe.aws.awsclient import is_expired_credentials_error
    from pushframe.sync import FAILURE_SYSTEMIC, classify_write_failure

    assert not is_expired_credentials_error(exc)
    assert classify_write_failure(exc) == FAILURE_SYSTEMIC


# --- 3. execute_plan: refresh + retry once, never the breaker --------------

class _ExpiringS3:
    """S3 fake whose credentials 'expire' after `per_credential` uploads:
    the next upload raises ClientError(code) until `refresh_credentials()`
    is called (which, unless `refresh_fixes=False`, starts a fresh lifetime)."""

    def __init__(self, per_credential: int, *, refresh_fixes: bool = True,
                 refresh_error: Exception | None = None, code: str = 'ExpiredToken'):
        self.per_credential = per_credential
        self.refresh_fixes = refresh_fixes
        self.refresh_error = refresh_error
        self.code = code
        self.on_current = 0
        self.uploaded: list = []
        self.expired_raises = 0
        self.refreshes = 0

    def upload_file(self, data, extension):
        if self.on_current >= self.per_credential:
            self.expired_raises += 1
            raise _client_error(self.code)
        self.on_current += 1
        self.uploaded.append(data)
        return f'f{len(self.uploaded)}{extension}', get_md5(data)

    def refresh_credentials(self):
        self.refreshes += 1
        if self.refresh_error is not None:
            raise self.refresh_error
        if self.refresh_fixes:
            self.on_current = 0


class _RecordingBudget:
    def __init__(self):
        self.acquired = 0
        self.tripped = 0
        self.saves = 0

    def acquire(self, n, *, wait, max_wait, now, sleep, on_wait=None):
        self.acquired += n

    def reconcile_tripped(self, now):
        self.tripped += 1

    def save(self):
        self.saves += 1


def _jpegs(tmp_path, n):
    paths = []
    for i in range(n):
        p = tmp_path / f'AF1Qip{i:03d}'
        Image.new('RGB', (4, 4), (i % 255, 0, 0)).save(p, format='JPEG')
        paths.append(p)
    return paths


def _run(plan, aura, *, s3, sqs=None, **kwargs):
    return execute_plan(
        plan, aura, FRAME_ID, s3_client=s3,
        sqs_client=sqs if sqs is not None else _FakeSQSClient(),
        sleep=lambda *_: None, throttle_seconds=0, chunk_delay_seconds=0, **kwargs)


N_ITEMS = 2 * MAX_CONSECUTIVE_WRITE_FAILURES + 2


# Boundary neighbours around the breaker threshold: an expiry every item,
# every MAX-1, every MAX and every MAX+1 uploads, across a chunk of
# 2*MAX+2 items -- before the fix, the 5 expired puts that follow the first
# expiry tripped the breaker in every case.
@pytest.mark.parametrize('per_credential', [
    1,
    MAX_CONSECUTIVE_WRITE_FAILURES - 1,
    MAX_CONSECUTIVE_WRITE_FAILURES,
    MAX_CONSECUTIVE_WRITE_FAILURES + 1,
])
def test_expired_token_mid_run_refreshes_and_retries_the_item(tmp_path, per_credential):
    paths = _jpegs(tmp_path, N_ITEMS)
    aura = offline_aura(overrides=_default_overrides())
    _install_ack_all_batch_update(aura)
    s3 = _ExpiringS3(per_credential)
    budget = _RecordingBudget()

    result = _run(SyncPlan(to_upload=paths), aura, s3=s3, budget=budget)

    expected_refreshes = (N_ITEMS - 1) // per_credential
    assert result.upload_succeeded == N_ITEMS
    assert result.upload_failures == [] and result.upload_skipped == []
    assert s3.refreshes == expected_refreshes == result.credential_refreshes
    assert s3.expired_raises == expected_refreshes   # each item retried ONCE
    assert len(s3.uploaded) == N_ITEMS               # nothing uploaded twice
    assert budget.tripped == 0


def test_venus_shape_expiry_across_chunks_never_aborts(tmp_path):
    # Several chunks (as the budget-paced venus run), credentials rolling
    # over mid-chunk: every item lands, the run never aborts.
    paths = _jpegs(tmp_path, 23)
    aura = offline_aura(overrides=_default_overrides())
    batch_calls = _install_ack_all_batch_update(aura)
    s3 = _ExpiringS3(7)
    progress = []

    result = _run(SyncPlan(to_upload=paths), aura, s3=s3, batch_size=5,
                  progress=lambda *a: progress.append(a))

    assert result.upload_succeeded == 23
    assert result.credential_refreshes == 3
    assert len(batch_calls) == 5
    assert all(ok for (_, _, ok) in progress) and len(progress) == 23


def test_expiry_surviving_the_refresh_aborts_with_a_credentials_error(tmp_path):
    from pushframe.sync import AwsCredentialsError

    paths = _jpegs(tmp_path, N_ITEMS)
    aura = offline_aura(overrides=_default_overrides())
    _install_ack_all_batch_update(aura)
    s3 = _ExpiringS3(3, refresh_fixes=False)
    budget = _RecordingBudget()
    progress = []

    with pytest.raises(AwsCredentialsError) as exc_info:
        _run(SyncPlan(to_upload=paths), aura, s3=s3, budget=budget, batch_size=2,
             progress=lambda *a: progress.append(a))

    err = exc_info.value
    assert not isinstance(err, ConsecutiveWriteFailureError)
    assert 'lockout' not in str(err).lower()
    assert 'ExpiredToken' in str(err)
    assert s3.refreshes == 1                       # one refresh, no storm
    assert s3.expired_raises == 2                  # the item + its ONE retry
    # The first full chunk landed; the aborting item never resolved and
    # nothing was attributed as a write failure.
    assert err.result.upload_succeeded == 2
    assert err.result.upload_failures == []
    assert len(progress) == 2
    # The Pushd account was never refused: no budget trip reconcile.
    assert budget.tripped == 0


def test_failing_refresh_aborts_with_a_credentials_error(tmp_path):
    from pushframe.sync import AwsCredentialsError

    paths = _jpegs(tmp_path, 4)
    aura = offline_aura(overrides=_default_overrides())
    _install_ack_all_batch_update(aura)
    boom = ConnectionError('cognito-identity unreachable')
    s3 = _ExpiringS3(1, refresh_error=boom)

    with pytest.raises(AwsCredentialsError) as exc_info:
        _run(SyncPlan(to_upload=paths), aura, s3=s3)

    assert exc_info.value.__cause__ is boom
    assert 'cognito-identity unreachable' in str(exc_info.value)
    assert 'lockout' not in str(exc_info.value).lower()


def test_s3_client_without_refresh_aborts_with_a_credentials_error(tmp_path):
    from pushframe.sync import AwsCredentialsError

    class _NoRefreshS3:
        calls = 0

        def upload_file(self, data, extension):
            type(self).calls += 1
            raise _client_error('ExpiredToken')

    paths = _jpegs(tmp_path, 3)
    aura = offline_aura(overrides=_default_overrides())

    with pytest.raises(AwsCredentialsError):
        _run(SyncPlan(to_upload=paths), aura, s3=_NoRefreshS3())

    assert _NoRefreshS3.calls == 1                 # no pointless retry


def test_non_expiry_s3_client_errors_still_trip_the_breaker(tmp_path):
    paths = _jpegs(tmp_path, N_ITEMS)
    aura = offline_aura(overrides=_default_overrides())

    class _DeniedS3(_ExpiringS3):
        def upload_file(self, data, extension):
            raise _client_error('AccessDenied', message='Access Denied')

    s3 = _DeniedS3(0)
    with pytest.raises(ConsecutiveWriteFailureError) as exc_info:
        _run(SyncPlan(to_upload=paths), aura, s3=s3)

    assert exc_info.value.count == MAX_CONSECUTIVE_WRITE_FAILURES
    assert s3.refreshes == 0


# --- 4. the SQS poll never gates a chunk -----------------------------------

class _PollFailingSQS(_FakeSQSClient):
    def __init__(self, exc_factory, fail_times: int = 1):
        super().__init__()
        self.exc_factory = exc_factory
        self.fail_times = fail_times
        self.polls = 0
        self.refreshes = 0

    def receive_message(self, queue_url, wait_time_seconds=5):
        self.polls += 1
        if self.polls <= self.fail_times:
            raise self.exc_factory()
        return {}

    def refresh_credentials(self):
        self.refreshes += 1


def test_expired_sqs_poll_refreshes_and_never_gates_the_chunk(tmp_path):
    paths = _jpegs(tmp_path, 12)
    aura = offline_aura(overrides=_default_overrides())
    select_calls = _install_select_asset_recorder(aura)
    batch_calls = _install_ack_all_batch_update(aura)
    sqs = _PollFailingSQS(lambda: _client_error('ExpiredToken', 'ReceiveMessage'))

    result = _run(SyncPlan(to_upload=paths), aura, s3=_FakeS3Client(), sqs=sqs, batch_size=6)

    assert result.upload_succeeded == 12
    assert result.upload_failures == []
    assert len(select_calls) == 2 and len(batch_calls) == 2
    assert sqs.refreshes == 1 and sqs.polls == 2
    assert result.credential_refreshes == 1


@pytest.mark.parametrize('exc_factory', [
    lambda: RuntimeError('SQS endpoint exploded'),
    lambda: _client_error('AWS.SimpleQueueService.NonExistentQueue', 'ReceiveMessage', 'gone'),
])
def test_any_failing_sqs_poll_never_gates_the_chunk(tmp_path, exc_factory):
    # Venus-sized chunk: one poll error used to attribute ALL prepped items
    # (after select_asset was already sent) and feed the breaker 50 times.
    paths = _jpegs(tmp_path, N_ITEMS)
    aura = offline_aura(overrides=_default_overrides())
    batch_calls = _install_ack_all_batch_update(aura)
    sqs = _PollFailingSQS(exc_factory)

    result = _run(SyncPlan(to_upload=paths), aura, s3=_FakeS3Client(), sqs=sqs)

    assert result.upload_succeeded == N_ITEMS
    assert result.upload_failures == []
    assert len(batch_calls) == 1
    assert sqs.refreshes == 0          # only an expiry triggers a refresh


# --- 5. the operator-facing surfaces: honest cause, no lockout advice ------

def _distinct_jpeg(n: int) -> bytes:
    # tests.test_gsync_execute._jpeg's near-identical tiny solid colours can
    # JPEG-encode to identical bytes (deduped by md5); these never collide.
    import io
    buf = io.BytesIO()
    Image.new('RGB', (4, 4), ((n * 37) % 256, (n * 91) % 256, (n * 53) % 256)).save(
        buf, format='JPEG')
    return buf.getvalue()


def _google_bodies(ns):
    from tests.test_gsync_execute import _jpeg
    # Item 1 is the one already on the frame (`_assets_for(1)` = md5 of _jpeg(1)).
    return {n: (_jpeg(n) if n == 1 else _distinct_jpeg(n)) for n in ns}


def test_google_sync_credentials_abort_names_the_cause_not_a_lockout(tmp_path, capsys):
    from tests.test_gsync_execute import (
        _assets_for, _FakeAura, _FakeSQS, _google_session, _GoogleRouter)
    from pushframe.gsync import run_google_sync

    ns = tuple(range(1, 9))
    bodies = _google_bodies(ns)
    assert len({get_md5(b) for b in bodies.values()}) == len(ns)
    router = _GoogleRouter(item_bodies=bodies, listing_ns=ns, metadata_count=8)
    aura = _FakeAura(_assets_for(1))
    rc = run_google_sync("Cadre", "Fabrice", apply=True, yes=True,
                         session=_google_session(router), aura=aura,
                         s3_client=_ExpiringS3(2, refresh_fixes=False),
                         sqs_client=_FakeSQS(),
                         cache_dir=tmp_path / "cache",
                         manifest_path=tmp_path / "manifest.json")
    out = capsys.readouterr().out

    assert rc == 1
    assert 'google-sync failed:' in out              # report subject marker
    assert 'credentials' in out
    assert 'consecutive write failures' not in out
    assert '60+ min' not in out
    assert 'anti-abuse' not in out
    assert 'lockout' not in out.lower()


def test_google_sync_venus_shape_rolls_credentials_and_completes(tmp_path, capsys):
    import json
    from tests.test_gsync_execute import (
        _assets_for, _FakeAura, _FakeSQS, _gid, _google_session, _GoogleRouter)
    from pushframe.gsync import run_google_sync

    ns = tuple(range(1, 9))
    router = _GoogleRouter(item_bodies=_google_bodies(ns), listing_ns=ns, metadata_count=8)
    aura = _FakeAura(_assets_for(1))
    rc = run_google_sync("Cadre", "Fabrice", apply=True, yes=True,
                         session=_google_session(router), aura=aura,
                         s3_client=_ExpiringS3(2), sqs_client=_FakeSQS(),
                         cache_dir=tmp_path / "cache",
                         manifest_path=tmp_path / "manifest.json")
    out = capsys.readouterr().out

    assert rc == 0
    assert 'Applied: 7 uploaded' in out
    assert 'AWS credentials renewed 3 time(s)' in out
    assert 'upload(s) FAILED' not in out
    manifest = json.loads((tmp_path / "manifest.json").read_text())
    assert all(_gid(n) in manifest for n in range(2, 9))


def test_cli_sync_credentials_abort_names_the_cause_not_a_lockout(tmp_path, monkeypatch, capsys):
    from pushframe import cli
    from pushframe.sync import AwsCredentialsError, ExecutionResult
    from tests.test_cli_apply import ASSETS_PATH, _assets_response, _env, _patch_aws_clients

    _env(monkeypatch)
    _patch_aws_clients(monkeypatch)

    def failing_execute_plan(plan, aura, frame_id, **kwargs):
        raise AwsCredentialsError(
            'An error occurred (ExpiredToken) when calling the PutObject operation: '
            'The provided token has expired.', ExecutionResult(upload_succeeded=7),
            refreshed=True)

    monkeypatch.setattr(cli, 'execute_plan', failing_execute_plan)
    (tmp_path / 'new.jpg').write_bytes(b'new-photo-bytes')
    aura = offline_aura(overrides={ASSETS_PATH: _assets_response()})

    rc = cli.run_sync(str(tmp_path), 'Fake', apply=True, yes=True, aura=aura)

    out = capsys.readouterr().out
    assert rc == 1
    assert '7 uploads' in out
    assert 'ExpiredToken' in out
    assert 'lockout' not in out.lower()
    assert 'consecutive write failures' not in out

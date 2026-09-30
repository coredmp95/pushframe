"""Offline tests for `pushframe status` (pushframe.cli.run_status). Calls
run_status() directly (never main()) so load_dotenv() is not invoked and a
filesystem .env cannot interfere.

Unmarked (no @pytest.mark.live) — this is the default pytest suite, runs
with zero network access and no real credentials.
"""
import httpx
import pytest
from loguru import logger

from pushframe.cli import run_status
from tests.offline import offline_aura


@pytest.fixture(autouse=True)
def _reset_loguru(monkeypatch):
    # loguru's `logger` is a process-global singleton and Aura._init_logger()
    # accumulates sinks across constructions; reset around each test so the
    # stderr assertions below are deterministic and a sink bound to a
    # torn-down capsys buffer from a prior test can't fire in a later one.
    # Phase 19 (MOD-04): _init_logger() is now guarded by a process-level
    # flag, so resetting the flag too lets each test's Aura() construction
    # re-register sinks as this file's contract expects.
    import pushframe.aura as aura_module

    logger.remove()
    monkeypatch.setattr(aura_module, '_LOGGER_READY', False)
    yield
    logger.remove()


FRAME_401_TRIP_BODY = httpx.Response(
    401, json={"error": True, "message": "Request Unauthenticated", "logout": True})
FRAME_401_TOKEN_BODY = httpx.Response(
    401, json={"error": True, "message": "invalid session token"})


def test_status_fresh_creds_refused_with_trip_body_means_the_trip(monkeypatch, capsys):
    """Debug session status-crash-401-trip, updated contract: on the env
    path the credentials are ALREADY fresh — a trip-shaped 401 body there
    is the anti-abuse trip on reads: 24h-silence verdict, no re-login
    suggestion, never a traceback."""
    monkeypatch.setenv('AURA_EMAIL', 'you@example.invalid')
    monkeypatch.setenv('AURA_PASSWORD', 'super-secret-pw')

    aura = offline_aura(overrides={'/v5/frames.json': FRAME_401_TRIP_BODY})
    rc = run_status(aura=aura)

    assert rc == 1
    out = capsys.readouterr().out
    assert 'HTTP 401' in out
    assert 'FRESH credentials' in out
    assert '24h' in out
    assert 'Traceback' not in out


def test_status_stale_token_401_non_tty_fails_named_without_refresh(monkeypatch, capsys, tmp_path):
    """Scheduled/no-TTY runs NEVER prompt: the refresh guard raises the
    named SessionExpiredError and status surfaces it with the config
    remedy — exit 1, zero tracebacks."""
    from pushframe.utils import settings
    monkeypatch.setattr(settings, 'CONFIG_PATH', tmp_path / 'config.json')
    from pushframe import config_store
    config_store.update(email='vaulted@example.invalid', auth_token='tok-dead',
                        user_id='u-1')
    monkeypatch.delenv('AURA_EMAIL', raising=False)
    monkeypatch.delenv('AURA_PASSWORD', raising=False)
    monkeypatch.setattr('sys.stdin.isatty', lambda: False)

    aura = offline_aura(overrides={'/v5/frames.json': FRAME_401_TOKEN_BODY})
    rc = run_status(aura=aura)

    assert rc == 1
    out = capsys.readouterr().out
    assert 'refresh' in out and 'pushframe config' in out
    assert 'Traceback' not in out


def test_status_stale_token_401_tty_refreshes_once_and_succeeds(monkeypatch, capsys, tmp_path):
    """The venus story, automated: stored token refused → ONE TTY re-login
    prompt → new token persisted → the SAME run lists the frames. Never a
    second attempt: the login is called exactly once."""
    from pushframe.utils import settings
    monkeypatch.setattr(settings, 'CONFIG_PATH', tmp_path / 'config.json')
    from pushframe import config_store
    config_store.update(email='vaulted@example.invalid', auth_token='tok-dead',
                        user_id='u-1')
    monkeypatch.delenv('AURA_EMAIL', raising=False)
    monkeypatch.delenv('AURA_PASSWORD', raising=False)
    monkeypatch.setattr('sys.stdin.isatty', lambda: True)

    logins = []

    import getpass as gp
    monkeypatch.setattr(gp, 'getpass', lambda *a: 'typed-pw')  # noqa: S105
    monkeypatch.setattr('builtins.input', lambda *a: 'vaulted@example.invalid')

    def fake_wizard_login(email, password):
        logins.append((email, password))
        return {'email': email, 'auth_token': 'fresh-tok',  # noqa: S105
                'user_id': 'u-1', 'frames': []}

    monkeypatch.setattr('pushframe.cli._wizard_login', fake_wizard_login)

    # frames.json: the trip-shaped 401 once (dead token), then the normal
    # fixture listing after the refresh.
    state = {'calls': 0}
    from tests.offline import FIXTURES_DIR
    frames_fixture = (FIXTURES_DIR / 'frames.json').read_text()

    def frames_route(request):
        state['calls'] += 1
        if state['calls'] == 1:
            return FRAME_401_TRIP_BODY
        return httpx.Response(200, content=frames_fixture)

    aura = offline_aura(overrides={'/v5/frames.json': frames_route})

    rc = run_status(aura=aura)

    assert rc == 0
    out = capsys.readouterr().out
    assert 'Fake Frame' in out
    assert logins == [('vaulted@example.invalid', 'typed-pw')]  # noqa: S105 — exactly ONE
    assert state['calls'] == 2  # refused once, re-read once after refresh
    assert config_store.load()['auth_token'] == 'fresh-tok'  # noqa: S105


def test_status_token_shaped_401_names_the_config_remedy(monkeypatch, capsys):
    monkeypatch.setenv('AURA_EMAIL', 'you@example.invalid')
    monkeypatch.setenv('AURA_PASSWORD', 'super-secret-pw')

    aura = offline_aura(overrides={'/v5/frames.json': FRAME_401_TOKEN_BODY})
    rc = run_status(aura=aura)

    assert rc == 1
    out = capsys.readouterr().out
    assert 'HTTP 401' in out and 'pushframe config' in out
    assert 'Traceback' not in out


def test_status_rate_limit_475_named_wait_message(monkeypatch, capsys):
    """A 475 on the frames read is classified by the client layer itself
    (_raise_if_rate_limited): status must surface it as a named WAIT, never
    a traceback."""
    monkeypatch.setenv('AURA_EMAIL', 'you@example.invalid')
    monkeypatch.setenv('AURA_PASSWORD', 'super-secret-pw')

    aura = offline_aura(overrides={'/v5/frames.json': httpx.Response(
        475, text='The Aura API is rate-limiting or has locked out this account.')})
    rc = run_status(aura=aura)

    assert rc == 1
    out = capsys.readouterr().out
    assert 'rate-limited or locked out' in out
    assert 'pushframe config' not in out  # a 475 is a WAIT, not a token fix
    assert 'Traceback' not in out


def test_status_missing_creds_exits_nonzero_no_network(monkeypatch, capsys):
    monkeypatch.delenv('AURA_EMAIL', raising=False)
    monkeypatch.delenv('AURA_PASSWORD', raising=False)

    rc = run_status()

    assert rc == 1
    out = capsys.readouterr().out
    assert 'PUSHFRAME_EMAIL: NOT SET' in out
    assert 'PUSHFRAME_PASSWORD: NOT SET' in out


def test_status_success_lists_frames_and_never_prints_password(monkeypatch, capsys):
    monkeypatch.setenv('AURA_EMAIL', 'you@example.invalid')
    monkeypatch.setenv('AURA_PASSWORD', 'super-secret-pw')

    rc = run_status(aura=offline_aura())

    assert rc == 0
    out = capsys.readouterr().out
    assert 'PUSHFRAME_EMAIL: set' in out
    assert 'PUSHFRAME_PASSWORD: set' in out
    assert 'Logged in as you@example.invalid' in out
    assert '1 frames:' in out
    assert 'Fake Frame' in out
    assert 'frame-fake-0001' in out
    assert 'super-secret-pw' not in out


def test_status_login_failure_exits_nonzero(monkeypatch, capsys):
    monkeypatch.setenv('AURA_EMAIL', 'you@example.invalid')
    monkeypatch.setenv('AURA_PASSWORD', 'super-secret-pw')
    aura = offline_aura(overrides={
        '/v5/login.json': httpx.Response(200, json={'error': 'invalid_credentials', 'message': 'Bad login'})
    })

    rc = run_status(aura=aura)

    assert rc == 1
    out = capsys.readouterr().out
    assert 'Login failed' in out


def test_status_works_from_stored_session_without_env(tmp_path, monkeypatch, capsys):
    """Roadmap §23 criterion 1 (hermetic half): after `pushframe config`
    stored email+token, `status` must work with NO env vars — resuming the
    stored session, never calling login, never printing the token."""
    from pushframe.utils import settings
    monkeypatch.setattr(settings, 'CONFIG_PATH', tmp_path / 'config.json')
    from pushframe import config_store
    config_store.update(email='vaulted@example.invalid', auth_token='tok-123',
                        user_id='user-1')
    for var in ('PUSHFRAME_EMAIL', 'AURA_EMAIL', 'PUSHFRAME_PASSWORD', 'AURA_PASSWORD'):
        monkeypatch.delenv(var, raising=False)

    # A login endpoint hit would 500 and fail the command: proves the stored
    # session path never logs in.
    aura = offline_aura(overrides={
        '/v5/login.json': httpx.Response(500, json={'error': 'login must not be called'})})

    rc = run_status(aura=aura)

    assert rc == 0
    out = capsys.readouterr().out
    assert 'Logged in as vaulted@example.invalid' in out
    assert '1 frames:' in out and 'Fake Frame' in out
    assert 'tok-123' not in out


def test_status_with_corrupt_config_fails_loud_without_env(tmp_path, monkeypatch, capsys):
    from pushframe.utils import settings
    monkeypatch.setattr(settings, 'CONFIG_PATH', tmp_path / 'config.json')
    (tmp_path / 'config.json').write_text('{not json')
    for var in ('PUSHFRAME_EMAIL', 'AURA_EMAIL', 'PUSHFRAME_PASSWORD', 'AURA_PASSWORD'):
        monkeypatch.delenv(var, raising=False)

    rc = run_status()

    assert rc == 1
    assert 'corrupt' in capsys.readouterr().out


def test_status_quiet_by_default_suppresses_verbose_stderr(monkeypatch, capsys):
    monkeypatch.setenv('AURA_EMAIL', 'you@example.invalid')
    monkeypatch.setenv('AURA_PASSWORD', 'super-secret-pw')

    rc = run_status(aura=offline_aura())

    assert rc == 0
    captured = capsys.readouterr()
    assert 'Logged in as you@example.invalid' in captured.out
    assert '1 frames:' in captured.out
    assert 'Fake Frame' in captured.out
    # The two loguru markers Client.get/post write for every HTTP call must
    # not reach stderr by default (this is the RED assertion pre-fix).
    assert 'request to' not in captured.err
    assert 'Response (' not in captured.err


def test_status_debug_flag_restores_verbose_stderr(monkeypatch, capsys):
    monkeypatch.setenv('AURA_EMAIL', 'you@example.invalid')
    monkeypatch.setenv('AURA_PASSWORD', 'super-secret-pw')

    rc = run_status(aura=offline_aura(), debug=True)

    assert rc == 0
    captured = capsys.readouterr()
    # --debug leaves loguru's sinks untouched, so the request-line marker
    # from Client.get/post is present on stderr (opt-in verbosity).
    assert 'request to' in captured.err

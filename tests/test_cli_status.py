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

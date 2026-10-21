"""Run-lock dispatch matrix (quick-261021-c1k): every write verb runs its
handler UNDER the per-account lock; every read-only verb and dry-run never
touches it. Handlers are faked; a raw non-blocking flock probe inside the fake
proves the lock is actually held during the call."""
import fcntl
import json
import os

import pytest

from pushframe import runlock

EMAIL = 'a@b.c'
SALON = {'pid': 4242, 'job': 'SALON-CADRE', 'command': 'google-sync',
         'started_at': '2026-10-21T00:13:00'}


@pytest.fixture
def cfg_path(tmp_path, monkeypatch):
    from pushframe.utils import settings
    path = tmp_path / 'config.json'
    monkeypatch.setattr(settings, 'CONFIG_PATH', path)
    monkeypatch.setenv('PUSHFRAME_EMAIL', EMAIL)
    monkeypatch.delenv('AURA_EMAIL', raising=False)
    monkeypatch.delenv('PUSHFRAME_ACCOUNT_LOCK_MAX_WAIT', raising=False)
    return path


def _probe_held() -> bool:
    path = runlock.lock_path_for(EMAIL)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(fd, fcntl.LOCK_UN)
        return False
    except BlockingIOError:
        return True
    finally:
        os.close(fd)


def _hold_externally():
    path = runlock.lock_path_for(EMAIL)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    fcntl.flock(fd, fcntl.LOCK_EX)
    os.pwrite(fd, json.dumps(SALON).encode(), 0)
    return fd


class Recorder:
    """A fake handler: records whether the lock was held during the call and
    returns a programmable rc."""

    def __init__(self, rc=0):
        self.rc = rc
        self.held = []

    def __call__(self, *a, **k):
        self.held.append(_probe_held())
        return self.rc


def _patch(monkeypatch, verb, rec):
    if verb == 'sync':
        monkeypatch.setattr('pushframe.cli.run_sync', rec)
    elif verb == 'reconcile':
        monkeypatch.setattr('pushframe.cli.run_reconcile', rec)
    elif verb == 'gsync':
        monkeypatch.setattr('pushframe.gsync.run_google_sync', rec)
    else:  # pragma: no cover
        raise AssertionError(verb)


WRITE_CASES = [
    ('push', ['push', 'DIR', '--frame', 'X', '--apply', '--yes']),
    ('sync', ['sync', 'DIR', '--frame', 'X', '--apply', '--yes']),
    ('gsync', ['google-sync', 'A', '--frame', 'F', '--apply', '--yes']),
    ('gsync', ['google-sync', '--pair', 'p', '--apply', '--yes']),
    ('gsync', ['google-sync', '--all', '--apply', '--yes']),
    ('reconcile', ['reconcile', '--frame', 'X', '--remove', '--yes']),
]
FAKE_FOR = {'push': 'sync', 'sync': 'sync', 'gsync': 'gsync',
            'reconcile': 'reconcile'}


@pytest.mark.parametrize('kind,argv', WRITE_CASES,
                         ids=lambda v: v if isinstance(v, str) else ' '.join(v[:2]))
def test_write_verbs_run_under_the_lock(cfg_path, monkeypatch, tmp_path, kind, argv):
    from pushframe import cli
    rec = Recorder()
    _patch(monkeypatch, FAKE_FOR[kind], rec)
    argv = [str(tmp_path) if a == 'DIR' else a for a in argv]
    assert cli.main(argv) == 0
    assert rec.held == [True]
    assert not _probe_held()          # released afterwards


def test_gsync_report_branch_runs_under_the_lock(cfg_path, monkeypatch, capsys):
    from pushframe import cli
    rec = Recorder(rc=1)
    _patch(monkeypatch, 'gsync', rec)
    delivered = []
    monkeypatch.setattr(cli, '_deliver_report',
                        lambda *a, **k: delivered.append(k))
    rc = cli.main(['google-sync', 'A', '--frame', 'F', '--apply', '--yes',
                   '--report', 'INFO'])
    assert rc == 1 and rec.held == [True]
    assert delivered and delivered[0]['rc'] == 1      # the real rc reaches the report


@pytest.mark.parametrize('kind,argv', WRITE_CASES,
                         ids=lambda v: v if isinstance(v, str) else ' '.join(v[:2]))
@pytest.mark.parametrize('rc_value', [0, 1, 2])
def test_exit_codes_propagate_unchanged(cfg_path, monkeypatch, tmp_path, kind,
                                        argv, rc_value):
    from pushframe import cli
    _patch(monkeypatch, FAKE_FOR[kind], Recorder(rc=rc_value))
    argv = [str(tmp_path) if a == 'DIR' else a for a in argv]
    assert cli.main(argv) == rc_value


# --- read-only paths never touch the lock -----------------------------------

READ_CASES = [
    ('status', ['status'], 'pushframe.cli.run_status'),
    ('inspect', ['inspect', '--frame', 'X'], 'pushframe.cli.run_inspect'),
    ('doctor', ['doctor'], 'pushframe.doctor.run_doctor'),
    ('sync-dry', ['sync', 'DIR', '--frame', 'X'], 'pushframe.cli.run_sync'),
    ('push-dry', ['push', 'DIR', '--frame', 'X'], 'pushframe.cli.run_sync'),
    ('gsync-dry', ['google-sync', 'A', '--frame', 'F'],
     'pushframe.gsync.run_google_sync'),
    ('gsync-pair-dry', ['google-sync', '--pair', 'p'],
     'pushframe.gsync.run_google_sync'),
    ('reconcile-dry', ['reconcile', '--frame', 'X'], 'pushframe.cli.run_reconcile'),
]


@pytest.mark.parametrize('name,argv,target', READ_CASES,
                         ids=[c[0] for c in READ_CASES])
def test_read_only_verbs_never_touch_the_lock(cfg_path, monkeypatch, tmp_path,
                                              name, argv, target):
    from pushframe import cli
    calls = []
    monkeypatch.setattr(runlock, 'acquire_account_lock',
                        lambda *a, **k: calls.append((a, k)))
    ran = []
    monkeypatch.setattr(target, lambda *a, **k: ran.append(1) or 0)
    argv = [str(tmp_path) if a == 'DIR' else a for a in argv]
    assert cli.main(argv) == 0
    assert ran == [1] and calls == []


def test_dry_run_creates_no_lock_file(cfg_path, monkeypatch, tmp_path):
    from pushframe import cli
    monkeypatch.setattr('pushframe.cli.run_sync', lambda *a, **k: 0)
    cli.main(['sync', str(tmp_path), '--frame', 'X'])
    assert not runlock.lock_path_for(EMAIL).exists()


# --- contended: named failure, header/footer intact, no traceback -----------

def test_contended_scheduled_sync_fails_named(cfg_path, monkeypatch, tmp_path,
                                              capsys):
    from pushframe import cli
    monkeypatch.setenv('PUSHFRAME_ACCOUNT_LOCK_MAX_WAIT', '0')
    rec = Recorder()
    _patch(monkeypatch, 'sync', rec)
    fd = _hold_externally()
    try:
        rc = cli.main(['sync', str(tmp_path), '--frame', 'X', '--apply',
                       '--yes', '--scheduled', '--report-tag', 'nightly'])
    finally:
        os.close(fd)
    cap = capsys.readouterr()
    assert rc == 1 and rec.held == []
    assert cap.out.splitlines()[0].startswith('=== run ')
    assert ('sync failed: account lock still held by job SALON-CADRE '
            '(pid 4242') in cap.out
    assert '00:13' in cap.out
    assert 'PUSHFRAME_ACCOUNT_LOCK_MAX_WAIT' in cap.out
    assert '=== run end rc=1' in cap.out
    assert 'Traceback' not in cap.out + cap.err


@pytest.mark.parametrize('verb,argv,fake', [
    ('google-sync', ['google-sync', 'A', '--frame', 'F', '--apply', '--yes'],
     'gsync'),
    ('reconcile', ['reconcile', '--frame', 'X', '--remove', '--yes'],
     'reconcile'),
    ('push', ['push', 'DIR', '--frame', 'X', '--apply', '--yes'], 'sync'),
])
def test_contended_other_write_verbs_fail_named(cfg_path, monkeypatch, tmp_path,
                                                capsys, verb, argv, fake):
    from pushframe import cli
    monkeypatch.setenv('PUSHFRAME_ACCOUNT_LOCK_MAX_WAIT', '0')
    rec = Recorder()
    _patch(monkeypatch, fake, rec)
    fd = _hold_externally()
    try:
        rc = cli.main([str(tmp_path) if a == 'DIR' else a for a in argv])
    finally:
        os.close(fd)
    cap = capsys.readouterr()
    assert rc == 1 and rec.held == []
    assert f'{verb} failed: account lock' in cap.out
    assert 'Traceback' not in cap.out + cap.err


def test_junk_cap_falls_back_with_a_note(cfg_path, monkeypatch, tmp_path, capsys):
    from pushframe import cli
    monkeypatch.setenv('PUSHFRAME_ACCOUNT_LOCK_MAX_WAIT', 'soon')
    rec = Recorder()
    _patch(monkeypatch, 'sync', rec)
    rc = cli.main(['sync', str(tmp_path), '--frame', 'X', '--apply', '--yes'])
    cap = capsys.readouterr()
    assert rc == 0 and rec.held == [True]
    assert ('note: PUSHFRAME_ACCOUNT_LOCK_MAX_WAIT is not a number of seconds'
            in cap.out)
    assert 'Traceback' not in cap.out + cap.err


def test_no_identity_skips_the_lock_with_a_stderr_note(cfg_path, monkeypatch,
                                                       tmp_path, capsys):
    from pushframe import cli
    monkeypatch.delenv('PUSHFRAME_EMAIL', raising=False)
    monkeypatch.delenv('AURA_EMAIL', raising=False)
    monkeypatch.setattr(cli, 'load_dotenv', lambda *a, **k: None)
    calls = []
    monkeypatch.setattr(runlock, 'acquire_account_lock',
                        lambda *a, **k: calls.append(1))
    ran = []
    monkeypatch.setattr('pushframe.cli.run_sync',
                        lambda *a, **k: ran.append(1) or 0)
    rc = cli.main(['sync', str(tmp_path), '--frame', 'X', '--apply', '--yes'])
    cap = capsys.readouterr()
    assert rc == 0 and ran == [1] and calls == []
    assert 'account lock: no account identity' in cap.err
    assert 'note:' not in cap.out
